from datetime import UTC, datetime
from typing import ClassVar

import pytest

from content_factory.app.research_pipeline import ResearchDeps
from content_factory.domain.errors import SegmentNotFound
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, ResearchCampaign, Segment
from content_factory.infra.queue import worker as w
from tests.stubs import FakeLimiter, FakeLock, FakeRepos


class CountingDeps:
    def __init__(self) -> None:
        self.collect_calls = 0


class _FakeCampaignsMultiSegment:
    """Campanhas ativas devolvendo Segment fakes para os slugs pedidos."""

    def __init__(self, slugs: list[str], platforms: list[str] | None = None) -> None:
        self._slugs = slugs
        self._platforms = platforms or ["youtube", "tiktok"]
        self.stopped: list[int] = []

    async def active_segments(self) -> list[Segment]:
        return [
            Segment(
                id=i + 1,
                slug=slug,
                name=slug,
                platforms=self._platforms,
                keywords=["k"],
            )
            for i, slug in enumerate(self._slugs)
        ]

    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
        return None

    async def start(self, segment_id: int, ends_at: object) -> ResearchCampaign:
        raise NotImplementedError

    async def stop(self, segment_id: int) -> None:
        self.stopped.append(segment_id)


class _NoopAppDeps:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


async def test_run_collect_job_processes_all_active_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 segmentos ativos → 2 chamadas de collect, 1 commit por segmento."""
    calls: list[str] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(slug)

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: {"platforms": [], "limit": 10})
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": _AppDeps()}
    )
    assert calls == ["velas", "sabonetes"]
    assert out["ok"] is True
    assert set(out["segments"]) == {"velas", "sabonetes"}
    assert committed == ["commit", "commit"]  # 1 commit por segmento


async def test_run_collect_job_noop_when_no_active_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 segmentos ativos → noop ok=True, sem chamar collect."""
    monkeypatch.setattr(w, "_load_settings", lambda: {"platforms": [], "limit": 10})
    campaigns = _FakeCampaignsMultiSegment(slugs=[])
    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": _NoopAppDeps()}
    )
    assert out == {"ok": True, "segments": {}}


