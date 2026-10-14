import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from tests.stubs import (
    ConcurrencyTracker,
    FakeLimiter,
    FakeLock,
    FakeRepos,
    FakeRuns,
    FakeTrendObservations,
    FakeVideos,
)

from content_factory.app.gate_hits import record_gate_hit
from content_factory.app.ports import KeywordSignalRepository, SocialDiscoverer
from content_factory.app.research_pipeline import ResearchDeps, ResearchOutcome, run_research_cycle
from content_factory.domain.errors import CollectorError
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    DepthConfig,
    KeywordSignal,
    Segment,
    SegmentConfig,
    TrendPoint,
    UpsertStats,
)


class _FakeWeb:
    platform: ClassVar[str] = "web_suggest"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
        self.calls.append(list(seeds))
        return [
            KeywordSignal(
                keyword=f"{seeds[0]} relacionada",
                source=self.platform,
                weight=0.7,
                depth=depth + 1,
            )
        ]


class _FakeTrends:
    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]:
        return [
            TrendPoint(
                keyword=k,
                period_start=datetime(2026, 10, 1, tzinfo=UTC),
                period_granularity="week",
                interest=50.0,
            )
            for k in keywords
        ]


class _FakeSocial:
    platform: ClassVar[str] = "instagram"

    def __init__(self, candidates: list[CandidateVideo] | None = None) -> None:
        self.calls: list[list[str]] = []
        self._candidates = candidates if candidates is not None else []

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        self.calls.append(list(keywords))
        return list(self._candidates)


class _FakeSignalRepo(KeywordSignalRepository):
    def __init__(self) -> None:
        self.saved: list[KeywordSignal] = []

    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int:
        self.saved.extend(signals)
        return len(signals)

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]:
        return []


class _FakeSegmentsWithKeywords:
    """Diferente de FakeRepos().segments: devolve keywords/platforms reais
    pro guard.admit ter o que filtrar e pro loop social bater com `socials`."""

    async def get_by_slug(self, slug: str) -> Segment:
        return Segment(id=1, slug=slug, name=slug, platforms=["instagram"], keywords=["vela"])

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        raise NotImplementedError


