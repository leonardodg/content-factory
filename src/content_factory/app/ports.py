from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar, Protocol

from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    ScoreResult,
    Segment,
    SegmentConfig,
    UpsertStats,
    VideoSnapshot,
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

    async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
        """1 linha por vídeo do segmento com a métrica mais recente."""
        ...


class ScoreRepository(Protocol):
    async def upsert(
        self, video_id: int, segment_id: int, result: ScoreResult, computed_at: datetime
    ) -> None: ...


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


class SsrfGuard(Protocol):
    """Guarda SSRF de uma URL — implementada em infra/ssrf.py.

    `infra.ssrf.assert_public_http` satisfaz o protocolo estruturalmente
    (função `def(url: str) -> None` que levanta `DomainError`); a composition
    root (CLI/worker) é quem injeta — `app/` nunca importa `infra/`.
    """

    def __call__(self, url: str) -> None: ...
