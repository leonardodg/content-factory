from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import ClassVar, Literal, Protocol

from content_factory.domain.kw_rank import SuggestHit
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Discovery,
    KeywordSignal,
    MetricCoverage,
    RefreshResult,
    RemediationTarget,
    ResearchCampaign,
    ScoreResult,
    SearchQuery,
    SearchQueryRun,
    Segment,
    SegmentConfig,
    SegmentTerm,
    TermTrendDay,
    TrendPoint,
    UpsertStats,
    VideoMetrics,
    VideoSnapshot,
    WebPageScoreRow,
    WebSearchHit,
)
from content_factory.domain.web_relevance import WebPageInput


class UnitOfWork(Protocol):
    """Transação de 1 unidade de trabalho: 1 segmento = 1 UoW = 1 transação.

    O engine é compartilhado (task-safe); a sessão nunca é (AsyncSession não
    é task-safe). Implementada por `infra.db.base.SessionUnitOfWork`. Só
    `commit`/`rollback` (ISP, Etapa 2 Task 13): o escopo — abrir, reverter
    em exceção e fechar — é de `infra.db.base.session_scope`.
    """

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class Collector(Protocol):
    platform: ClassVar[str]

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]: ...

    # aceita SegmentConfig (e não Segment): coletores não precisam do id —
    # o id só entra no upsert, via VideoRepository.upsert_candidates


class KeywordExpander(Protocol):
    """Descoberta de sugestões relacionadas (Google Suggest)."""

    platform: ClassVar[str]

    async def expand(self, seeds: list[str], depth: int) -> list[SuggestHit]: ...


class TermSearcher(Protocol):
    """Busca por UM termo, com atribuição (qual termo/posição trouxe o vídeo) — R6.

    Capacidade separada do `Collector`: Instagram/TikTok/web não precisam
    implementá-la. Hoje só o YouTube.
    """

    platform: ClassVar[str]

    async def search_term(
        self, segment: SegmentConfig, query: SearchQuery, limit: int
    ) -> list[Discovery]: ...


class RefreshableCollector(Protocol):
    """R7: re-mede vídeos já conhecidos por id (só o YouTube implementa).

    Porta separada do `Collector`: Instagram (mídia própria) e TikTok não
    precisam implementá-la nem nos fakes. `refresh` devolve o que voltou do
    `videos.list` e os ids que a plataforma não devolveu (removido/privado).
    """

    platform: ClassVar[str]

    async def refresh(self, targets: Sequence[RemediationTarget]) -> RefreshResult: ...


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


class RemediationRepository(Protocol):
    """R7: alvos da remedição, gravação de métrica e marcação de removidos.

    Deliberadamente separada de `VideoRepository`: a remedição **não** usa
    `upsert_candidates` (que sobrescreveria metadados e `discovery_source`),
    grava só `video_metrics`.
    """

    async def remediation_targets(
        self,
        segment_id: int,
        since_days: int,
        limit: int,
        min_gap_hours: int,
        platforms: Sequence[str],
    ) -> list[RemediationTarget]:
        """Vídeos do segmento descobertos nos últimos `since_days`, sem medição
        nas últimas `min_gap_hours` e que não estejam marcados como removidos."""
        ...

    async def record_metrics(
        self, target: RemediationTarget, metrics: VideoMetrics, followers_at_capture: int | None
    ) -> None: ...

    async def mark_removed(self, video_ids: Sequence[int]) -> int: ...


@dataclass(frozen=True)
class ScoreUpsertRow:
    """Uma linha de `video_scores` a persistir em lote (Task 13).

    `video_scores` é append-only (sem ON CONFLICT): o lote é direto. O
    use-case calcula todos os `ScoreResult` (puro) e monta as linhas; o
    impl persiste em blocos com fallback por linha. `result`/`computed_at`
    vêm de domain (`ScoreResult`/`datetime`).
    """

    video_id: int
    segment_id: int
    result: ScoreResult
    computed_at: datetime


@dataclass
class UpsertReport:
    """Resultado de `upsert_many`: o que persistiu e o que caiu, por categoria.

    Antes a porta devolvia só um `int` e o repo descartava linha ruim em
    silêncio — o diagnóstico (R5) exigia saber **quanto** caiu e **por quê**.
    `failures` é categoria → nº de linhas (ex.: `{"data_error": 1}`), nunca
    mensagem: envelope fica só com contagens, o detalhe vai para o log.
    `persisted + sum(failures.values()) == len(rows)` no caminho do impl
    (linhas descartadas são exatamente as de erro de dado).
    """

    persisted: int
    failures: dict[str, int] = field(default_factory=dict[str, int])


