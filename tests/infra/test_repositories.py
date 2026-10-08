import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from content_factory.domain.models import CandidateVideo, CollectRun, SegmentConfig
from content_factory.infra.db.orm import CollectRunRow, VideoSegmentRow
from content_factory.infra.db.repositories import (
    CollectRunRepositoryImpl,
    SegmentRepositoryImpl,
    VideoRepositoryImpl,
)
from tests.conftest import requires_infra

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def candidate(i: int) -> CandidateVideo:
    # mesmo vídeo duas vezes = teste de idempotência; sufixo único protege
    # contra linhas deixadas por execuções anteriores da suíte
    return CandidateVideo(
        platform="youtube",
        platform_video_id=f"vid{i}-{uuid4().hex[:12]}",
        url=f"https://youtu.be/vid{i}",
        creator_handle="u",
    )


@requires_infra
async def test_upsert_is_idempotent() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        # FK: video_segments.segment_id exige o segmento existir
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug="teste-upsert",
                name="Teste upsert",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        c = candidate(1)
        repo = VideoRepositoryImpl(s)
        first = await repo.upsert_candidates(seg.id, "youtube", [c])
        second = await repo.upsert_candidates(seg.id, "youtube", [c])
        assert first.inserted >= 1 and second.updated >= 1
        # roundtrip: o segmento upsertado volta completo pelo get_by_slug
        seg2 = await SegmentRepositoryImpl(s).get_by_slug("teste-upsert")
        assert seg2.id == seg.id and seg2.name == "Teste upsert"
        # vínculo video_segments gravado com a fonte da descoberta
        link = (
            await s.execute(select(VideoSegmentRow).where(VideoSegmentRow.segment_id == seg.id))
        ).scalar_one_or_none()
        assert link is not None and link.discovery_source == "youtube"
    await engine.dispose()


@requires_infra
async def test_segment_not_found_raises() -> None:
    from content_factory.domain.errors import SegmentNotFound

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        with pytest.raises(SegmentNotFound):
            await SegmentRepositoryImpl(s).get_by_slug("nao-existe")
    await engine.dispose()


@requires_infra
async def test_collect_run_record_persists_and_returns_id() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        run_id = await CollectRunRepositoryImpl(s).record(
            CollectRun(collector="youtube", started_at=datetime.now(UTC))
        )
        assert run_id > 0
        row = (
            await s.execute(select(CollectRunRow).where(CollectRunRow.id == run_id))
        ).scalar_one()
        assert row.collector == "youtube"
        assert row.status == "running"
        assert row.found == 0
    await engine.dispose()