async def test_run_collect_job_platforms_fall_back_to_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`COLLECT_PLATFORMS` vazio → usa as plataformas declaradas do segmento."""
    calls: list[list[str]] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(platforms)

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: {"platforms": [], "limit": 5})
    campaigns = _FakeCampaignsMultiSegment(slugs=["candles"], platforms=["youtube", "tiktok"])
    await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": _NoopAppDeps()}
    )
    assert calls == [["youtube", "tiktok"]]  # plataformas do segmento (stub)


def test_cron_hours_parse() -> None:
    assert w.parse_cron_hours("08,20") == {8, 20}
    assert w.parse_cron_hours("") == {8, 20}  # default


def test_workersettings_registers_jobs_and_cron() -> None:
    """Contrato: `functions` = collect/score/research; cron diário nesses jobs.

    Asserts diretos nas classes de settings (o `create_worker` do arq é
    validado de ponta a ponta na subida do container worker).
    """
    settings = w.WorkerSettings
    assert {f.name for f in settings.functions} == {"collect_job", "score_job", "research_job"}
    assert [cj.coroutine for cj in settings.cron_jobs] == [
        w.run_research_job,
        w.run_collect_job,
        w.run_score_job,
    ]
    assert [cj.minute for cj in settings.cron_jobs] == [0, 0, 30]
    assert all(cj.unique for cj in settings.cron_jobs)
    assert settings.on_startup is w.startup
    assert settings.on_job_end is w.job_end
    assert settings.on_shutdown is w.shutdown


def test_workersettings_max_jobs_is_one() -> None:
    """Sessão única compartilhada: 1 job por vez (arq default é 10)."""
    assert w.WorkerSettings.max_jobs == 1


def test_workersettings_timezone_is_sao_paulo() -> None:
    """Cron dispara 08:00/20:00 no horário local do projeto, não em UTC."""
    from zoneinfo import ZoneInfo

    tz = w.WorkerSettings.timezone
    assert isinstance(tz, ZoneInfo)
    assert tz.key == "America/Sao_Paulo"


async def test_startup_failure_then_shutdown_propagates_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """arq chama on_shutdown no finally; falha de startup não vira KeyError."""

    async def boom(_settings: object) -> w.AppDeps:
        raise SegmentNotFound("candles")

    monkeypatch.setattr(w, "create_app_deps", boom)
    ctx: dict[str, object] = {}
    with pytest.raises(SegmentNotFound):
        try:
            await w.startup(ctx)
        finally:
            await w.shutdown(ctx)


async def test_shutdown_closes_app_deps_when_present() -> None:
    closed: list[str] = []

    class FakeAppDeps:
        async def close(self) -> None:
            closed.append("close")

    await w.shutdown({"app_deps": FakeAppDeps()})
    assert closed == ["close"]


class _FakeCampaigns:
    def __init__(self, campaign: ResearchCampaign | None) -> None:
        self._campaign = campaign
        self.stopped: list[int] = []

    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
        return self._campaign

    async def start(self, segment_id: int, ends_at: object) -> ResearchCampaign:
        raise NotImplementedError

    async def stop(self, segment_id: int) -> None:
        self.stopped.append(segment_id)


class _NoopWeb:
    platform: str = "web_suggest"

    async def expand(self, seeds: list[str], depth: int) -> list[object]:
        return []


class _NoopTrends:
    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[object]:
        return []


class _NoopSignals:
    async def upsert_many(self, segment_id: int, signals: object) -> int:
        return 0

    async def top_for_segment(self, segment_id: int, source: object, limit: int) -> list[object]:
        return []


class _NoopTrendObservations:
    async def record_many(self, segment_id: int, points: object) -> int:
        return 0

    async def history_for_keyword(self, segment_id: int, keyword: str, limit: int) -> list[object]:
        return []


def _research_deps() -> ResearchDeps:
    repos = FakeRepos()
    return ResearchDeps(
        web=_NoopWeb(),  # type: ignore[arg-type]
        trends=_NoopTrends(),  # type: ignore[arg-type]
        socials={},
        signals=_NoopSignals(),  # type: ignore[arg-type]
        trend_observations=_NoopTrendObservations(),  # type: ignore[arg-type]
        segments=repos.segments,  # type: ignore[arg-type]
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )


async def test_run_research_job_skips_without_active_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem campanha ativa → ok=True + skipped, sem chamar o ciclo."""
    calls: list[str] = []

    async def fake_research(slug: str, cfg: object, deps: object) -> object:
        calls.append(slug)
        raise SystemExit

    monkeypatch.setattr(w, "_research_impl", fake_research)
    monkeypatch.setattr(w, "_load_settings", lambda: {"segment": "candles"})
    deps = _research_deps()
    campaigns = _FakeCampaigns(campaign=None)
    out = await w.run_research_job(
        {"research_deps": deps, "campaigns_deps": campaigns, "app_deps": None}
    )
    assert out == {"ok": True, "skipped": "sem campanha ativa"}
    assert calls == []  # ciclo NAO rodou


async def test_run_research_job_runs_cycle_with_active_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Campanha ativa → ciclo roda, devolve counts e commita."""
    calls: list[str] = []

    async def fake_research(slug: str, cfg: object, deps: object) -> object:
        calls.append(slug)

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"web_found": 3}

        return _Out()

    monkeypatch.setattr(w, "_research_impl", fake_research)
    monkeypatch.setattr(w, "_load_settings", lambda: {"segment": "candles"})
    deps = _research_deps()
    now = datetime.now(UTC)
    campaign = ResearchCampaign(id=1, segment_id=1, started_at=now, ends_at=now)
    campaigns = _FakeCampaigns(campaign=campaign)
    committed: list[str] = []

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

    out = await w.run_research_job(
        {"research_deps": deps, "campaigns_deps": campaigns, "app_deps": _AppDeps()}
    )
    assert calls == ["candles"]
    assert out == {"ok": True, "partial": False, "web_found": 3}
    assert committed == ["commit"]


def test_workersettings_registers_research_job_and_cron() -> None:
    """Contrato: research_job em functions + cron 07:00 antes do collect."""
    settings = w.WorkerSettings
    assert {f.name for f in settings.functions} == {"collect_job", "score_job", "research_job"}
    coroutines = [cj.coroutine for cj in settings.cron_jobs]
    assert coroutines == [w.run_research_job, w.run_collect_job, w.run_score_job]
    assert settings.cron_jobs[0].hour == {7}
