from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SegmentRow(Base):
    __tablename__ = "segments"
    __table_args__ = (Index("ix_segments_config_gin", "config", postgresql_using="gin"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CreatorRow(Base):
    __tablename__ = "creators"
    __table_args__ = (
        UniqueConstraint("platform", "platform_creator_id"),
        Index("ix_creators_raw_gin", "raw", postgresql_using="gin"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    platform_creator_id: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(200))
    followers_count: Mapped[int | None]
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class VideoRow(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("platform", "platform_video_id"),
        Index("ix_videos_raw_gin", "raw", postgresql_using="gin"),
        Index("ix_videos_platform_published_at", "platform", "published_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    platform_video_id: Mapped[str] = mapped_column(String(64))
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"))
    # ciclo videos↔transcripts: FK diferida p/ o DDL (SQLAlchemy exige use_alter)
    transcript_id: Mapped[int | None] = mapped_column(ForeignKey("transcripts.id", use_alter=True))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int | None]
    language: Mapped[str | None] = mapped_column(String(16))
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    first_collected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_collected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    creator: Mapped[CreatorRow | None] = relationship()


class VideoSegmentRow(Base):
    __tablename__ = "video_segments"
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), primary_key=True)
    discovery_source: Mapped[str] = mapped_column(String(64))
    relevance_score: Mapped[float | None] = mapped_column(Float)
    discovered_at: Mapped[datetime] = mapped_column(server_default=func.now())


class VideoMetricsRow(Base):
    __tablename__ = "video_metrics"
    __table_args__ = (Index("ix_video_metrics_video_id_captured_at", "video_id", "captured_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    views: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    shares: Mapped[int | None]
    saves: Mapped[int | None]
    followers_at_capture: Mapped[int | None]


class VideoScoreRow(Base):
    __tablename__ = "video_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    viral_score: Mapped[float] = mapped_column(Float, index=True)
    # Fase G: None = métrica ausente no snapshot (não "zero reportado")
    performance: Mapped[float | None] = mapped_column(Float)
    # Fase H: None = sem par de snapshots comparável (momentum_score)
    velocity: Mapped[float | None] = mapped_column(Float)
    engagement: Mapped[float | None] = mapped_column(Float)
    relevance: Mapped[float] = mapped_column(Float)
    recency: Mapped[float] = mapped_column(Float)
    trend_decay: Mapped[str] = mapped_column(String(8))
    # Fase H: cobertura de métricas e versão da fórmula — auditáveis no banco
    metrics_available: Mapped[list[str]] = mapped_column(JSONB, default=list)
    formula_version: Mapped[str] = mapped_column(String(16), default="fase-h")
    replication_score: Mapped[float | None] = mapped_column(Float)  # fase IA
    production_score: Mapped[float | None] = mapped_column(Float)  # fase IA


class WebSearchHitRow(Base):
    """F1 — proveniência de cada resultado web, inclusive os descartados.

    Append-only: uma linha por resultado devolvido pelo `ddgs`. `video_id` é
    NULL quando descartado (não entra em `videos`); `status` diz por quê.
    """

    __tablename__ = "web_search_hits"
    __table_args__ = (
        Index("ix_web_search_hits_segment_collected_at", "segment_id", "collected_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    query: Mapped[str] = mapped_column(Text)
    query_id: Mapped[str] = mapped_column(String(64))
    query_intent: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(32), default="unknown")
    provider_result_position: Mapped[int] = mapped_column(Integer)
    url_original: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    discard_reason: Mapped[str | None] = mapped_column(String(64))
    collector_version: Mapped[str] = mapped_column(String(32))
    rules_version: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")


class WebPageScoreRow(Base):
    """F3 — score `web-v1` por página, com componentes e explicação. Append-only."""

    __tablename__ = "web_page_scores"
    __table_args__ = (Index("ix_web_page_scores_segment_id", "segment_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    score: Mapped[float | None] = mapped_column(Float)
    score_version: Mapped[str] = mapped_column(String(16))
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class TranscriptRow(Base):
    __tablename__ = "transcripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), unique=True)
    source: Mapped[str] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    segments: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CreativePatternRow(Base):
    __tablename__ = "creative_patterns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    psychology: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    visual_structure: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    best_for: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    origin_video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))  # fase IA


class IdeaRow(Base):
    __tablename__ = "ideas"
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey("creative_patterns.id"))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ScriptRow(Base):
    __tablename__ = "scripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"))
    title: Mapped[str] = mapped_column(Text)
    duration_s: Mapped[int | None]
    hook: Mapped[str] = mapped_column(Text, default="")
    scenes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    cta: Mapped[str] = mapped_column(Text, default="")
    production_difficulty: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProductionJobRow(Base):
    __tablename__ = "production_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    scheduled_for: Mapped[str | None] = mapped_column(String(16))  # today|tomorrow|+7
    assets: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PublishedVideoRow(Base):
    __tablename__ = "published_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"))
    platform: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PerformanceMetricRow(Base):
    __tablename__ = "performance_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    published_video_id: Mapped[int] = mapped_column(ForeignKey("published_videos.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    views: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    shares: Mapped[int | None]
    saves: Mapped[int | None]
    watch_time_s: Mapped[int | None]
    retention_pct: Mapped[float | None]
    completion_rate: Mapped[float | None]
    followers_gained: Mapped[int | None]


class CollectRunRow(Base):
    __tablename__ = "collect_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    collector: Mapped[str] = mapped_column(String(32), index=True)
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id"))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32))
    # Task 4 (baseline): NULL = run pré-instrumentação; médias ignoram NULLs
    discover_ms: Mapped[int | None]
    persist_ms: Mapped[int | None]


class KeywordSignalRow(Base):
    __tablename__ = "keyword_signals"
    __table_args__ = (
        UniqueConstraint("segment_id", "keyword", "source", name="uq_keyword_signal"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    keyword: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32))
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SegmentTermRow(Base):
    """Pool de termos do segmento (R4). Único por (segment_id, term, origin).

    A origem faz parte da chave: a mesma frase pode ser seed E sugestão (o
    Suggest devolve a própria seed), e uma hashtag pode coincidir com um termo.
    Com chave só em (segment_id, term) o upsert trocava a origem e rebaixava a
    seed (revisão QA).
    """

    __tablename__ = "segment_terms"
    __table_args__ = (
        UniqueConstraint("segment_id", "term", "origin", name="uq_segment_term"),
        Index("ix_segment_terms_segment_status", "segment_id", "status"),
        Index("ix_segment_terms_segment_origin", "segment_id", "origin"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    term: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(32))
    kw_rank: Mapped[float | None] = mapped_column(Float)
    trend_score: Mapped[float | None] = mapped_column(Float)
    engagement_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="active")
    exclude_reason: Mapped[str | None] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    rules_version: Mapped[str] = mapped_column(String(32), default="")


class KeywordSignalDayRow(Base):
    """Dias distintos em que um termo do Suggest apareceu (R2 — persistência)."""

    __tablename__ = "keyword_signal_days"
    __table_args__ = (UniqueConstraint("segment_id", "keyword", "source", "seen_on"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    keyword: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32))
    seen_on: Mapped[date] = mapped_column(Date)


class ResearchCampaignRow(Base):
    __tablename__ = "research_campaigns"
    __table_args__ = (Index("ix_research_campaigns_segment_id_active", "segment_id", "active"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TrendObservationRow(Base):
    """Série temporal do Google Trends — upsert por período (Fase H: mesmo
    período medido de novo atualiza a observação, não duplica)."""

    __tablename__ = "trend_observations"
    __table_args__ = (
        UniqueConstraint(
            "segment_id",
            "keyword",
            "period_start",
            "period_granularity",
            name="uq_trend_observation_period",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    keyword: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_granularity: Mapped[str] = mapped_column(String(8))
    interest: Mapped[float] = mapped_column(Float)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
