import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, ClassVar, cast

import pytest

from content_factory.app.collect import CollectDeps, collect
from content_factory.app.ports import Collector
from content_factory.app.research_pipeline import ResearchDeps
from content_factory.app.score import ScoreDeps
from content_factory.domain.errors import SegmentNotFound
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, ResearchCampaign, Segment
from content_factory.infra.queue import worker as w
from content_factory.infra.ratelimit import RedisRateLimiter, Window
from tests.stubs import (
    ConcurrencyTracker,
    FakeCollector,
    FakeLimiter,
    FakeLock,
    FakeRedis,
    FakeRepos,
    TrackedCollector,
)


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


class _FakeSession:
    """Sessão fake com identidade própria: 1 por segmento (Task 10)."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


class _FakeUoW:
    """UoW fake: commit/rollback delegam à sessão do segmento + hooks de contagem."""

    def __init__(
        self,
        session: _FakeSession,
        on_commit: Callable[[], None],
        on_rollback: Callable[[], None],
    ) -> None:
        self.session = session
        self._on_commit = on_commit
        self._on_rollback = on_rollback

    async def commit(self) -> None:
        await self.session.commit()
        self._on_commit()

    async def rollback(self) -> None:
        await self.session.rollback()
        self._on_rollback()


@dataclass
class _FakeScorePart:
    segment: Segment


@dataclass
class _FakeResearchPart:
    guard: FanoutGuard


@dataclass
class _FakeSegmentBundle:
    uow: _FakeUoW
    collect: object
    score: _FakeScorePart
    research: _FakeResearchPart


class _FakeAppDeps:
    """Fake de AppDeps (Task 10): `create_segment_deps` abre 1 UoW/sessão por segmento.

    Mesma política do real: exceção dentro do escopo → rollback da sessão
    do segmento, sem tocar nas demais. `committed`/`rolled_back` são injetados
    pelos testes para os asserts de envelope continuarem idênticos.
    """

    def __init__(self, committed: list[str], rolled_back: list[str]) -> None:
        self.sessions: list[_FakeSession] = []
        self._committed = committed
        self._rolled_back = rolled_back

    @asynccontextmanager
    async def create_segment_deps(self, segment: Segment) -> AsyncGenerator[_FakeSegmentBundle]:
        session = _FakeSession()
        self.sessions.append(session)
        committed = self._committed
        rolled_back = self._rolled_back
        uow = _FakeUoW(
            session,
            on_commit=lambda: committed.append("commit"),
            on_rollback=lambda: rolled_back.append("rollback"),
        )
        try:
            yield _FakeSegmentBundle(
                uow=uow,
                collect=object(),
                score=_FakeScorePart(segment=segment),
                research=_FakeResearchPart(guard=FanoutGuard(segment.research or DepthConfig())),
            )
        except BaseException:
            await uow.rollback()
            raise


def _collect_cfg(platforms: list[str], limit: int) -> dict[str, Any]:
    """`_load_settings` fake com tipos concretos (pyright strict)."""
    return {"platforms": platforms, "limit": limit}


class _NoopScores:
    async def upsert(
        self, video_id: int, segment_id: int, result: object, computed_at: object
    ) -> None:
        pass


def _score_deps_placeholder() -> ScoreDeps:
    """ScoreDeps de startup: segment/keywords são placeholders (Task 5) —
    run_score_job sempre substitui antes de chamar _score_impl."""
    repos = FakeRepos()
    return ScoreDeps(
        videos=repos.videos,  # type: ignore[arg-type]
        scores=_NoopScores(),  # type: ignore[arg-type]
        segment=Segment(id=0, slug="_placeholder", name="_", platforms=[], keywords=[]),
        keywords=[],
    )


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
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)

    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert calls == ["velas", "sabonetes"]
    assert out["ok"] is True
    assert set(out["segments"]) == {"velas", "sabonetes"}
    assert committed == ["commit", "commit"]  # 1 commit por segmento
    assert rolled_back == []


async def test_run_collect_job_noop_when_no_active_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 segmentos ativos → noop ok=True, sem chamar collect."""
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
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
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 5))
    campaigns = _FakeCampaignsMultiSegment(slugs=["candles"], platforms=["youtube", "tiktok"])
    committed: list[str] = []
    rolled_back: list[str] = []
    await w.run_collect_job(
        {
            "collect_deps": object(),
            "campaigns_deps": campaigns,
            "app_deps": _FakeAppDeps(committed, rolled_back),
        }
    )
    assert calls == [["youtube", "tiktok"]]  # plataformas do segmento (stub)


