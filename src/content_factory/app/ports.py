from collections.abc import Sequence
from typing import ClassVar, Protocol

from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Segment,
    SegmentConfig,
    UpsertStats,
)


class Collector(Protocol):
    platform: ClassVar[str]

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]: ...

    # aceita SegmentConfig (e não Segment): coletores não precisam do id —
    # o id só entra no upsert, via VideoRepository.upsert_candidates


class VideoRepository(Protocol):
    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats: ...


class SegmentRepository(Protocol):
    async def get_by_slug(self, slug: str) -> Segment: ...
    async def upsert(self, cfg: SegmentConfig) -> Segment: ...


class CollectRunRepository(Protocol):
    async def record(self, run: CollectRun) -> int: ...


class RateLimiter(Protocol):
    async def acquire(self, platform: str) -> None: ...  # bloqueia até liberar


class Lock(Protocol):
    async def acquire_once(self, key: str) -> bool: ...  # False se já em execução
    async def release(self, key: str) -> None: ...  # SEM isso o lock fica eterno
