"""Etapa 1 Task 9: gate_hits contado POR CHAMADA, não por instância singleton."""

import asyncio
from typing import ClassVar

from tests.stubs import FakeLimiter, FakeLock, FakeRepos

from content_factory.app.collect import CollectDeps, collect
from content_factory.app.gate_hits import counting, record_gate_hit
from content_factory.domain.models import CandidateVideo, SegmentConfig


def test_record_without_active_counter_is_noop() -> None:
    record_gate_hit()  # fora de `counting()`: não levanta, não conta


def test_counting_sums_hits_in_scope() -> None:
    with counting() as hits:
        record_gate_hit()
        record_gate_hit(2)
    assert hits.total == 3


def test_nested_counting_is_isolated() -> None:
    with counting() as outer:
        record_gate_hit()
        with counting() as inner:
            record_gate_hit(5)
        record_gate_hit()
    assert inner.total == 5
    assert outer.total == 2


async def test_concurrent_tasks_count_independently() -> None:
    async def _one(n: int) -> int:
        with counting() as hits:
            for _ in range(n):
                record_gate_hit()
                await asyncio.sleep(0)
        return hits.total

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
