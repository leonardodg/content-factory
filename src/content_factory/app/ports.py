from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, ClassVar, Protocol, Self

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

if TYPE_CHECKING:
    from content_factory.app.collect import CollectDeps
    from content_factory.app.research_pipeline import ResearchDeps
    from content_factory.app.score import ScoreDeps


class UnitOfWork(Protocol):
    """Sessão isolada por unidade de trabalho: 1 segmento = 1 UoW = 1 transação.

    O engine é compartilhado (task-safe); a sessão nunca é (AsyncSession não
    é task-safe). Implementada por `infra.db.base.SessionUnitOfWork`. Na
    Task 10 os jobs ainda abrem 1 UoW por segmento em loop sequencial; a
    Task 11 executa as UoWs sob `TaskGroup`.
    """

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


@dataclass
class SegmentDepsBundle:
    """Deps com escopo de 1 segmento, nascidas da mesma UoW (Task 10).

    Repositórios (`collect`/`score`/`research`) ligam a sessão da UoW;
    o resto (collectors, limiter, lock, providers) continua compartilhado.
    O caller commita via `uow` (1 commit por segmento, no job).
    """

    uow: UnitOfWork
    collect: CollectDeps
    score: ScoreDeps
    research: ResearchDeps


class SegmentDepsFactory(Protocol):
    """Fábrica de bundles por segmento a partir de UoWs próprias (Task 10/11).

    Implementada por `AppDeps` (worker). Cada chamada abre uma UoW própria;
    `async with factory.create_segment_deps(segment) as bundle` delimita a
    transação do segmento.
    """

    def create_segment_deps(
        self, segment: Segment
    ) -> AbstractAsyncContextManager[SegmentDepsBundle]: ...


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

    async def timing_stats(
        self, segment_id: int, since: datetime
    ) -> dict[str, dict[str, float | int | None]]:
        """Agregado de discover_ms/persist_ms por plataforma (baseline Task 4).

        Janela do `cf collect-timings`: `{plataforma: {n, avg/p95 de cada fase}}`.
        NULLs (runs pré-instrumentação) são ignorados nas médias.
        """
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