async def test_run_collect_job_isolates_segment_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 segmento levanta exceção não-CollectorError → outros seguem rodando.

    `web.py`/outros collectors podem levantar exceções que `collect()` não
    captura (só pega `CollectorError`) — o job não pode deixar isso abortar
    os demais segmentos do tick.
    """
    calls: list[str] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(slug)
        if slug == "velas":
            raise RuntimeError("boom")

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)

    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert calls == ["velas", "sabonetes"]  # ambos tentados, 1 não trava o outro
    assert out["ok"] is False
    assert out["segments"]["velas"]["ok"] is False
    assert "boom" in out["segments"]["velas"]["error"]
    assert out["segments"]["sabonetes"] == {"ok": True, "partial": False, "found": 1}
    assert committed == ["commit"]  # só sabonetes comitou
    assert rolled_back == ["rollback"]  # velas sofreu rollback, não trava a sessão


async def test_run_score_job_processes_all_active_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 segmentos ativos → ScoreDeps reconstruído por segmento, 1 commit cada."""
    calls: list[str] = []

    async def fake_score(deps: ScoreDeps) -> object:
        calls.append(deps.segment.slug)

        class _Out:
            ok = True
            scored = 3

        return _Out()

    monkeypatch.setattr(w, "_score_impl", fake_score)
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)

    # base do startup: só videos/scores importam — segment/keywords são
    # placeholder que o job substitui por segmento
    base = _score_deps_placeholder()
    out = await w.run_score_job(
        {"score_deps": base, "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert calls == ["velas", "sabonetes"]
    assert out == {
        "ok": True,
        "segments": {"velas": {"ok": True, "scored": 3}, "sabonetes": {"ok": True, "scored": 3}},
    }
    assert committed == ["commit", "commit"]


async def test_run_score_job_isolates_segment_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 segmento levanta exceção → outros seguem, erro aparece no envelope."""
    calls: list[str] = []

    async def fake_score(deps: ScoreDeps) -> object:
        calls.append(deps.segment.slug)
        if deps.segment.slug == "velas":
            raise RuntimeError("boom")

        class _Out:
            ok = True
            scored = 3

        return _Out()

    monkeypatch.setattr(w, "_score_impl", fake_score)
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)

    out = await w.run_score_job(
        {
            "score_deps": _score_deps_placeholder(),
            "campaigns_deps": campaigns,
            "app_deps": app_deps,
        }
    )
    assert calls == ["velas", "sabonetes"]
    assert out["ok"] is False
    assert out["segments"]["velas"]["ok"] is False
    assert "boom" in out["segments"]["velas"]["error"]
    assert out["segments"]["sabonetes"] == {"ok": True, "scored": 3}
    assert committed == ["commit"]
    assert rolled_back == ["rollback"]


async def test_run_score_job_noop_when_no_active_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    async def fake_score(deps: object) -> object:
        calls.append(deps)
        raise AssertionError("nao devia rodar")

    monkeypatch.setattr(w, "_score_impl", fake_score)
    campaigns = _FakeCampaignsMultiSegment(slugs=[])
    out = await w.run_score_job(
        {
            "score_deps": _score_deps_placeholder(),
            "campaigns_deps": campaigns,
            "app_deps": _NoopAppDeps(),
        }
    )
    assert out == {"ok": True, "segments": {}}
    assert calls == []


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
    # Task 11: on_job_end REMOVIDO — cada UoW já reverte em exceção via
    # session_scope; não há mais sessão global a limpar após o job.
    assert getattr(settings, "on_job_end", None) is None
    assert settings.on_shutdown is w.shutdown


def test_workersettings_max_jobs_allows_concurrency() -> None:
    """Task 11: sessões por UoW — jobs podem se sobrepor (era 1 pela sessão global)."""
    # 3 = 1 por tipo de job (collect/score/research); engine e clientes Redis
    # são task-safe, AsyncSession nunca é compartilhada.
    assert w.WorkerSettings.max_jobs == 3


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


async def test_create_app_deps_startup_resolves_no_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 5: startup NÃO resolve mais 1 segmento fixo do banco.

    O único sinal de "quais segmentos processar" passou a ser
    `active_segments()` dentro de cada job — o startup monta
    ScoreDeps/ResearchDeps com placeholders que os jobs sempre
    substituem antes de usar. `get_by_slug` boom = guarda de regressão.
    """
    from contextlib import asynccontextmanager

    import content_factory.infra.collectors as collectors_mod
    import content_factory.infra.db.base as db_base
    import content_factory.infra.db.repositories as repos_mod
    import content_factory.infra.redis_ops as redis_ops
    from content_factory.config import Settings

    async def _boom(self: object, slug: str) -> Segment:
        raise AssertionError("startup nao deve resolver segmento")

    monkeypatch.setattr(repos_mod.SegmentRepositoryImpl, "get_by_slug", _boom)

    class _Session:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    @asynccontextmanager
    async def _session() -> AsyncGenerator[Any]:
        yield _Session()

    @asynccontextmanager
    async def _redis(_url: str) -> AsyncGenerator[Any]:
        yield object()  # RedisLock/RateLimiter só armazenam o client

    def _factory(_settings: Settings) -> Any:
        return _session

    def _register(_settings: Settings) -> None:
        pass

    class _Registry:
        def snapshot(self) -> list[str]:
            return []

        def resolve(self, names: list[str]) -> list[object]:
            return []

    monkeypatch.setattr(db_base, "get_session_factory", _factory)
    monkeypatch.setattr(redis_ops, "redis_client", _redis)
    monkeypatch.setattr(collectors_mod, "register_defaults", _register)
    monkeypatch.setattr(collectors_mod, "registry", _Registry())

    deps = await w.create_app_deps(Settings())
    # placeholders nunca lidos fora do loop dos jobs (Tasks 2-4)
    assert deps.score.segment.slug == "_placeholder"
    assert deps.score.segment.id == 0
    assert deps.score.keywords == []
    assert deps.research.guard is not None
    assert not deps.research.guard.admit("x", depth=99)  # DepthConfig default
    assert deps.collect.collectors == []
    assert deps.campaigns is not None
    await deps.close()


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


async def test_run_research_job_noop_when_no_active_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 segmentos com campanha ativa → noop ok=True, sem chamar o ciclo.

    A chave antiga `skipped` saiu do envelope — a lista vazia de
    `active_segments()` já é o "sem campanha ativa".
    """
    calls: list[str] = []

    async def fake_research(slug: str, cfg: object, deps: object) -> object:
        calls.append(slug)
        raise AssertionError("ciclo nao devia rodar")

    monkeypatch.setattr(w, "_research_impl", fake_research)
    campaigns = _FakeCampaignsMultiSegment(slugs=[])
    out = await w.run_research_job(
        {"research_deps": _research_deps(), "campaigns_deps": campaigns, "app_deps": _NoopAppDeps()}
    )
    assert out == {"ok": True, "segments": {}}
    assert calls == []


async def test_run_research_job_processes_all_active_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 campanhas ativas num tick → 1 ciclo cada, guard trocado, 1 commit cada."""

    class _Campaigns:
        async def active_segments(self) -> list[Segment]:
            return [
                Segment(
                    id=1,
                    slug="velas",
                    name="Velas",
                    platforms=["youtube"],
                    keywords=["k"],
                    research=DepthConfig(max_depth=5),
                ),
                Segment(
                    id=2,
                    slug="sabonetes",
                    name="Sabonetes",
                    platforms=["youtube"],
                    keywords=["k"],
                ),
            ]

        async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
            return None

        async def start(self, segment_id: int, ends_at: object) -> ResearchCampaign:
            raise NotImplementedError

        async def stop(self, segment_id: int) -> None:
            pass

    calls: list[str] = []
    guard_allows_depth4: list[bool] = []

    async def fake_research(slug: str, cfg: object, deps: object) -> object:
        research = cast("ResearchDeps", deps)
        calls.append(slug)
        guard_allows_depth4.append(research.guard.admit(slug, depth=4))

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"web_found": 3}

        return _Out()

    monkeypatch.setattr(w, "_research_impl", fake_research)
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)

    out = await w.run_research_job(
        {
            "research_deps": _research_deps(),
            "campaigns_deps": _Campaigns(),
            "app_deps": app_deps,
        }
    )
    assert calls == ["velas", "sabonetes"]
    # guard novo por segmento: max_depth=5 do segmento permite depth=4;
    # segmento sem research cai no DepthConfig default (max_depth=2)
    assert guard_allows_depth4 == [True, False]
    assert out == {
        "ok": True,
        "segments": {
            "velas": {"ok": True, "partial": False, "web_found": 3},
            "sabonetes": {"ok": True, "partial": False, "web_found": 3},
        },
    }
    assert committed == ["commit", "commit"]


