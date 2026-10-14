"""Contadores por chamada (gate_hits — Etapa 1 T9; quota_used — revisão B2)."""

import asyncio
from typing import ClassVar

from tests.stubs import FakeLimiter, FakeLock, FakeRepos, FakeRuns

from content_factory.app.call_counters import counting, record_gate_hit, record_quota
from content_factory.app.collect import CollectDeps, collect
from content_factory.domain.models import CandidateVideo, SegmentConfig


def test_record_without_active_counter_is_noop() -> None:
    record_gate_hit()  # fora de `counting()`: não levanta, não conta


def test_counting_sums_hits_in_scope() -> None:
    with counting() as hits:
        record_gate_hit()
        record_gate_hit(2)
    assert hits.gate_hits == 3


def test_nested_counting_is_isolated() -> None:
    with counting() as outer:
        record_gate_hit()
        with counting() as inner:
            record_gate_hit(5)
        record_gate_hit()
    assert inner.gate_hits == 5
    assert outer.gate_hits == 2


async def test_concurrent_tasks_count_independently() -> None:
    async def _one(n: int) -> int:
        with counting() as hits:
            for _ in range(n):
                record_gate_hit()
                await asyncio.sleep(0)
        return hits.gate_hits

    assert await asyncio.gather(_one(1), _one(3), _one(2)) == [1, 3, 2]


class _SharedGatedCollector:
    """Singleton do registry: 1 hit por chamada, com yield no meio (intercala)."""

    platform: ClassVar[str] = "instagram"

    def __init__(self) -> None:
        self.gate_hits = 0

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        self.gate_hits += 1
        record_gate_hit()
        await asyncio.sleep(0.01)
        return []


async def test_two_concurrent_segments_each_count_their_own_hit() -> None:
    """Antes: delta before/after do atributo somava o hit do outro segmento (2/2)."""
    shared = _SharedGatedCollector()

    def _deps() -> CollectDeps:
        repos = FakeRepos()
        return CollectDeps(
            collectors=[shared],
            videos=repos.videos,
            segments=repos.segments,
            runs=repos.runs,
            limiter=FakeLimiter(),
            lock=FakeLock(),
        )

    a, b = await asyncio.gather(
        collect("velas", ["instagram"], limit=5, deps=_deps()),
        collect("sabonetes", ["instagram"], limit=5, deps=_deps()),
    )
    assert a.counts["instagram_gate_hits"] == 1
    assert b.counts["instagram_gate_hits"] == 1
    assert shared.gate_hits == 2  # o atributo do singleton segue acumulando


def test_quota_is_counted_separately_from_gate_hits() -> None:
    with counting() as counters:
        record_quota(100)
        record_quota(1)
        record_gate_hit()
    assert counters.quota_used == 101
    assert counters.gate_hits == 1


class _QuotaCollector:
    """Como o YouTube: 1 search (100) + 2 lists (1+1) por discover."""

    platform: ClassVar[str] = "youtube"

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        record_quota(100)
        await asyncio.sleep(0.01)  # intercala com o outro segmento
        record_quota(1)
        record_quota(1)
        return []


async def test_collect_run_records_quota_of_its_own_call() -> None:
    """R6: collect_runs.quota_used era sempre 0 (o singleton acumulava e ninguém lia)."""
    shared = _QuotaCollector()

    def _deps() -> tuple[CollectDeps, FakeRepos]:
        repos = FakeRepos()
        deps = CollectDeps(
            collectors=[shared],
            videos=repos.videos,
            segments=repos.segments,
            runs=repos.runs,
            limiter=FakeLimiter(),
            lock=FakeLock(),
        )
        return deps, repos

    (deps_a, repos_a), (deps_b, _) = _deps(), _deps()
    a, b = await asyncio.gather(
        collect("velas", ["youtube"], 5, deps_a), collect("sabonetes", ["youtube"], 5, deps_b)
    )
    assert [r.quota_used for r in a.runs] == [102]
    assert [r.quota_used for r in b.runs] == [102]
    assert isinstance(repos_a.runs, FakeRuns)
    assert repos_a.runs.recorded[0].quota_used == 102
