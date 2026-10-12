from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from tests.stubs import FakeLimiter, FakeLock, FakeRepos, FakeRuns, FakeVideos

from content_factory.app.ports import KeywordSignalRepository
from content_factory.app.research_pipeline import ResearchDeps, run_research_cycle
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import (
    CandidateVideo,
    DepthConfig,
    KeywordSignal,
    Segment,
    SegmentConfig,
    TrendPoint,
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
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True  # ciclo não aborta
    assert out.counts.get("instagram_failed") == 1
    assert isinstance(repos.runs, FakeRuns)
    assert repos.runs.recorded[0].status == "failed"
    assert repos.runs.recorded[0].error_code == "blocked"