async def test_run_research_job_isolates_segment_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1 segmento levanta exceção (ex.: trends_google fora do try/except de
    `run_research_cycle`) → os demais seguem, erro aparece no envelope."""

    class _Campaigns:
        async def active_segments(self) -> list[Segment]:
            return [
                Segment(id=1, slug="velas", name="Velas", platforms=["youtube"], keywords=["k"]),
                Segment(
                    id=2, slug="sabonetes", name="Sabonetes", platforms=["youtube"], keywords=["k"]
                ),
            ]

        async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
            return None

        async def start(self, segment_id: int, ends_at: object) -> ResearchCampaign:
            raise NotImplementedError

        async def stop(self, segment_id: int) -> None:
            pass

    calls: list[str] = []

    async def fake_research(slug: str, cfg: object, deps: object) -> object:
        calls.append(slug)
        if slug == "velas":
            raise RuntimeError("boom")

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"web_found": 3}

        return _Out()

    monkeypatch.setattr(w, "_research_impl", fake_research)
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)

    out = await w.run_research_job(
        {"research_deps": _research_deps(), "campaigns_deps": _Campaigns(), "app_deps": app_deps}
    )
    assert calls == ["velas", "sabonetes"]
    assert out["ok"] is False
    assert out["segments"]["velas"]["ok"] is False
    assert "boom" in out["segments"]["velas"]["error"]
    assert out["segments"]["sabonetes"] == {"ok": True, "partial": False, "web_found": 3}
    assert committed == ["commit"]
    assert rolled_back == ["rollback"]


def test_workersettings_registers_research_job_and_cron() -> None:
    """Contrato: research_job em functions + cron 07:00 antes do collect."""
    settings = w.WorkerSettings
    assert {f.name for f in settings.functions} == {"collect_job", "score_job", "research_job"}
    coroutines = [cj.coroutine for cj in settings.cron_jobs]
    assert coroutines == [w.run_research_job, w.run_collect_job, w.run_score_job]
    assert settings.cron_jobs[0].hour == {7}


async def test_create_app_deps_single_redis_when_urls_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URLs iguais ⇒ um único cliente Redis (comportamento pré-Task 3)."""
    from contextlib import asynccontextmanager

    import content_factory.infra.collectors as collectors_mod
    import content_factory.infra.db.base as db_base
    import content_factory.infra.redis_ops as redis_ops
    from content_factory.config import Settings

    opened: list[str] = []
    closed: list[str] = []

    class _Client:
        def __init__(self, url: str) -> None:
            self.url = url

    @asynccontextmanager
    async def _redis(url: str) -> AsyncGenerator[object]:
        client = _Client(url)
        opened.append(url)
        try:
            yield client
        finally:
            closed.append(url)

    class _Session:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    @asynccontextmanager
    async def _session() -> AsyncGenerator[Any]:
        yield _Session()

    def _factory(_settings: Settings) -> Any:
        return _session

    def _register(_settings: Settings) -> None:
        pass

    class _Registry:
        def snapshot(self) -> list[str]:
            return []

        def resolve(self, names: list[str]) -> list[object]:
            return []

    monkeypatch.setattr(db_base, "get_session_factory", _factory)
    monkeypatch.setattr(redis_ops, "redis_client", _redis)
    monkeypatch.setattr(collectors_mod, "register_defaults", _register)
    monkeypatch.setattr(collectors_mod, "registry", _Registry())

    settings = Settings()
    assert settings.rate_limit_redis_url == settings.redis_url  # default = redis_url
    deps = await w.create_app_deps(settings)
    assert opened == [settings.redis_url]  # um cliente só
    await deps.close()
    assert closed == [settings.redis_url]