class ScoreRepository(Protocol):
    async def upsert(
        self, video_id: int, segment_id: int, result: ScoreResult, computed_at: datetime
    ) -> None:
        """Upsert de um vídeo — mantido para a CLI/uso direto pontual."""
        ...

    async def upsert_many(self, rows: Sequence[ScoreUpsertRow]) -> UpsertReport:
        """Persiste o lote, isolando linhas ruins (fallback por linha).

        Devolve o relatório (persistidas + falhas por categoria); linhas com
        dado inválido são puladas sem derrubar o lote. Erros que não são de
        dados da linha (ex.: conexão) propagam para o caller.
        """
        ...


class WebHitRepository(Protocol):
    """Grava a proveniência dos resultados web (F1), inclusive descartados."""

    async def record(self, segment_id: int, hits: Sequence[WebSearchHit]) -> int: ...


class WebScoreRepository(Protocol):
    """Leitura/escrita do experimento web próprio (`web_page_scores`, hits).

    Não toca `video_scores`: a web tem score e tabela próprios (F3). O score
    novo só chega ao worker sob `WEB_SCORE_ENABLED` (F5).
    """

    async def pages_for_segment(
        self, segment_id: int, keywords: list[str], *, until: datetime | None = None
    ) -> list[WebPageInput]:
        """Páginas web do segmento, SEM as de anúncio (inclusive as antigas).

        `until` corta os hits em `collected_at <= until` (avaliação na data da
        amostra, reprodutível).
        """
        ...

    async def append_scores(self, rows: Sequence[WebPageScoreRow]) -> int: ...

    async def hits_for_segment(
        self, segment_id: int, *, until: datetime | None = None
    ) -> list[WebSearchHit]: ...


class NoOpWebHitRepository:
    """Default de `CollectDeps` quando o chamador não grava hits (testes, uso avulso)."""

    async def record(self, segment_id: int, hits: Sequence[WebSearchHit]) -> int:
        _ = segment_id
        return len(hits)


class SegmentRepository(Protocol):
    async def get_by_slug(self, slug: str) -> Segment: ...
    async def upsert(self, cfg: SegmentConfig) -> Segment: ...


class KeywordSignalRepository(Protocol):
    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int: ...

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]: ...

    async def record_days(
        self, segment_id: int, keywords: Sequence[str], source: str, day: date
    ) -> int:
        """Dias distintos em que cada termo do Suggest apareceu (R2). Append-only."""
        ...

    async def days_seen(
        self, segment_id: int, source: str, days: int, today: date
    ) -> dict[str, int]:
        """`{termo: nº de dias distintos}` na janela `[today-days+1, today]` (R2)."""
        ...


class SegmentTermRepository(Protocol):
    """Pool de termos do segmento (R4) — fonte única das buscas."""

    async def upsert_many(self, segment_id: int, terms: Sequence[SegmentTerm]) -> int: ...

    async def top_by_origin(
        self,
        segment_id: int,
        origin: str,
        limit: int,
        order_by: Literal["kw_rank", "trend_score", "engagement_score"] = "kw_rank",
    ) -> list[SegmentTerm]: ...

    async def counts_by_origin(self, segment_id: int) -> dict[str, int]: ...

    async def all_for_segment(
        self, segment_id: int, status: str | None = None
    ) -> list[SegmentTerm]: ...

    async def set_status(
        self, segment_id: int, origin: str, keep: Sequence[str], reason: str
    ) -> int:
        """Marca `excluded` (com `reason`) os termos ativos da origem fora de `keep`."""
        ...

    async def set_trend_scores(self, segment_id: int, scores: Mapping[str, float | None]) -> int:
        """R3: grava `trend_score` nas linhas `suggest` existentes (não cria termo,
        não mexe em `last_seen_at`, que significa "visto no Suggest")."""
        ...


class TermTrendRepository(Protocol):
    """R3: histórico do `trend_score` por termo e dia (`term_trend_days`)."""

    async def record_many(self, segment_id: int, rows: Sequence[TermTrendDay]) -> int: ...


