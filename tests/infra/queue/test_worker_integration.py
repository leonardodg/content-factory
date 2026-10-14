"""UoW concorrente real: 3 segmentos via `create_app_deps` de verdade (Etapa 0 Task 8).

Mesmo roteiro executado à mão no review: infra descartável
(`TEST_DATABASE_URL`/`TEST_REDIS_URL`, nunca os containers do experimento),
`_collect_impl` fake gravando pela sessão do bundle. Só os slugs deste teste
são processados — campanhas alheias ativas no banco descartável retornam
`ok` vazio sem escrever nada.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import ClassVar, cast
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from content_factory.app.collect import CollectDeps
from content_factory.config import Settings
from content_factory.domain.models import CandidateVideo, CollectRun, SegmentConfig
from content_factory.infra.db.orm import CollectRunRow, VideoSegmentRow
from content_factory.infra.db.repositories import SegmentRepositoryImpl
from content_factory.infra.queue import worker as w
from tests.conftest import requires_infra, requires_redis

pytestmark = pytest.mark.integration


@requires_infra
@requires_redis
async def test_concurrent_segment_uows_isolate_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 segmentos concorrentes: pico 3, `-b` reverte sozinho, irmãs commitam."""
    monkeypatch.setenv("WORKER_MAX_SEGMENTS", "3")
    tag = uuid4().hex[:8]
    slugs = [f"t8-{tag}-a", f"t8-{tag}-b", f"t8-{tag}-c"]
    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        redis_url=os.environ["TEST_REDIS_URL"],
    )
    deps = await w.create_app_deps(settings)
    started_ids: list[int] = []
    try:
        async with deps.session_factory() as session:
            ids: dict[str, int] = {}
            for slug in slugs:
                seg = await SegmentRepositoryImpl(session).upsert(
                    SegmentConfig(slug=slug, name=slug, platforms=["youtube"], keywords=["k"])
                )
                ids[slug] = seg.id
            await session.commit()
        ends_at = datetime.now(UTC) + timedelta(days=7)
        for sid in ids.values():
            await deps.campaigns.start(sid, ends_at)
            started_ids.append(sid)

        state = {"current": 0, "peak": 0}

        async def fake_collect(
            slug: str, platforms: list[str], limit: int, collect_deps: object
        ) -> object:
            if slug not in ids:
                # campanha alheia no banco descartável: fora do experimento.
                class _Skipped:
                    ok = True
                    partial = False
                    counts: ClassVar[dict[str, object]] = {}

                return _Skipped()
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            try:
                bundle_collect = cast(CollectDeps, collect_deps)
                segment = await bundle_collect.segments.get_by_slug(slug)
                videos = [
                    CandidateVideo(
                        platform="youtube",
                        platform_video_id=f"{slug}-{i}",
                        url=f"https://example.invalid/{slug}-{i}",
                        creator_handle="stub",
                        title=f"video {i}",
                    )
                    for i in range(2)
                ]
                await bundle_collect.videos.upsert_candidates(segment.id, "youtube", videos)
                await bundle_collect.runs.record(
                    CollectRun(
                        collector="youtube",
                        segment_id=segment.id,
                        status="ok",
                        found=2,
                        inserted=2,
                        started_at=datetime.now(UTC),
                        finished_at=datetime.now(UTC),
                    )
                )
                await asyncio.sleep(0.2)
                if slug == slugs[1]:
                    raise RuntimeError("boom-b")
            finally:
                state["current"] -= 1

            class _Out:
                ok = True
                partial = False
                counts: ClassVar[dict[str, object]] = {"found": 2}

            return _Out()

        monkeypatch.setattr(w, "_collect_impl", fake_collect)
        out = await w.run_collect_job({"campaigns_deps": deps.campaigns, "app_deps": deps})
        assert state["peak"] == 3
        assert out["segments"][slugs[1]] == {
            "ok": False,
            "error": "boom-b",
            "error_type": "RuntimeError",
        }
        assert out["segments"][slugs[0]]["ok"] is True
        assert out["segments"][slugs[2]]["ok"] is True

        async with deps.session_factory() as session:
            link_counts = [
                (
                    await session.execute(
                        select(func.count())
                        .select_from(VideoSegmentRow)
                        .where(VideoSegmentRow.segment_id == ids[slug])
                    )
                ).scalar_one()
                for slug in slugs
            ]
            run_counts = [
                (
                    await session.execute(
                        select(func.count())
                        .select_from(CollectRunRow)
                        .where(CollectRunRow.segment_id == ids[slug])
                    )
                ).scalar_one()
                for slug in slugs
            ]
        assert link_counts == [2, 0, 2]
        assert run_counts == [1, 0, 1]
    finally:
        for sid in started_ids:
            await deps.campaigns.stop(sid)
        await deps.close()