async def test_create_app_deps_two_redis_when_urls_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URLs diferentes ⇒ dois clientes; ambos fechados no shutdown."""
    from contextlib import asynccontextmanager

    import content_factory.infra.collectors as collectors_mod
    import content_factory.infra.db.base as db_base
    import content_factory.infra.redis_ops as redis_ops
    from content_factory.config import Settings

    opened: list[str] = []
    closed: list[str] = []

    class _Client:
        def __init__(self, url: str) -> None:
            self.url = url

    @asynccontextmanager
    async def _redis(url: str) -> AsyncGenerator[object]:
        client = _Client(url)
        opened.append(url)
        try:
            yield client
        finally:
            closed.append(url)

    class _Session:
        async def commit(self) -> None:
            pass

        async def rollback(self) -> None:
            pass

    @asynccontextmanager
    async def _session() -> AsyncGenerator[Any]:
        yield _Session()

    def _factory(_settings: Settings) -> Any:
        return _session

    def _register(_settings: Settings) -> None:
        pass

    class _Registry:
        def snapshot(self) -> list[str]:
            return []

        def resolve(self, names: list[str]) -> list[object]:
            return []

    monkeypatch.setattr(db_base, "get_session_factory", _factory)
    monkeypatch.setattr(redis_ops, "redis_client", _redis)
    monkeypatch.setattr(collectors_mod, "register_defaults", _register)
    monkeypatch.setattr(collectors_mod, "registry", _Registry())

    settings = Settings(
        redis_url="redis://redis:6379/1", rate_limit_redis_url="redis://redis:6379/0"
    )
    deps = await w.create_app_deps(settings)
    assert opened == ["redis://redis:6379/1", "redis://redis:6379/0"]
    await deps.close()
    assert sorted(closed) == ["redis://redis:6379/0", "redis://redis:6379/1"]


async def test_startup_fails_when_collect_segment_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard P0: imagem multi-segmento sob compose antigo (COLLECT_SEGMENT) falha no startup."""
    monkeypatch.setenv("COLLECT_SEGMENT", "velas-artesanais")
    ctx: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="multi-segmento"):
        await w.startup(ctx)


async def test_startup_ok_without_collect_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem COLLECT_SEGMENT o startup segue normal (injeta app_deps)."""
    monkeypatch.delenv("COLLECT_SEGMENT", raising=False)

    class _Deps:
        collect = object()
        score = object()
        research = object()
        campaigns = object()

        async def close(self) -> None:
            pass

    async def _fake_create(_settings: object) -> object:
        return _Deps()

    monkeypatch.setattr(w, "create_app_deps", _fake_create)
    ctx: dict[str, object] = {}
    await w.startup(ctx)
    assert "app_deps" in ctx
    await w.shutdown(ctx)


def test_load_settings_max_concurrency_default_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 7: COLLECT_MAX_CONCURRENCY (default 2; inválido/<1 cai em 2/1)."""
    load = w._load_settings  # pyright: ignore[reportPrivateUsage]
    monkeypatch.delenv("COLLECT_MAX_CONCURRENCY", raising=False)
    assert load()["max_concurrency"] == 2
    monkeypatch.setenv("COLLECT_MAX_CONCURRENCY", "4")
    assert load()["max_concurrency"] == 4
    monkeypatch.setenv("COLLECT_MAX_CONCURRENCY", "lixo")
    assert load()["max_concurrency"] == 2
    monkeypatch.setenv("COLLECT_MAX_CONCURRENCY", "0")
    assert load()["max_concurrency"] == 1


