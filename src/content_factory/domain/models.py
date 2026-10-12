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
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0


class VideoSnapshot(BaseModel):
    """Vídeo + sua métrica mais recente — entrada do use-case de score."""

    video_id: int
    title: str = ""
    description: str = ""
    published_at: datetime | None = None
    metrics: VideoMetrics


class ScoreResult(BaseModel):
    total: float
    performance: float
    velocity: float
    engagement: float
    relevance: float
    recency: float
    trend_decay: str


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
    failed: int = 0
    quota_used: int = 0
    retries: int = 0
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
