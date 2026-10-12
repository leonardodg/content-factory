from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.app.ports import (
    CollectRunRepository,
    KeywordSignalRepository,
    ResearchCampaignRepository,
    ScoreRepository,
    SegmentRepository,
    VideoRepository,
)
from content_factory.domain.errors import SegmentNotFound
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    KeywordSignal,
    ResearchCampaign,
    ScoreResult,
    Segment,
    SegmentConfig,
    UpsertStats,
    VideoMetrics,
    VideoSnapshot,
)
from content_factory.infra.db.orm import (
    CollectRunRow,
    KeywordSignalRow,
    ResearchCampaignRow,
    SegmentRow,
    VideoMetricsRow,
    VideoRow,
    VideoScoreRow,
    VideoSegmentRow,
)


def _snapshot(video: VideoRow, m: VideoMetricsRow | None) -> VideoSnapshot:
    """LEFT JOIN sem match vira `None` ⇒ métricas zeradas (vídeo ainda não capturado)."""
    metrics = (
        VideoMetrics(
            views=m.views or 0,
            likes=m.likes or 0,
            comments=m.comments or 0,
            shares=m.shares or 0,
            saves=m.saves or 0,
        )
        if m is not None
        else VideoMetrics()
    )
    return VideoSnapshot(
        video_id=video.id,
        title=video.title,
        description=video.description,
        published_at=video.published_at,
        metrics=metrics,
    )


class VideoRepositoryImpl(VideoRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats:
        stats = UpsertStats()
        for c in candidates:
            # SAVEPOINT por candidato (precedente T16): um INSERT quebrado
            # (ex.: id > VARCHAR(64) no playtest real) envenenaria a
            # transação inteira e o runs.record do finally do collect
            # mascararia o erro original (REVISÃO §6 #1). Só o candidato
            # doente é revertido; erros que não são de dados do candidato
            # (ex.: conexão) seguem propagando.
            try:
                async with self._s.begin_nested():
                    stmt = pg_insert(VideoRow).values(
                        platform=c.platform,
                        platform_video_id=c.platform_video_id,
                        url=c.url,
                        title=c.title,
                        description=c.description,
                        published_at=c.published_at,
                        duration_s=c.duration_s,
                        thumbnail_url=c.thumbnail_url,
                        raw=c.raw,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["platform", "platform_video_id"],
                        set_={
                            "title": stmt.excluded.title,
                            "last_collected_at": stmt.excluded.last_collected_at,
                        },
                    )
                    # xmax = 0 ⇔ linha inserida; ≠ 0 ⇔ conflito atualizou (rowcount é 1
                    # nos dois casos, então não dá pra contar por rowcount)
                    res = await self._s.execute(
                        stmt.returning(VideoRow.id, text("(xmax = 0) AS inserted"))
                    )
                    row = res.one()
                    video_id: int = row[0]
                    is_new = row[1]
                    link = pg_insert(VideoSegmentRow).values(
                        video_id=video_id,
                        segment_id=segment_id,
                        discovery_source=source,
                    )
                    link = link.on_conflict_do_update(
                        index_elements=["video_id", "segment_id"],
                        set_={"discovery_source": link.excluded.discovery_source},
                    )
                    await self._s.execute(link)
                if is_new:
                    stats.inserted += 1
                else:
                    stats.updated += 1
            except DataError, IntegrityError:
                stats.skipped += 1
        return stats

    async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
        """1 snapshot por vídeo do segmento com a métrica mais recente.

        `DISTINCT ON (video_id) ORDER BY video_id, captured_at DESC` — Postgres
        escolhe a captura mais nova por vídeo. Vídeo sem captura em
        `video_metrics` entra com métricas zeradas (LEFT JOIN): quem foi
        vinculado pelo `cf collect` é pontuado mesmo antes da primeira coleta
        de métricas.
        """
        stmt = (
            select(VideoRow, VideoMetricsRow)
            .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoRow.id)
            .outerjoin(VideoMetricsRow, VideoMetricsRow.video_id == VideoRow.id)
            .where(VideoSegmentRow.segment_id == segment_id)
            .ext(distinct_on(VideoRow.id))
            .order_by(VideoRow.id, VideoMetricsRow.captured_at.desc())
        )
        rows = (await self._s.execute(stmt)).all()
        return [_snapshot(video, m) for video, m in rows]