async def test_run_collect_job_segments_get_distinct_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 10: cada segmento recebe UMA sessão própria; commits independentes."""
    seen_collect_deps: list[object] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        seen_collect_deps.append(deps)

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)
    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert out["ok"] is True
    assert len(app_deps.sessions) == 2  # 1 UoW por segmento, ainda sequencial
    assert app_deps.sessions[0] is not app_deps.sessions[1]  # sessão NUNCA compartilhada
    assert [s.events for s in app_deps.sessions] == [["commit"], ["commit"]]
    assert committed == ["commit", "commit"]
    assert rolled_back == []


async def test_run_collect_job_failed_segment_rolls_back_only_its_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 10: falha de 1 segmento → rollback só da sua sessão; a outra comita."""
    calls: list[str] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(slug)
        if slug == "velas":
            raise RuntimeError("boom")

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)
    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert calls == ["velas", "sabonetes"]
    assert out["ok"] is False
    assert out["segments"]["velas"]["ok"] is False
    assert len(app_deps.sessions) == 2
    assert app_deps.sessions[0] is not app_deps.sessions[1]
    assert app_deps.sessions[0].events == ["rollback"]  # commit de um não depende do outro
    assert app_deps.sessions[1].events == ["commit"]
    assert committed == ["commit"]
    assert rolled_back == ["rollback"]


