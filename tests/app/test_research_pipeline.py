from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from tests.stubs import FakeLimiter, FakeLock, FakeRepos

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

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        self.calls.append(list(keywords))
        return []


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