class NoOpTermTrendRepository:
    """Default de `ResearchDeps` quando o chamador não grava o histórico (testes)."""

    async def record_many(self, segment_id: int, rows: Sequence[TermTrendDay]) -> int:
        _ = segment_id
        return len(rows)


class NoOpSegmentTermRepository:
    """Default de `ResearchDeps` quando o chamador não grava o pool (testes)."""

    async def upsert_many(self, segment_id: int, terms: Sequence[SegmentTerm]) -> int:
        _ = segment_id
        return len(terms)

    async def top_by_origin(
        self,
        segment_id: int,
        origin: str,
        limit: int,
        order_by: Literal["kw_rank", "trend_score", "engagement_score"] = "kw_rank",
    ) -> list[SegmentTerm]:
        _ = segment_id, origin, limit, order_by
        return []

    async def counts_by_origin(self, segment_id: int) -> dict[str, int]:
        _ = segment_id
        return {}

    async def all_for_segment(
        self, segment_id: int, status: str | None = None
    ) -> list[SegmentTerm]:
        _ = segment_id, status
        return []

    async def set_status(
        self, segment_id: int, origin: str, keep: Sequence[str], reason: str
    ) -> int:
        _ = segment_id, origin, keep, reason
        return 0

    async def set_trend_scores(self, segment_id: int, scores: Mapping[str, float | None]) -> int:
        _ = segment_id
        return len(scores)


class DiscoveryRepository(Protocol):
    """R6: grava qual termo/posição trouxe cada vídeo (uma linha por busca)."""

    async def record(self, segment_id: int, discoveries: Sequence[Discovery]) -> int:
        """Devolve as linhas gravadas; descoberta de vídeo não persistido é pulada."""
        ...


class SearchQueryRepository(Protocol):
    """R13: log de cada busca executada, inclusive as vazias e as que falharam."""

    async def record_many(self, segment_id: int, runs: Sequence[SearchQueryRun]) -> int: ...


class NoOpDiscoveryRepository:
    """Default de `CollectDeps` quando o chamador não grava atribuição (testes)."""

    async def record(self, segment_id: int, discoveries: Sequence[Discovery]) -> int:
        _ = segment_id
        return len(discoveries)


class NoOpSearchQueryRepository:
    """Default de `CollectDeps` quando o chamador não grava o log (testes)."""

    async def record_many(self, segment_id: int, runs: Sequence[SearchQueryRun]) -> int:
        _ = segment_id
        return len(runs)


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

    async def runs_since(self, segment_id: int, since: datetime) -> list[CollectRun]:
        """Runs do segmento iniciadas depois de `since`, em ordem de início (C2)."""
        ...


class JobRunReader(Protocol):
    """Última execução de cada job do cron — catch-up no startup do worker (T2)."""

    async def last_runs(self, segment_ids: list[int]) -> dict[str, datetime | None]:
        """`{"research", "collect", "score"}` → início da run mais recente entre os segmentos.

        research/collect saem de `collect_runs` (prefixo `research:` separa um
        do outro); score sai de `video_scores.computed_at`. `None` = nunca rodou.
        """
        ...


class OpsReportReader(Protocol):
    """Leituras agregadas do `cf ops-report` (C2) — só Postgres."""

    async def metric_coverage(
        self, segment_id: int, since: datetime | None = None
    ) -> dict[str, MetricCoverage]:
        """Por plataforma: vídeos, com views no último snapshot e com ≥ 2 snapshots.

        Sem `since`: todo o histórico do segmento. Com `since`: só vídeos com
        snapshot capturado depois de `since` (cobertura da janela).
        """
        ...

    async def scored_since(self, segment_id: int, since: datetime) -> int:
        """Scores calculados para o segmento depois de `since`."""
        ...

    async def discard_stats(self, segment_id: int, since: datetime) -> dict[str, int]:
        """Descartes web por motivo (`web_search_hits.discard_reason`) na janela (F0)."""
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


class PlatformHealth(Protocol):
    """Breaker por plataforma (spec §7): N falhas seguidas → `blocked` no acquire.

    Segregada de `RateLimiter` (ISP): quem só limita não precisa registrar
    saúde. Implementada por `infra.ratelimit.RedisRateLimiter` (chaves
    `cf:cb:*`, globais entre segmentos).
    """

    async def record_failure(self, platform: str) -> None: ...
    async def record_success(self, platform: str) -> None: ...


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