async def test_failed_segment_logs_error_with_traceback_and_error_type(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Etapa 0 Task 5: segmento que levanta gera 1 ERROR com traceback; envelope tem error_type."""

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        if slug == "velas":
            raise RuntimeError("boom")

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)
    with caplog.at_level(logging.ERROR):
        out = await w.run_collect_job(
            {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
        )
    assert out["segments"]["velas"] == {
        "ok": False,
        "error": "boom",
        "error_type": "RuntimeError",
    }
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "velas" in r.getMessage()]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert "collect" in errors[0].getMessage()


async def test_create_segment_deps_builds_per_segment_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 10: bundle real por segmento — ScoreDeps/ResearchDeps re-resolvidos na UoW.

    Repositórios nascem por unidade de trabalho (não no startup); engine e
    clientes Redis continuam compartilhados. Seam igual ao de
    test_create_app_deps_startup_resolves_no_segment, mas com sessões
    gravadas para provar o isolamento.
    """
    from contextlib import asynccontextmanager

    import content_factory.infra.collectors as collectors_mod
    import content_factory.infra.db.base as db_base
    import content_factory.infra.redis_ops as redis_ops
    from content_factory.config import Settings

    created: list[_FakeSession] = []

    @asynccontextmanager
    async def _session() -> AsyncGenerator[_FakeSession]:
        session = _FakeSession()
        created.append(session)
        yield session

    @asynccontextmanager
    async def _redis(_url: str) -> AsyncGenerator[Any]:
        yield object()

    def _factory(_settings: Settings) -> Any:
        return _session

    def _register(_settings: Settings) -> None:
        pass

    class _Registry:
        def snapshot(self) -> list[str]:
            return []

        def resolve(self, names: list[str]) -> list[object]:
            return []

    monkeypatch.setattr(db_base, "get_session_factory", _factory)
    monkeypatch.setattr(redis_ops, "redis_client", _redis)
    monkeypatch.setattr(collectors_mod, "register_defaults", _register)
    monkeypatch.setattr(collectors_mod, "registry", _Registry())

    deps = await w.create_app_deps(Settings())
    seg_a = Segment(
        id=1,
        slug="velas",
        name="Velas",
        platforms=["youtube"],
        keywords=["k1"],
        research=DepthConfig(max_depth=5),
    )
    seg_b = Segment(id=2, slug="sabonetes", name="Sabonetes", platforms=["youtube"], keywords=["k"])
    async with deps.create_segment_deps(seg_a) as bundle_a:
        assert bundle_a.score.segment.slug == "velas"
        assert bundle_a.score.keywords == ["k1"]
        assert bundle_a.research.guard.admit("x", depth=4) is True
        await bundle_a.uow.commit()
    async with deps.create_segment_deps(seg_b) as bundle_b:
        assert bundle_b.score.segment.slug == "sabonetes"
        assert bundle_b.research.guard.admit("x", depth=4) is False
        await bundle_b.uow.commit()
    assert len(created) == 3  # 1 sessão de startup (legado, Task 11 aposenta) + 1 UoW/segmento
    assert created[1] is not created[2]
    assert [s.events for s in created] == [[], ["commit"], ["commit"]]
    await deps.close()


async def test_segment_bundles_get_distinct_lock_instances_over_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Etapa 0 Task 2: cada bundle tem seu RedisLock; o cliente é compartilhado.

    Topologia pós-fix (uma instância por segmento sobre o mesmo
    `lock_client`): o token mora na instância, então o release tardio de um
    segmento nunca apaga o lock do outro.
    """
    from contextlib import asynccontextmanager

    import content_factory.infra.collectors as collectors_mod
    import content_factory.infra.db.base as db_base
    import content_factory.infra.redis_ops as redis_ops
    from content_factory.config import Settings

    @asynccontextmanager
    async def _session() -> AsyncGenerator[_FakeSession]:
        yield _FakeSession()

    @asynccontextmanager
    async def _redis(_url: str) -> AsyncGenerator[Any]:
        yield object()

    def _factory(_settings: Settings) -> Any:
        return _session

    def _register(_settings: Settings) -> None:
        pass

    class _Registry:
        def snapshot(self) -> list[str]:
            return []

        def resolve(self, names: list[str]) -> list[object]:
            return []

    monkeypatch.setattr(db_base, "get_session_factory", _factory)
    monkeypatch.setattr(redis_ops, "redis_client", _redis)
    monkeypatch.setattr(collectors_mod, "register_defaults", _register)
    monkeypatch.setattr(collectors_mod, "registry", _Registry())

    deps = await w.create_app_deps(Settings())
    seg_a = Segment(id=1, slug="velas", name="Velas", platforms=["youtube"], keywords=["k"])
    seg_b = Segment(id=2, slug="sabonetes", name="Sabonetes", platforms=["youtube"], keywords=["k"])
    async with (
        deps.create_segment_deps(seg_a) as bundle_a,
        deps.create_segment_deps(seg_b) as bundle_b,
    ):
        assert bundle_a.collect.lock is not bundle_b.collect.lock
        assert bundle_a.research.lock is not bundle_b.research.lock
        client_a = cast(Any, bundle_a.collect.lock)._client
        client_b = cast(Any, bundle_b.collect.lock)._client
        assert client_a is client_b is deps.lock_client
    await deps.close()


async def test_worker_segments_share_platform_slots_capping_tiktok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Etapa 0 Task 7: 3 segmentos concorrentes sob 1 PlatformSlots.

    O teto é por processo, não por segmento: tiktok=1 serializa (pico 1)
    enquanto youtube sem teto sobrepõe (pico >= 2). Os 3 bundles herdam a
    MESMA instância criada no startup.
    """
    from contextlib import asynccontextmanager

    import content_factory.infra.collectors as collectors_mod
    import content_factory.infra.db.base as db_base
    import content_factory.infra.redis_ops as redis_ops
    from content_factory.config import Settings

    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "3")
    monkeypatch.setenv("PLATFORM_MAX_CONCURRENCY", "tiktok=1")

    @asynccontextmanager
    async def _session() -> AsyncGenerator[_FakeSession]:
        yield _FakeSession()

    @asynccontextmanager
    async def _redis(_url: str) -> AsyncGenerator[Any]:
        yield object()

    def _factory(_settings: Settings) -> Any:
        return _session

    def _register(_settings: Settings) -> None:
        pass

    class _Registry:
        def snapshot(self) -> list[str]:
            return []

        def resolve(self, names: list[str]) -> list[object]:
            return []

    monkeypatch.setattr(db_base, "get_session_factory", _factory)
    monkeypatch.setattr(redis_ops, "redis_client", _redis)
    monkeypatch.setattr(collectors_mod, "register_defaults", _register)
    monkeypatch.setattr(collectors_mod, "registry", _Registry())

    tiktok_tracker = ConcurrencyTracker()
    youtube_tracker = ConcurrencyTracker()
    tiktok = TrackedCollector("tiktok", tiktok_tracker, delay=0.05)
    youtube = TrackedCollector("youtube", youtube_tracker, delay=0.05)
    seen_slots: list[object] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        bundle_collect = cast(CollectDeps, deps)
        seen_slots.append(bundle_collect.platform_slots)
        repos = FakeRepos()
        test_deps = replace(
            bundle_collect,
            collectors=[youtube, tiktok],
            videos=repos.videos,
            segments=repos.segments,
            runs=repos.runs,
            limiter=FakeLimiter(),
            lock=FakeLock(),
        )
        out = await collect(slug, platforms, limit, test_deps)

        class _Out:
            ok = out.ok
            partial = out.partial
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    campaigns = _FakeCampaignsMultiSegment(slugs=["a", "b", "c"])
    app_deps = await w.create_app_deps(Settings())
    try:
        out = await w.run_collect_job(
            {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
        )
    finally:
        await app_deps.close()
    assert out["ok"] is True
    assert tiktok_tracker.peak == 1
    assert youtube_tracker.peak >= 2
    assert len(seen_slots) == 3
    assert seen_slots[0] is seen_slots[1] is seen_slots[2]
    assert seen_slots[0] is app_deps.collect.platform_slots


async def test_create_app_deps_close_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 10: shutdown fecha o engine (dispose), além da sessão/Redis."""
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncEngine

    import content_factory.infra.collectors as collectors_mod
    import content_factory.infra.redis_ops as redis_ops
    from content_factory.config import Settings

    @asynccontextmanager
    async def _redis(_url: str) -> AsyncGenerator[Any]:
        yield object()

    def _register(_settings: Settings) -> None:
        pass

    class _Registry:
        def snapshot(self) -> list[str]:
            return []

        def resolve(self, names: list[str]) -> list[object]:
            return []

    monkeypatch.setattr(redis_ops, "redis_client", _redis)
    monkeypatch.setattr(collectors_mod, "register_defaults", _register)
    monkeypatch.setattr(collectors_mod, "registry", _Registry())

    # get_session_factory REAL (não monkeypatchado): engine lazy, sem conexões.
    disposed: list[str] = []

    async def _recording_dispose(self: AsyncEngine) -> None:
        disposed.append("dispose")

    monkeypatch.setattr(AsyncEngine, "dispose", _recording_dispose)
    deps = await w.create_app_deps(Settings())
    assert deps.engine is not None
    assert deps.engine is deps.session_factory.kw["bind"]  # 1 engine compartilhado
    await deps.close()
    assert disposed == ["dispose"]


def test_max_segments_from_env_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 11: WORKER_MAX_SEGMENTS (default 3; inválido/<1 cai em 3/1)."""
    load = w._max_segments_from_env  # pyright: ignore[reportPrivateUsage]
    monkeypatch.delenv("WORKER_MAX_SEGMENTS", raising=False)
    assert load() == 3
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "2")
    assert load() == 2
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "lixo")
    assert load() == 3
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "0")
    assert load() == 1


