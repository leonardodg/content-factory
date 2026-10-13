from typing import Any, ClassVar, cast

import pytest

from content_factory.app.research_pipeline import ResearchDeps
from content_factory.app.score import ScoreDeps
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
    await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": _NoopAppDeps()}
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

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

        async def rollback(self) -> None:
            rolled_back.append("rollback")

    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": _AppDeps()}
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

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

    # base do startup: só videos/scores importam — segment/keywords são
    # placeholder que o job substitui por segmento
    base = _score_deps_placeholder()
    out = await w.run_score_job(
        {"score_deps": base, "campaigns_deps": campaigns, "app_deps": _AppDeps()}
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

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

        async def rollback(self) -> None:
            rolled_back.append("rollback")

    out = await w.run_score_job(
        {
            "score_deps": _score_deps_placeholder(),
            "campaigns_deps": campaigns,
            "app_deps": _AppDeps(),
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


async def test_create_app_deps_startup_resolves_no_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 5: startup NÃO resolve mais 1 segmento fixo do banco.

    O único sinal de "quais segmentos processar" passou a ser
    `active_segments()` dentro de cada job — o startup monta
    ScoreDeps/ResearchDeps com placeholders que os jobs sempre
    substituem antes de usar. `get_by_slug` boom = guarda de regressão.
    """
    from collections.abc import AsyncGenerator
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

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

    out = await w.run_research_job(
        {
            "research_deps": _research_deps(),
            "campaigns_deps": _Campaigns(),
            "app_deps": _AppDeps(),
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

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

        async def rollback(self) -> None:
            rolled_back.append("rollback")

    out = await w.run_research_job(
        {"research_deps": _research_deps(), "campaigns_deps": _Campaigns(), "app_deps": _AppDeps()}
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
    from collections.abc import AsyncGenerator
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
    assert settings.rate_limit_redis_url is None
    deps = await w.create_app_deps(settings)
    assert opened == [settings.redis_url]  # um cliente só
    await deps.close()
    assert closed == [settings.redis_url]


async def test_create_app_deps_two_redis_when_urls_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URLs diferentes ⇒ dois clientes; ambos fechados no shutdown."""
    from collections.abc import AsyncGenerator
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
