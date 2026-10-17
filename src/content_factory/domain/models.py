from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchConfig(BaseModel):
    """Busca do YouTube: idioma, região e ordenação (`relevanceLanguage`,
    `regionCode`, `order`). `None` = padrão (sem restrição, `viewCount`).

    Busca real 2026-10-15 (resina): com `order=viewCount` o YouTube ignora o
    idioma (1/10 vídeos em PT); com `relevance`, 9/10. Idioma só tem efeito
    com `order=relevance`. Não é filtro rígido de idioma.
    """

    relevance_language: str | None = None
    region_code: str | None = None
    order: Literal["viewCount", "relevance", "date", "rating"] | None = None

    def is_empty(self) -> bool:
        return not (self.relevance_language or self.region_code or self.order)


class SegmentConfig(BaseModel):
    slug: str
    name: str
    languages: list[str] = Field(default_factory=lambda: ["pt-BR"])
    platforms: list[str]
    content_types: list[str] = Field(default_factory=list)
    keywords: list[str]
    # termos-cabeca para sondar o Suggest pela intencao (opcao (a) da revisao
    # 16/10). Vazio = sem sondas. Ver `domain/suggest_probes.py`.
    probe_terms: list[str] = Field(default_factory=list)
    # R1: âncoras do nicho (ex.: vela, pavio, cera). Frase sem nenhuma âncora
    # é descartada. Vazio = checagem de âncora desligada para o segmento.
    anchor_terms: list[str] = Field(default_factory=list)
    # R1: lista curta de bloqueio por segmento (marcas/termos fora do nicho).
    blocked_terms: list[str] = Field(default_factory=list)
    subnichos: dict[str, list[str]] = Field(default_factory=dict)
    audience: list[str] = Field(default_factory=list)  # vai p/ segments.config JSONB
    research: DepthConfig | None = None  # guard-rail do pipeline de pesquisa
    search: SearchConfig | None = None  # idioma/região da busca (YAML)


class Segment(SegmentConfig):
    id: int


class CandidateVideo(BaseModel):
    """DTO de fronteira: o que todo collector devolve (validado na entrada)."""

    platform: str
    platform_video_id: str
    url: str
    creator_handle: str
    creator_followers: int | None = None
    # id do canal na plataforma (YouTube: channelId) — base do vínculo com
    # `creators` (R8). None = a plataforma não expôs o id.
    creator_platform_id: str | None = None
    title: str = ""
    description: str = ""
    published_at: datetime | None = None
    duration_s: int | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    thumbnail_url: str | None = None
    # idioma declarado pelo autor (ex.: "pt-BR"); None = não declarado
    language: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class VideoMetrics(BaseModel):
    """Métricas de um vídeo — `None` = métrica AUSENTE (não reportada),
    `0` = métrica reportada como zero. Fase G: scoring não penaliza ausência."""

    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None


class VideoSnapshot(BaseModel):
    """Vídeo + sua métrica mais recente — entrada do use-case de score."""

    video_id: int
    title: str = ""
    description: str = ""
    published_at: datetime | None = None
    metrics: VideoMetrics
    captured_at: datetime | None = None  # quando `metrics` foi capturada
    previous_metrics: VideoMetrics | None = None  # captura anterior, se existir
    previous_captured_at: datetime | None = None


class ScoreResult(BaseModel):
    """Resultado do scoring — `None` em performance/velocity/engagement = ausente."""

    total: float
    performance: float | None = None
    velocity: float | None = None  # Fase H: None = sem snapshot comparável
    engagement: float | None = None
    relevance: float
    recency: float
    trend_decay: str
    metrics_available: list[str] = Field(default_factory=list[str])


class WebSearchHit(BaseModel):
    """F1 — uma linha de `web_search_hits` (inclui descartados pelo filtro).

    `provider="unknown"` enquanto o `ddgs` agregar engines; `status` diz se a
    página entrou em `videos` (`kept`) ou por que foi descartada.
    """

    segment_id: int | None = None
    video_id: int | None = None
    collected_at: datetime | None = None
    query: str
    query_id: str
    query_intent: str
    provider: str = "unknown"
    provider_result_position: int
    url_original: str
    canonical_url: str
    status: str  # kept|discarded_ad|duplicate
    discard_reason: str | None = None
    collector_version: str
    rules_version: str
    title: str = ""
    snippet: str = ""


