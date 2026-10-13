from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar, Protocol

from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    KeywordSignal,
    ResearchCampaign,
    ScoreResult,
    Segment,
    SegmentConfig,
    TrendPoint,
    UpsertStats,
    VideoSnapshot,
)


class Collector(Protocol):
    platform: ClassVar[str]

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]: ...

    # aceita SegmentConfig (e não Segment): coletores não precisam do id —
    # o id só entra no upsert, via VideoRepository.upsert_candidates


class KeywordExpander(Protocol):
    """Stage de descoberta de keywords relacionadas (ex. web search suggest)."""

    platform: ClassVar[str]

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]: ...


class TrendsProvider(Protocol):
    """Métrica de série temporal por keyword (ex. Google Trends)."""

    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]: ...


class SocialDiscoverer(Protocol):
    """Descoberta orientada a keyword/hashtag numa rede social (IG/YT/TikTok)."""

    platform: ClassVar[str]

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]: ...


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


class KeywordSignalRepository(Protocol):
    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int: ...

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]: ...


class CollectRunRepository(Protocol):
    async def record(self, run: CollectRun) -> int: ...
    async def status_counts(self, segment_id: int, since: datetime) -> dict[str, int]:
        """Contagem de runs por status a partir de `since` (janela do `cf status`)."""
        ...


class ResearchCampaignRepository(Protocol):
    async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign: ...
    async def stop(self, segment_id: int) -> None: ...
    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
        """Campanha ativa e não vencida (ends_at > now) do segmento, se houver."""
        ...

    async def active_segments(self) -> list[Segment]:
        """Todos os segmentos com campanha ativa e não vencida (ends_at > now).

        Fonte única de "quais segmentos o worker deve processar neste
        tick" — vale pros 3 jobs (collect/score/research), não só pesquisa.
        """
        ...


class TrendObservationRepository(Protocol):
    async def record_many(self, segment_id: int, points: Sequence[TrendPoint]) -> int: ...

    async def history_for_keyword(
        self, segment_id: int, keyword: str, limit: int
    ) -> list[TrendPoint]:
        """Mais recentes primeiro — base para calcular crescimento na Fase G."""
        ...


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
