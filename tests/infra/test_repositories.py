import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from content_factory.app.score import ScoreDeps, score
from content_factory.domain.models import CandidateVideo, CollectRun, ScoreResult, SegmentConfig
from content_factory.infra.db.orm import (
    CollectRunRow,
    VideoMetricsRow,
    VideoRow,
    VideoScoreRow,
    VideoSegmentRow,
)
from content_factory.infra.db.repositories import (
    CollectRunRepositoryImpl,
    ScoreRepositoryImpl,
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


async def video_id_of(s: AsyncSession, c: CandidateVideo) -> int:
    row = (
        await s.execute(select(VideoRow).where(VideoRow.platform_video_id == c.platform_video_id))
    ).scalar_one()
    return row.id


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
async def test_latest_for_segment_picks_newest_metric_and_zero_fills() -> None:
    """1 snapshot por vídeo do segmento; métrica mais recente vence (DISTINCT ON).

    Vídeo sem captura em video_metrics entra zerado — `cf score` precisa
    pontuar tudo que o `cf collect` vinculou ao segmento.
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug="teste-latest", name="Teste latest", platforms=["youtube"], keywords=["k"]
            )
        )
        repo = VideoRepositoryImpl(s)
        com_metricas, sem_metricas = candidate(3), candidate(4)
        await repo.upsert_candidates(seg.id, "youtube", [com_metricas, sem_metricas])
        vid = await video_id_of(s, com_metricas)
        vid_vazio = await video_id_of(s, sem_metricas)
        base = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        s.add_all(
            [
                VideoMetricsRow(video_id=vid, captured_at=base, views=10, likes=1),
                VideoMetricsRow(
                    video_id=vid, captured_at=base + timedelta(hours=1), views=9_999, likes=50
                ),
            ]
        )
        await s.flush()

        snaps = {snap.video_id: snap for snap in await repo.latest_for_segment(seg.id)}
        assert set(snaps) == {vid, vid_vazio}  # 1 linha por vídeo, inclusive sem métrica
        assert snaps[vid].metrics.views == 9_999  # captura mais recente vence a antiga
        assert snaps[vid].metrics.likes == 50
        assert snaps[vid_vazio].metrics.views == 0
    await engine.dispose()


@requires_infra
async def test_score_upsert_appends_one_row_per_capture() -> None:
    """video_scores é timeseries: 1 linha por captura, payload integral do ScoreResult."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug="teste-score", name="Teste score", platforms=["youtube"], keywords=["k"]
            )
        )
        c = candidate(5)
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "youtube", [c])
        vid = await video_id_of(s, c)
        result = ScoreResult(
            total=42.5,
            performance=10.0,
            velocity=80.0,
            engagement=20.0,
            relevance=100.0,
            recency=100.0,
            trend_decay="low",
        )
        first = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
        repo = ScoreRepositoryImpl(s)
        await repo.upsert(vid, seg.id, result, first)
        await repo.upsert(vid, seg.id, result, first + timedelta(hours=1))

        rows = (
            (
                await s.execute(
                    select(VideoScoreRow)
                    .where(VideoScoreRow.video_id == vid)
                    .order_by(VideoScoreRow.computed_at)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2  # append por captura, não sobrescreve o histórico
        assert rows[0].viral_score == 42.5
        assert rows[0].trend_decay == "low"
        assert rows[0].relevance == 100.0
        assert rows[0].segment_id == seg.id
        assert rows[0].computed_at == first  # timestamptz roundtrip consciente
    await engine.dispose()


@requires_infra
async def test_score_partial_persists_healthy_rows_when_one_insert_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SAVEPOINT por vídeo: um INSERT que falha não envenena o lote inteiro.

    Sem begin_nested no ScoreRepositoryImpl, o flush quebrado do vídeo doente
    marca a transação como failed no Postgres → o try/except do use-case
    sobrevive, mas o commit final levanta PendingRollbackError e NENHUMA
    linha persiste: o partial viraria mentira. Com savepoint, só o vídeo
    doente é revertido e os vizinhos seguem para o commit.
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                # slug único por execução: latest_for_segment veria vídeos
                # deixados por runs anteriores e os asserts globais mentiriam
                slug=f"teste-partial-{uuid4().hex[:8]}",
                name="Teste partial",
                platforms=["youtube"],
                keywords=["vela"],
            )
        )
        repo = VideoRepositoryImpl(s)
        cands = [candidate(10), candidate(11), candidate(12)]
        await repo.upsert_candidates(seg.id, "youtube", cands)
        vids = [await video_id_of(s, c) for c in cands]
        base = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
        s.add_all(
            [VideoMetricsRow(video_id=vid, captured_at=base, views=1_000, likes=10) for vid in vids]
        )
        await s.flush()

        # falha de INSERT só no vídeo do meio do lote: o objeto entra na
        # sessão, mas com segment_id nulado — o Postgres estoura NOT NULL
        # no flush (IntegrityError real, que envenena a transação)
        poison_id = vids[1]
        original_add = s.add

        def flaky_add(obj: object) -> None:
            original_add(obj)
            if isinstance(obj, VideoScoreRow) and obj.video_id == poison_id:
                cast(Any, obj).segment_id = None  # NOT NULL → IntegrityError real

        monkeypatch.setattr(s, "add", flaky_add)

        outcome = await score(
            ScoreDeps(
                videos=repo,
                scores=ScoreRepositoryImpl(s),
                segment=seg,
                keywords=seg.keywords,
            )
        )
        await s.commit()

        assert outcome.ok is False
        assert outcome.partial is True
        assert outcome.scored == 2  # os dois vizinhos saudáveis, não o doente
        rows = (
            (await s.execute(select(VideoScoreRow.id).where(VideoScoreRow.segment_id == seg.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 2  # persistiram apesar do meio do lote ter falhado
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


@requires_infra
async def test_collect_run_status_counts_last_7_days() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"status-{uuid4().hex[:8]}",
                name="Status",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        now = datetime.now(UTC)
        repo = CollectRunRepositoryImpl(s)
        for status in ("ok", "ok", "blocked"):
            await repo.record(
                CollectRun(collector="youtube", segment_id=seg.id, status=status, started_at=now)
            )
        await repo.record(
            CollectRun(
                collector="youtube",
                segment_id=seg.id,
                status="failed",
                started_at=now - timedelta(days=8),  # fora da janela de 7 dias
            )
        )
        counts = await repo.status_counts(seg.id, now - timedelta(days=7))
        assert counts == {"ok": 2, "blocked": 1}  # o failed de 8 dias fica de fora
    await engine.dispose()
