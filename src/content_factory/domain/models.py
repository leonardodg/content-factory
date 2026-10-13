from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SegmentConfig(BaseModel):
    slug: str
    name: str
    languages: list[str] = Field(default_factory=lambda: ["pt-BR"])
    platforms: list[str]
    content_types: list[str] = Field(default_factory=list)
    keywords: list[str]
    subnichos: dict[str, list[str]] = Field(default_factory=dict)
    audience: list[str] = Field(default_factory=list)  # vai p/ segments.config JSONB
    research: DepthConfig | None = None  # guard-rail do pipeline de pesquisa


class Segment(SegmentConfig):
    id: int


class CandidateVideo(BaseModel):
    """DTO de fronteira: o que todo collector devolve (validado na entrada)."""

    platform: str
    platform_video_id: str
    url: str
    creator_handle: str
    creator_followers: int | None = None
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


class ResearchCampaign(BaseModel):
    """Campanha de pesquisa "rodar diariamente por N semanas" — estado de
    fim-de-campanha persistido, porque o arq só tem cron recorrente sem fim."""

    id: int
    segment_id: int
    started_at: datetime
    ends_at: datetime
    active: bool = True