async def test_run_collect_job_runs_segments_concurrently_with_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 11: 3 segmentos sob TaskGroup + semáforo WORKER_MAX_SEGMENTS=2.

    Pico de `_collect_impl` simultâneos == 2 (o sleep força a sobreposição);
    o envelope tem TODOS os slugs — nenhum segmento silenciosamente abandonado.
    """
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "2")
    state: dict[str, int] = {"current": 0, "peak": 0}
    entered: list[str] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        entered.append(slug)
        try:
            await asyncio.sleep(0.02)
        finally:
            state["current"] -= 1

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes", "resina"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)
    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert state["peak"] == 2  # semáforo segurou o 3o enquanto 2 rodavam
    assert set(out["segments"]) == {"velas", "sabonetes", "resina"}
    assert out["ok"] is True
    assert len(app_deps.sessions) == 3
    assert len({id(s) for s in app_deps.sessions}) == 3  # sessão NUNCA compartilhada
    assert committed == ["commit", "commit", "commit"]
    assert rolled_back == []


async def test_run_collect_job_concurrent_failure_keeps_other_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 11: falha de 1 segmento DURANTE o overlap não cancela as irmãs.

    O sleep força a sobreposição real (velas levanta depois das outras já
    terem entrado): sem o try/except DENTRO da task, o TaskGroup cancelaria
    tudo e slugs sumiriam do envelope.
    """
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "3")

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        await asyncio.sleep(0.02 if slug == "velas" else 0.01)
        if slug == "velas":
            raise RuntimeError("boom")

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg([], 10))
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes", "resina"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)
    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert set(out["segments"]) == {"velas", "sabonetes", "resina"}
    assert out["ok"] is False
    assert out["segments"]["velas"]["ok"] is False
    assert "boom" in out["segments"]["velas"]["error"]
    assert out["segments"]["sabonetes"]["ok"] is True
    assert out["segments"]["resina"]["ok"] is True
    assert committed == ["commit", "commit"]
    assert rolled_back == ["rollback"]


class _KeyedFakeLock:
    """Lock por chave (espelha RedisLock): segmentos distintos não se bloqueiam.

    O FakeLock de stubs.py é de estado único — o 2o segmento nunca adquiriria.
    Aqui cada `collect:{slug}` tem seu próprio estado.
    """

    def __init__(self) -> None:
        self.held: set[str] = set()
        self.acquired: list[str] = []
        self.released: list[str] = []

    async def acquire_once(self, key: str) -> bool:
        if key in self.held:
            return False
        self.held.add(key)
        self.acquired.append(key)
        return True

    async def release(self, key: str) -> None:
        self.released.append(key)
        self.held.discard(key)


@dataclass
class _RealCollectBundle:
    uow: _FakeUoW
    collect: CollectDeps


class _SharedLimiterAppDeps:
    """AppDeps com CollectDeps REAIS sobre limiter/lock compartilhados (Task 11).

    Exercita o caminho real `collect()` (janela Redis) com UoWs fakes por
    segmento — prova que o teto de plataforma soma os segmentos.
    """

    def __init__(
        self,
        limiter: RedisRateLimiter,
        lock: _KeyedFakeLock,
        collector: Collector,
        committed: list[str],
        rolled_back: list[str],
    ) -> None:
        self._limiter = limiter
        self._lock = lock
        self._collector = collector
        self._committed = committed
        self._rolled_back = rolled_back

    @asynccontextmanager
    async def create_segment_deps(self, segment: Segment) -> AsyncGenerator[_RealCollectBundle]:
        repos = FakeRepos()
        session = _FakeSession()
        committed = self._committed
        rolled_back = self._rolled_back
        uow = _FakeUoW(
            session,
            on_commit=lambda: committed.append("commit"),
            on_rollback=lambda: rolled_back.append("rollback"),
        )
        deps = CollectDeps(
            collectors=[self._collector],
            videos=repos.videos,
            segments=repos.segments,
            runs=repos.runs,
            limiter=self._limiter,
            lock=self._lock,  # type: ignore[arg-type]
        )
        try:
            yield _RealCollectBundle(uow=uow, collect=deps)
        except BaseException:
            await uow.rollback()
            raise


