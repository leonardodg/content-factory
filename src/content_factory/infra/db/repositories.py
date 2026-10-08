from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.app.ports import CollectRunRepository, SegmentRepository, VideoRepository
from content_factory.domain.errors import SegmentNotFound
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Segment,
    SegmentConfig,
    UpsertStats,
)
from content_factory.infra.db.orm import CollectRunRow, SegmentRow, VideoRow, VideoSegmentRow


class VideoRepositoryImpl(VideoRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats:
        stats = UpsertStats()
        for c in candidates:
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
            res = await self._s.execute(stmt.returning(VideoRow.id, text("(xmax = 0) AS inserted")))
            row = res.one()
            video_id: int = row[0]
            if row[1]:
                stats.inserted += 1
            else:
                stats.updated += 1
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
        return stats


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
