from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
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
    performance: Mapped[float] = mapped_column(Float)
    velocity: Mapped[float] = mapped_column(Float)
    engagement: Mapped[float] = mapped_column(Float)
    relevance: Mapped[float] = mapped_column(Float)
    recency: Mapped[float] = mapped_column(Float)
    trend_decay: Mapped[str] = mapped_column(String(8))
    replication_score: Mapped[float | None] = mapped_column(Float)  # fase IA
    production_score: Mapped[float | None] = mapped_column(Float)  # fase IA


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
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32))


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