async def test_run_collect_job_global_limiter_shared_across_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 11: teto de plataforma SOMANDO os segmentos (limiter Redis da Task 3).

    3 segmentos x 1 plataforma sobre o MESMO FakeRedis com janela 2/dia: 2
    passam, o 3o vira failed via RateLimited — sem o cliente compartilhado
    cada segmento teria sua própria janela e os 3 passariam.
    """
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "3")
    monkeypatch.setattr(w, "_collect_impl", collect)
    monkeypatch.setattr(w, "_load_settings", lambda: _collect_cfg(["youtube"], 10))
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, windows={"youtube": Window(limit=2, period_s=86_400)})
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _SharedLimiterAppDeps(
        limiter, _KeyedFakeLock(), FakeCollector("youtube", n=2), committed, rolled_back
    )
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes", "resina"])
    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert set(out["segments"]) == {"velas", "sabonetes", "resina"}
    assert out["ok"] is False
    oks = sorted(s for s in out["segments"] if out["segments"][s]["ok"])
    fails = sorted(s for s in out["segments"] if not out["segments"][s]["ok"])
    assert len(oks) == 2 and len(fails) == 1
    assert out["segments"][fails[0]]["failed"] == 1  # counts do collect real
    assert sum(out["segments"][s]["found"] for s in oks) == 4  # 2 videos x 2 janelas
    assert redis.counters["cf:rl:youtube"] == 2  # janela COMUM, nao 3x
    assert committed == ["commit", "commit", "commit"]  # run failed também commita
    assert rolled_back == []


async def test_run_score_job_runs_segments_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 11: score também fana-out sob o mesmo TaskGroup + semáforo."""
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "3")
    state: dict[str, int] = {"current": 0, "peak": 0}

    async def fake_score(deps: ScoreDeps) -> object:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        try:
            await asyncio.sleep(0.01)
        finally:
            state["current"] -= 1

        class _Out:
            ok = True
            scored = 3

        return _Out()

    monkeypatch.setattr(w, "_score_impl", fake_score)
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes", "resina"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)
    out = await w.run_score_job(
        {
            "score_deps": _score_deps_placeholder(),
            "campaigns_deps": campaigns,
            "app_deps": app_deps,
        }
    )
    assert state["peak"] == 3
    assert set(out["segments"]) == {"velas", "sabonetes", "resina"}
    assert out["ok"] is True
    assert committed == ["commit", "commit", "commit"]
    assert rolled_back == []


async def test_run_research_job_runs_segments_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 11: research também fana-out sob o mesmo TaskGroup + semáforo."""
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "3")
    state: dict[str, int] = {"current": 0, "peak": 0}

    async def fake_research(slug: str, cfg: object, deps: object) -> object:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        try:
            await asyncio.sleep(0.01)
        finally:
            state["current"] -= 1

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"web_found": 3}

        return _Out()

    monkeypatch.setattr(w, "_research_impl", fake_research)
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes", "resina"])
    committed: list[str] = []
    rolled_back: list[str] = []
    app_deps = _FakeAppDeps(committed, rolled_back)
    out = await w.run_research_job(
        {"research_deps": _research_deps(), "campaigns_deps": campaigns, "app_deps": app_deps}
    )
    assert state["peak"] == 3
    assert set(out["segments"]) == {"velas", "sabonetes", "resina"}
    assert out["ok"] is True
    assert committed == ["commit", "commit", "commit"]
    assert rolled_back == []


async def test_campaigns_repo_opens_fresh_session_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 11: repo de campanhas abre 1 sessão fresca por chamada.

    Com max_jobs > 1 os jobs se sobrepõem — o repo do startup NÃO pode estar
    ligado à sessão compartilhada (quebraria com IllegalStateChangeError).
    """
    import content_factory.infra.db.repositories as repos_mod

    seen: list[object] = []

    class _Impl:
        def __init__(self, session: object) -> None:
            seen.append(session)

        async def active_segments(self) -> list[Segment]:
            return []

    monkeypatch.setattr(repos_mod, "ResearchCampaignRepositoryImpl", _Impl)

    @asynccontextmanager
    async def _session() -> AsyncGenerator[object]:
        yield object()

    def _factory() -> Any:
        return _session()

    wrapper = w._FreshSessionCampaigns(cast("Any", _factory))  # pyright: ignore[reportPrivateUsage]
    await wrapper.active_segments()
    await wrapper.active_segments()
    assert len(seen) == 2
    assert seen[0] is not seen[1]