class WebPageScoreRow(BaseModel):
    """Uma linha de `web_page_scores` (append-only) a persistir."""

    video_id: int
    segment_id: int
    computed_at: datetime
    score: float | None
    score_version: str
    components: dict[str, float | None] = Field(default_factory=dict[str, float | None])
    explanation: dict[str, Any] = Field(default_factory=dict[str, Any])


class UpsertStats(BaseModel):
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


class KeywordSignal(BaseModel):
    """Descoberta de keyword/hashtag de uma etapa do pipeline de pesquisa."""

    keyword: str
    source: str  # "web_suggest" | "trends" | "instagram_hashtag" | ...
    weight: float = 0.0
    depth: int = 0


class TrendPoint(BaseModel):
    """Métrica de série temporal de uma keyword (Google Trends ou equivalente)."""

    keyword: str
    period_start: datetime
    period_granularity: str  # "week" | "month"
    interest: float


class DepthConfig(BaseModel):
    """Guard-rail de profundidade/fan-out do ciclo de pesquisa."""

    max_depth: int = 2
    fanout_per_stage: dict[str, int] = Field(default_factory=dict)
    max_nodes_per_cycle: int = 200
    recursion_speed_factor: float = 1.0


class CollectRun(BaseModel):
    collector: str
    segment_id: int | None = None
    status: str = "running"  # running|ok|partial|failed|blocked
    found: int = 0
    inserted: int = 0
    updated: int = 0
    failed: int = 0  # Fase H: só falha de plataforma (discover/upsert abortou)
    skipped: int = 0  # Fase H: candidatos ignorados pelo savepoint, run continua ok
    quota_used: int = 0
    retries: int = 0
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    discover_ms: int | None = None  # Task 4 (baseline): tempo de rede (discover)
    persist_ms: int | None = None  # Task 4 (baseline): tempo de persistência (upsert)


class MetricCoverage(BaseModel):
    """Cobertura de métricas de uma plataforma no segmento (C2 `ops-report`)."""

    videos: int
    with_views: int  # último snapshot com views (base do B3)
    with_two_snapshots: int  # ≥ 2 snapshots: dá para medir crescimento


class ResearchCampaign(BaseModel):
    """Campanha de pesquisa "rodar diariamente por N semanas" — estado de
    fim-de-campanha persistido, porque o arq só tem cron recorrente sem fim."""

    id: int
    segment_id: int
    started_at: datetime
    ends_at: datetime
    active: bool = True


class SegmentTerm(BaseModel):
    """Termo do pool do segmento (R4) — fonte única das buscas."""

    segment_id: int
    term: str
    origin: str  # seed | suggest | trends | hashtag:youtube | hashtag:instagram | hashtag:tiktok
    kw_rank: float | None = None
    trend_score: float | None = None
    engagement_score: float | None = None
    status: str = "active"  # active | excluded
    exclude_reason: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    rules_version: str = ""


class SearchQuery(BaseModel):
    """Uma busca a executar: o termo ENVIADO à API, a origem e o id do plano (R6)."""

    term: str
    origin: str
    query_id: str


class Discovery(BaseModel):
    """Vídeo encontrado por uma busca — qual termo/posição o trouxe (R6)."""

    candidate: CandidateVideo
    term: str
    origin: str
    query_id: str | None = None
    result_position: int | None
    run_kind: str  # collect


class SearchQueryRun(BaseModel):
    """Log de cada busca executada, inclusive as vazias (R6/R13)."""

    platform: str
    query_id: str
    term: str
    origin: str
    results: int
    kept: int
    quota_used: int
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime


class RemediationTarget(BaseModel):
    """R7: alvo de remedição — identificadores só, sem métrica."""

    video_id: int
    platform: str
    platform_video_id: str


class RefreshResult(BaseModel):
    """R7: o que a remedição achou e o que sumiu (removido/privado)."""

    candidates: list[CandidateVideo] = Field(default_factory=list[CandidateVideo])
    missing_ids: list[str] = Field(default_factory=list[str])
