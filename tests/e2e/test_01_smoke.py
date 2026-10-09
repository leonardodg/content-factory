import os
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from content_factory.app.collect import CollectDeps, collect
from content_factory.app.score import ScoreDeps, score
from content_factory.app.segment import upsert_segment
from content_factory.domain.models import CandidateVideo, SegmentConfig
from content_factory.infra.db.orm import CollectRunRow, VideoRow, VideoScoreRow
from content_factory.infra.db.repositories import (
    CollectRunRepositoryImpl,
    ScoreRepositoryImpl,
    SegmentRepositoryImpl,
    VideoRepositoryImpl,
)
from tests.conftest import requires_infra

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class FakeCollectorOne:
    platform: ClassVar[str] = "youtube"

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        return [
            CandidateVideo(
                platform="youtube",
                platform_video_id="e2e1",
                url="https://youtu.be/e2e1",
                creator_handle="u",
                title="vela de soja test",
                views=10_000,
                likes=500,
            )
        ]


class AlwaysLock:
    async def acquire_once(self, key: str) -> bool:
        return True

    async def release(self, key: str) -> None:
        return None


class NoopLimiter:
    async def acquire(self, platform: str) -> None:
        return None


@requires_infra
async def test_smoke_segment_collect_score() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        # 1) segment load → id > 0
        seg = await upsert_segment(SegmentRepositoryImpl(s), Path("segments/candles.yaml"))
        assert seg.id > 0

        # 2) collect com FakeCollector → upsert real
        deps = CollectDeps(
            collectors=[FakeCollectorOne()],
            videos=VideoRepositoryImpl(s),
            segments=SegmentRepositoryImpl(s),
            runs=CollectRunRepositoryImpl(s),
            limiter=NoopLimiter(),
            lock=AlwaysLock(),
        )
        out = await collect("candles", ["youtube"], limit=5, deps=deps)
        assert out.ok and int(out.counts["inserted"]) >= 1

        # 3) score → linha em video_scores
        sdeps = ScoreDeps(
            videos=VideoRepositoryImpl(s),
            scores=ScoreRepositoryImpl(s),
            segment=seg,
            keywords=seg.keywords,
        )
        sout = await score(sdeps)
        assert sout.scored >= 1

        # 4) persistência: videos + scores + 1 run ok
        n_videos = (await s.execute(select(func.count()).select_from(VideoRow))).scalar()
        n_scores = (await s.execute(select(func.count()).select_from(VideoScoreRow))).scalar()
        runs = (
            (await s.execute(select(CollectRunRow).where(CollectRunRow.collector == "youtube")))
            .scalars()
            .all()
        )
        assert n_videos and n_videos >= 1
        assert n_scores and n_scores >= 1
        assert [r.status for r in runs][-1] == "ok"
        await s.rollback()
    await engine.dispose()