def _make_deps(lock: FakeLock | None = None) -> tuple[ResearchDeps, _FakeWeb, _FakeSignalRepo]:
    repos = FakeRepos()
    web = _FakeWeb()
    signals = _FakeSignalRepo()
    deps = ResearchDeps(
        web=web,
        trends=_FakeTrends(),
        socials={"instagram": _FakeSocial()},
        signals=signals,
        trend_observations=FakeTrendObservations(),
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=repos.runs,
        lock=lock or FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    return deps, web, signals


@pytest.mark.asyncio
async def test_run_research_cycle_orchestrates_web_trends_social() -> None:
    deps, web, signals = _make_deps()
    social = deps.socials["instagram"]
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    assert out.counts["web_found"] == 1
    assert out.counts["trends_found"] == 1
    assert out.counts["signals_saved"] == 2  # web + trends
    assert isinstance(social, _FakeSocial) and social.calls  # social chamado com keywords
    assert signals.saved and signals.saved[0].source == "web_suggest"
    assert web.calls == [["vela"]]


@pytest.mark.asyncio
async def test_run_research_cycle_respects_lock() -> None:
    deps, _, _ = _make_deps(lock=FakeLock(acquired=False))
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is False
    assert out.counts.get("ja_em_execucao") is True


@pytest.mark.asyncio
async def test_run_research_cycle_releases_lock_even_on_success() -> None:
    lock = FakeLock()
    deps, _, _ = _make_deps(lock=lock)
    await run_research_cycle("candles", DepthConfig(), deps)
    assert lock.released == ["research:candles"]


class _ManualClock:
    """Relógio manual: só avança quando o teste manda."""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def advance_ms(self, ms: int) -> None:
        self._now += timedelta(milliseconds=ms)


class _TimedSocial:
    platform: ClassVar[str] = "instagram"

    def __init__(self, clock: _ManualClock, ms: int) -> None:
        self._clock = clock
        self._ms = ms

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        _ = hashtags, depth
        self._clock.advance_ms(self._ms)
        return [
            CandidateVideo(
                platform="instagram",
                platform_video_id="ig-1",
                url="https://example.invalid/ig-1",
                creator_handle="stub",
                title="reel",
            )
        ]


@pytest.mark.asyncio
async def test_research_run_duration_covers_network_plus_persistence() -> None:
    """Etapa 0 Task 6: started_at abre a fase de rede (antes do acquire).

    Com clock fake que só avança em discover_by_keywords, a duração cobre a
    rede — comparável ao baseline congelado, como em collect().
    """
    clock = _ManualClock()
    repos = FakeRepos()
    runs = FakeRuns()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={"instagram": _TimedSocial(clock, 200)},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
        clock=clock,
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    social_runs = [r for r in runs.recorded if r.collector == "research:instagram"]
    assert len(social_runs) == 1
    run = social_runs[0]
    assert run.finished_at is not None
    assert run.finished_at - run.started_at == timedelta(milliseconds=200)


@pytest.mark.asyncio
async def test_run_research_cycle_dedupes_seeds_via_guard() -> None:
    """Seed repetida (mesmo casefold) não gera 2 chamadas a web.expand."""

    class _DupSegments:
        async def get_by_slug(self, slug: str) -> Segment:
            return Segment(
                id=1,
                slug=slug,
                name=slug,
                platforms=["instagram"],
                keywords=["Vela", "vela", "VELA"],
            )

        async def upsert(self, cfg: SegmentConfig) -> Segment:
            raise NotImplementedError

    repos = FakeRepos()
    web = _FakeWeb()
    deps = ResearchDeps(
        web=web,
        trends=_FakeTrends(),
        socials={},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_DupSegments(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    assert web.calls == [["Vela"]]  # as duas variantes deduplicadas pelo guard


@pytest.mark.asyncio
async def test_run_research_cycle_persists_social_candidates() -> None:
    """F2: candidatos de discover_by_keywords chegam a upsert_candidates."""
    cands = [
        CandidateVideo(
            platform="instagram",
            platform_video_id=f"ig{i}",
            url=f"https://instagram.com/reel/ig{i}",
            creator_handle="u",
            views=100,
            likes=10,
        )
        for i in range(2)
    ]
    repos = FakeRepos()
    web = _FakeWeb()
    deps = ResearchDeps(
        web=web,
        trends=_FakeTrends(),
        socials={"instagram": _FakeSocial(cands)},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)

    # upsert chamado com os 2 candidatos
    assert isinstance(repos.videos, FakeVideos)
    assert repos.videos.upserts == [(1, "instagram", 2)]
    # contadores do vocabulário collect()
    assert out.counts["social_found"] == 2
    assert out.counts["social_inserted"] == 2
    # CollectRun auditável com prefixo research:
    assert isinstance(repos.runs, FakeRuns)
    runs = repos.runs.recorded
    assert len(runs) == 1
    assert runs[0].collector == "research:instagram"
    assert runs[0].status == "ok"
    assert runs[0].found == 2
    assert runs[0].inserted == 2


@pytest.mark.asyncio
async def test_run_research_cycle_social_failure_records_failed_run() -> None:
    """Social que levanta CollectorError não aborta o ciclo; grava run failed."""

    class _BoomSocial:
        platform: ClassVar[str] = "instagram"

        async def discover_by_keywords(
            self, keywords: list[str], hashtags: list[str], depth: int
        ) -> list[CandidateVideo]:
            from content_factory.domain.errors import CollectorError

            raise CollectorError("blocked", "instagram", "rate limit")

    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={"instagram": _BoomSocial()},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is False  # falha TOTAL da única plataforma social configurada
    assert out.partial is False  # nada foi inserido/atualizado
    assert out.counts.get("instagram_failed") == 1
    assert isinstance(repos.runs, FakeRuns)
    assert repos.runs.recorded[0].status == "failed"
    assert repos.runs.recorded[0].error_code == "blocked"
    assert repos.runs.recorded[0].failed == 1  # Fase H: explícito no campo numérico
    assert repos.runs.recorded[0].skipped == 0


@pytest.mark.asyncio
async def test_run_research_cycle_partial_when_one_of_two_social_fails() -> None:
    """1 plataforma falha, outra insere candidato → ok=False, partial=True."""

    class _BoomSocial:
        platform: ClassVar[str] = "tiktok"

        async def discover_by_keywords(
            self, keywords: list[str], hashtags: list[str], depth: int
        ) -> list[CandidateVideo]:
            from content_factory.domain.errors import CollectorError

            raise CollectorError("blocked", "tiktok", "rate limit")

    cands = [
        CandidateVideo(
            platform="instagram",
            platform_video_id="ig1",
            url="https://instagram.com/reel/ig1",
            creator_handle="u",
        )
    ]

    class _SegmentsWithTwoPlatforms:
        async def get_by_slug(self, slug: str) -> Segment:
            return Segment(
                id=1,
                slug=slug,
                name=slug,
                platforms=["instagram", "tiktok"],
                keywords=["vela"],
            )

        async def upsert(self, cfg: SegmentConfig) -> Segment:
            raise NotImplementedError

    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={"instagram": _FakeSocial(cands), "tiktok": _BoomSocial()},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_SegmentsWithTwoPlatforms(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is False
    assert out.partial is True
    assert out.counts["social_inserted"] == 1
    assert out.counts.get("tiktok_failed") == 1


@pytest.mark.asyncio
async def test_run_research_cycle_ok_when_no_social_platform_configured() -> None:
    """Segmento sem rede social configurada: ausência de coleta social não é falha."""
    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_FakeSegmentsWithKeywords(),  # platforms=["instagram"], mas deps.socials={}
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    assert out.partial is False


@pytest.mark.asyncio
async def test_run_research_cycle_records_trend_observations() -> None:
    """F3: TrendPoints do Google Trends são gravados em trend_observations."""
    repos = FakeRepos()
    trend_obs = FakeTrendObservations()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={},
        signals=_FakeSignalRepo(),
        trend_observations=trend_obs,
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    await run_research_cycle("candles", DepthConfig(), deps)
    # _FakeWeb devolve 1 sinal "vela relacionada" → _FakeTrends devolve 1 TrendPoint
    assert len(trend_obs.recorded) == 1
    assert trend_obs.recorded[0].keyword == "vela relacionada"
    assert trend_obs.recorded[0].interest == 50.0


class _FakeGatedSocial:
    """Incrementa gate_hits durante discover_by_keywords (como instagram.py
    faz ao cair no fallback do gate #10) — mesmo padrão de
    `tests/app/test_collect.py::_FakeGatedCollector`, aqui pro caminho de
    pesquisa por keyword, que até agora não lia esse contador."""

    platform: ClassVar[str] = "instagram"

    def __init__(self) -> None:
        self.gate_hits = 0

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        self.gate_hits += 2
        record_gate_hit(2)
        return []


def _tracked_social(
    platform: str,
    tracker: ConcurrencyTracker,
    calls: list[list[str]],
    candidates: list[CandidateVideo] | None = None,
    delay: float = 0.0,
    fail: Exception | None = None,
) -> SocialDiscoverer:
    """Social instrumentado: mede sobreposição real de discover_by_keywords.

    Mesmo idioma de `tests/stubs.TrackedCollector` (classe com
    `platform: ClassVar` presa por closure, para satisfazer o Protocol),
    mas no protocolo `SocialDiscoverer`. `delay` simula I/O de rede; `fail`
    levanta no lugar de devolver candidatos; `calls` registra as keywords
    recebidas por plataforma.
    """
    name = platform
    cands = list(candidates) if candidates is not None else []
    wait = delay
    error = fail

    class Impl:
        platform: ClassVar[str] = name

        async def discover_by_keywords(
            self, keywords: list[str], hashtags: list[str], depth: int
        ) -> list[CandidateVideo]:
            tracker.current += 1
            tracker.peak = max(tracker.peak, tracker.current)
            tracker.entered.append(name)
            try:
                if wait:
                    await asyncio.sleep(wait)
                if error is not None:
                    raise error
                calls.append(list(keywords))
                return list(cands)
            finally:
                tracker.current -= 1
                tracker.exited.append(name)

    return Impl()


class _SegmentsWithPlatforms:
    """Segmento com plataformas e keywords configuráveis (fan-out multi-rede)."""

    def __init__(self, platforms: list[str], keywords: list[str] | None = None) -> None:
        self._platforms = list(platforms)
        self._keywords = list(keywords) if keywords is not None else ["vela"]

    async def get_by_slug(self, slug: str) -> Segment:
        return Segment(
            id=1,
            slug=slug,
            name=slug,
            platforms=list(self._platforms),
            keywords=list(self._keywords),
        )

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        raise NotImplementedError


class _RankedWeb:
    """Devolve N sinais com pesos distintos — o guard trunca pelos maiores."""

    platform: ClassVar[str] = "web_suggest"

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
        return [
            KeywordSignal(keyword=f"kw{i}", source=self.platform, weight=w, depth=depth + 1)
            for i, w in enumerate([0.1, 0.9, 0.5])
        ]


@pytest.mark.asyncio
async def test_research_social_persist_order_follows_socials_not_completion() -> None:
    """Task 8: persistência determinística na ordem de deps.socials.

    A primeira rede é lenta (termina por último na rede); runs e upserts
    precisam sair na ordem declarada — diffs de dados estáveis.
    """
    tracker = ConcurrencyTracker()
    socials: dict[str, SocialDiscoverer] = {
        "slow": _tracked_social("slow", tracker, [], None, 0.05),
        "fast1": _tracked_social("fast1", tracker, []),
        "fast2": _tracked_social("fast2", tracker, []),
    }
    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials=socials,
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_SegmentsWithPlatforms(["slow", "fast1", "fast2"]),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    assert isinstance(repos.runs, FakeRuns)
    assert [r.collector for r in repos.runs.recorded] == [
        "research:slow",
        "research:fast1",
        "research:fast2",
    ]
    assert isinstance(repos.videos, FakeVideos)
    assert [u[1] for u in repos.videos.upserts] == ["slow", "fast1", "fast2"]


@pytest.mark.asyncio
async def test_research_partial_failure_matches_sequential_path() -> None:
    """Task 8: falha parcial sob concorrência == caminho sequencial.

    Mesmas redes, 1 com CollectorError: counts e runs (status/error_code/
    failed) idênticos com max_concurrent_discoveries=2 e =1 (série via
    semáforo — o caminho sequencial).
    """

    async def _run(max_concurrency: int) -> tuple[ResearchOutcome, list[CollectRun]]:
        tracker = ConcurrencyTracker()
        cands = [
            CandidateVideo(
                platform="instagram",
                platform_video_id="ig1",
                url="https://instagram.com/reel/ig1",
                creator_handle="u",
            )
        ]
        repos = FakeRepos()
        deps = ResearchDeps(
            web=_FakeWeb(),
            trends=_FakeTrends(),
            socials={
                "instagram": _tracked_social("instagram", tracker, [], cands),
                "tiktok": _tracked_social(
                    "tiktok", tracker, [], None, 0.0, CollectorError("blocked", "tiktok")
                ),
            },
            signals=_FakeSignalRepo(),
            trend_observations=FakeTrendObservations(),
            segments=_SegmentsWithPlatforms(["instagram", "tiktok"]),
            videos=repos.videos,
            runs=repos.runs,
            lock=FakeLock(),
            limiter=FakeLimiter(),
            guard=FanoutGuard(DepthConfig()),
            max_concurrent_discoveries=max_concurrency,
        )
        out = await run_research_cycle("candles", DepthConfig(), deps)
        assert isinstance(repos.runs, FakeRuns)
        return out, list(repos.runs.recorded)

    def _sig(runs: list[CollectRun]) -> list[tuple[str, str | None, str | None, int]]:
        return [(r.collector, r.status, r.error_code, r.failed) for r in runs]

    concurrent, concurrent_runs = await _run(2)
    sequential, sequential_runs = await _run(1)
    assert concurrent.ok is False and concurrent.partial is True
    assert concurrent.counts == sequential.counts
    assert _sig(concurrent_runs) == _sig(sequential_runs)


@pytest.mark.asyncio
async def test_research_respects_max_concurrent_discoveries() -> None:
    """Task 8: pico de discover_by_keywords simultâneos == 2 (nem série, nem livre).

    3 redes com I/O de 50ms: o semáforo deixa 2 sobrepostas (prova que há
    concorrência real — falharia com pico 1 no código sequencial) e nunca 3.
    """
    tracker = ConcurrencyTracker()
    socials: dict[str, SocialDiscoverer] = {
        f"p{i}": _tracked_social(f"p{i}", tracker, [], None, 0.05) for i in range(3)
    }
    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials=socials,
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_SegmentsWithPlatforms(["p0", "p1", "p2"]),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    assert tracker.peak == 2


@pytest.mark.asyncio
async def test_research_guard_cuts_fanout_at_same_point() -> None:
    """Task 8: FanoutGuard corta no mesmo ponto com e sem concorrência.

    admit/truncate acontecem ANTES do TaskGroup (o guard não é task-safe:
    `_seen`/`_visited_count` mutáveis) — então as redes recebem keywords
    idênticas com max_concurrent_discoveries=1 e =2.
    """
    cfg = DepthConfig(fanout_per_stage={"web": 2}, max_nodes_per_cycle=50)

    async def _run(max_concurrency: int) -> tuple[ResearchOutcome, dict[str, list[list[str]]]]:
        tracker = ConcurrencyTracker()
        calls: dict[str, list[list[str]]] = {"instagram": [], "tiktok": []}
        repos = FakeRepos()
        deps = ResearchDeps(
            web=_RankedWeb(),
            trends=_FakeTrends(),
            socials={
                name: _tracked_social(name, tracker, calls[name])
                for name in ("instagram", "tiktok")
            },
            signals=_FakeSignalRepo(),
            trend_observations=FakeTrendObservations(),
            segments=_SegmentsWithPlatforms(["instagram", "tiktok"]),
            videos=repos.videos,
            runs=repos.runs,
            lock=FakeLock(),
            limiter=FakeLimiter(),
            guard=FanoutGuard(cfg),
            max_concurrent_discoveries=max_concurrency,
        )
        return await run_research_cycle("candles", cfg, deps), calls

    concurrent, concurrent_calls = await _run(2)
    sequential, sequential_calls = await _run(1)
    # web devolveu 3 sinais, fanout_per_stage cortou para os 2 de maior peso
    assert concurrent.counts["web_found"] == 2
    assert concurrent.counts == sequential.counts
    assert concurrent_calls == sequential_calls
    # as duas redes recebem as MESMAS keywords filtradas (2 web + 2 trends)
    assert concurrent_calls["instagram"] == concurrent_calls["tiktok"]
    assert len(concurrent_calls["instagram"][0]) == 4


@pytest.mark.asyncio
async def test_research_unexpected_social_error_becomes_failed_run_and_continues() -> None:
    """Semântica atual preservada: QUALQUER exceção por rede (não só
    CollectorError) vira CollectRun failed e NÃO aborta as irmãs."""
    tracker = ConcurrencyTracker()
    cands = [
        CandidateVideo(
            platform="instagram",
            platform_video_id="ig1",
            url="https://instagram.com/reel/ig1",
            creator_handle="u",
        )
    ]
    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={
            "instagram": _tracked_social("instagram", tracker, [], cands),
            "tiktok": _tracked_social("tiktok", tracker, [], None, 0.0, ValueError("boom")),
        },
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_SegmentsWithPlatforms(["instagram", "tiktok"]),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
        max_concurrent_discoveries=2,
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is False
    assert out.partial is True  # instagram inseriu 1 candidato
    assert out.counts.get("tiktok_failed") == 1
    assert out.counts["social_inserted"] == 1
    assert isinstance(repos.runs, FakeRuns)
    by_collector = {r.collector: r for r in repos.runs.recorded}
    assert by_collector["research:tiktok"].status == "failed"
    assert by_collector["research:tiktok"].error_code == "internal_error"
    assert by_collector["research:tiktok"].failed == 1
    assert by_collector["research:instagram"].status == "ok"


def _research_deps_with(
    socials: dict[str, SocialDiscoverer], repos: FakeRepos | None = None
) -> ResearchDeps:
    repos = repos or FakeRepos()
    return ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials=socials,
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_SegmentsWithPlatforms(list(socials)),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )


async def test_research_unexpected_social_error_logs_cause(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Etapa 0 Task 8.1: exceção fora do contrato vai para o log com traceback."""
    tracker = ConcurrencyTracker()
    deps = _research_deps_with(
        {"tiktok": _tracked_social("tiktok", tracker, [], None, 0.0, ValueError("boom"))}
    )
    with caplog.at_level(logging.ERROR, logger="content_factory"):
        await run_research_cycle("candles", DepthConfig(), deps)
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(records) == 1
    assert "candles" in records[0].getMessage() and "tiktok" in records[0].getMessage()
    assert records[0].exc_info is not None and isinstance(records[0].exc_info[1], ValueError)


async def test_research_contract_social_error_is_not_logged_as_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracker = ConcurrencyTracker()
    blocked = CollectorError("blocked", "tiktok", "gate")
    deps = _research_deps_with(
        {"tiktok": _tracked_social("tiktok", tracker, [], None, 0.0, blocked)}
    )
    with caplog.at_level(logging.ERROR, logger="content_factory"):
        await run_research_cycle("candles", DepthConfig(), deps)
    assert [r for r in caplog.records if r.levelno == logging.ERROR] == []


class _BoomVideos(FakeVideos):
    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats:
        raise KeyError("boom-upsert")


async def test_research_infra_error_in_persistence_propagates() -> None:
    """Etapa 1 (pendência do /code-review): erro fora do contrato no upsert PROPAGA.

    Erro de dado já é isolado por savepoint dentro do repo; o que escapa é
    infraestrutura (ex.: conexão) e deixa a sessão inutilizável — seguir para
    a próxima plataforma levantava `PendingRollbackError` no `runs.record`,
    mascarando a causa. Paridade com collect()/score(): sobe, o worker loga
    com traceback e reverte a UoW do segmento. O lock é liberado.
    """
    tracker = ConcurrencyTracker()
    cand = CandidateVideo(
        platform="tiktok", platform_video_id="t1", url="https://t.invalid/1", creator_handle="u"
    )
    repos = FakeRepos()
    repos.videos = _BoomVideos()
    deps = _research_deps_with({"tiktok": _tracked_social("tiktok", tracker, [], [cand])}, repos)
    with pytest.raises(KeyError, match="boom-upsert"):
        await run_research_cycle("candles", DepthConfig(), deps)
    assert isinstance(deps.lock, FakeLock)
    assert deps.lock.released == ["research:candles"]


async def test_research_contract_error_in_persistence_becomes_failed_run() -> None:
    """`CollectorError` na persistência continua virando run failed (contrato)."""

    class _BlockedVideos(FakeVideos):
        async def upsert_candidates(
            self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
        ) -> UpsertStats:
            raise CollectorError("blocked", "tiktok", "gate")

    tracker = ConcurrencyTracker()
    cand = CandidateVideo(
        platform="tiktok", platform_video_id="t1", url="https://t.invalid/1", creator_handle="u"
    )
    repos = FakeRepos()
    repos.videos = _BlockedVideos()
    deps = _research_deps_with({"tiktok": _tracked_social("tiktok", tracker, [], [cand])}, repos)
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.counts.get("tiktok_failed") == 1


@pytest.mark.asyncio
async def test_run_research_cycle_exposes_gate_hits_counter() -> None:
    """Sem isso, o envelope da pesquisa por keyword não distingue busca
    ampla por hashtag do fallback de conta própria (gate #10) do Instagram."""
    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={"instagram": _FakeGatedSocial()},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.counts["instagram_gate_hits"] == 2


class _SharedGatedSocial:
    """Etapa 1 Task 9: singleton com 1 hit por chamada e yield no meio."""

    platform: ClassVar[str] = "instagram"

    def __init__(self) -> None:
        self.gate_hits = 0

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        from content_factory.app.gate_hits import record_gate_hit

        self.gate_hits += 1
        record_gate_hit()
        await asyncio.sleep(0.01)
        return []


async def test_research_concurrent_segments_count_own_gate_hits() -> None:
    shared = _SharedGatedSocial()
    a, b = await asyncio.gather(
        run_research_cycle("velas", DepthConfig(), _research_deps_with({"instagram": shared})),
        run_research_cycle("sabonetes", DepthConfig(), _research_deps_with({"instagram": shared})),
    )
    assert a.counts["instagram_gate_hits"] == 1
    assert b.counts["instagram_gate_hits"] == 1


class _PoisonedRuns(FakeRuns):
    """Sessão quebrada: depois de um erro de infra, qualquer escrita levanta."""

    async def record(self, run: CollectRun) -> int:
        raise RuntimeError("PendingRollbackError simulada")


async def test_research_infra_error_is_not_masked_by_run_record() -> None:
    """O `finally` gravava a run na sessão quebrada e o erro dele mascarava o real."""
    tracker = ConcurrencyTracker()
    cand = CandidateVideo(
        platform="tiktok", platform_video_id="t1", url="https://t.invalid/1", creator_handle="u"
    )
    repos = FakeRepos()
    repos.videos = _BoomVideos()
    repos.runs = _PoisonedRuns()
    deps = _research_deps_with({"tiktok": _tracked_social("tiktok", tracker, [], [cand])}, repos)
    with pytest.raises(KeyError, match="boom-upsert"):
        await run_research_cycle("candles", DepthConfig(), deps)