class SegmentRepositoryImpl(SegmentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_slug(self, slug: str) -> Segment:
        row = (
            await self._s.execute(select(SegmentRow).where(SegmentRow.slug == slug))
        ).scalar_one_or_none()
        if row is None:
            raise SegmentNotFound(slug)
        cfg = SegmentConfig.model_validate(row.config)
        return Segment(id=row.id, **cfg.model_dump())

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        stmt = pg_insert(SegmentRow).values(slug=cfg.slug, name=cfg.name, config=cfg.model_dump())
        stmt = stmt.on_conflict_do_update(
            index_elements=["slug"],
            set_={"name": stmt.excluded.name, "config": stmt.excluded.config},
        )
        row = (await self._s.execute(stmt.returning(SegmentRow))).scalar_one()
        return Segment(id=row.id, **cfg.model_dump())


class CollectRunRepositoryImpl(CollectRunRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, run: CollectRun) -> int:
        row = CollectRunRow(**run.model_dump())
        self._s.add(row)
        await self._s.flush()
        return row.id

    async def status_counts(self, segment_id: int, since: datetime) -> dict[str, int]:
        stmt = (
            select(CollectRunRow.status, func.count(CollectRunRow.id))
            .where(CollectRunRow.segment_id == segment_id, CollectRunRow.started_at > since)
            .group_by(CollectRunRow.status)
        )
        rows = (await self._s.execute(stmt)).all()
        return {status: count for status, count in rows}


class ScoreRepositoryImpl(ScoreRepository):
    """`video_scores` é timeseries: 1 linha por captura (append, sem ON CONFLICT)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self, video_id: int, segment_id: int, result: ScoreResult, computed_at: datetime
    ) -> None:
        # SAVEPOINT por vídeo: no Postgres um flush quebrado envenena a
        # transação inteira — sem begin_nested o try/except do use-case
        # sobreviveria, mas o commit final levantaria PendingRollbackError
        # e reverteria o lote todo. Fica aqui (não no use-case): a porta
        # continua "upsert de um vídeo" sem vazar transação para app/.
        async with self._s.begin_nested():
            self._s.add(
                VideoScoreRow(
                    video_id=video_id,
                    segment_id=segment_id,
                    computed_at=computed_at,
                    viral_score=result.total,
                    performance=result.performance,
                    velocity=result.velocity,
                    engagement=result.engagement,
                    relevance=result.relevance,
                    recency=result.recency,
                    trend_decay=result.trend_decay,
                )
            )
            await self._s.flush()


class KeywordSignalRepositoryImpl(KeywordSignalRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int:
        saved = 0
        for sig in signals:
            stmt = pg_insert(KeywordSignalRow).values(
                segment_id=segment_id,
                keyword=sig.keyword,
                source=sig.source,
                weight=sig.weight,
                depth=sig.depth,
                created_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["segment_id", "keyword", "source"],
                set_={"weight": stmt.excluded.weight, "depth": stmt.excluded.depth},
            )
            await self._s.execute(stmt)
            saved += 1
        return saved

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]:
        stmt = select(KeywordSignalRow).where(KeywordSignalRow.segment_id == segment_id)
        if source is not None:
            stmt = stmt.where(KeywordSignalRow.source == source)
        stmt = stmt.order_by(KeywordSignalRow.weight.desc()).limit(limit)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            KeywordSignal(keyword=r.keyword, source=r.source, weight=r.weight, depth=r.depth)
            for r in rows
        ]


def _campaign_model(row: ResearchCampaignRow) -> ResearchCampaign:
    return ResearchCampaign(
        id=row.id,
        segment_id=row.segment_id,
        started_at=row.started_at,
        ends_at=row.ends_at,
        active=row.active,
    )


class ResearchCampaignRepositoryImpl(ResearchCampaignRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign:
        row = ResearchCampaignRow(
            segment_id=segment_id,
            started_at=datetime.now(UTC),
            ends_at=ends_at,
            active=True,
        )
        self._s.add(row)
        await self._s.flush()
        return _campaign_model(row)

    async def stop(self, segment_id: int) -> None:
        stmt = (
            update(ResearchCampaignRow)
            .where(ResearchCampaignRow.segment_id == segment_id, ResearchCampaignRow.active)
            .values(active=False)
        )
        await self._s.execute(stmt)

    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
        stmt = (
            select(ResearchCampaignRow)
            .where(
                ResearchCampaignRow.segment_id == segment_id,
                ResearchCampaignRow.active,
                ResearchCampaignRow.ends_at > datetime.now(UTC),
            )
            .order_by(ResearchCampaignRow.started_at.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _campaign_model(row) if row is not None else None
