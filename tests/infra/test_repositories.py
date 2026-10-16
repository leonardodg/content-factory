import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from content_factory.app.ports import ScoreUpsertRow
from content_factory.app.score import ScoreDeps, score
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    KeywordSignal,
    ScoreResult,
    SegmentConfig,
    TrendPoint,
    WebPageScoreRow,
    WebSearchHit,
)
from content_factory.infra.db.orm import (
    CollectRunRow,
    VideoMetricsRow,
    VideoRow,
    VideoScoreRow,
    VideoSegmentRow,
)
from content_factory.infra.db.repositories import (
    CollectRunRepositoryImpl,
    JobRunReaderImpl,
    KeywordSignalRepositoryImpl,
    OpsReportReaderImpl,
    ResearchCampaignRepositoryImpl,
    ScoreRepositoryImpl,
    ScoreSampleRepositoryImpl,
    SegmentRepositoryImpl,
    TrendObservationRepositoryImpl,
    VideoRepositoryImpl,
    WebHitRepositoryImpl,
    WebScoreRepositoryImpl,
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
async def test_upsert_candidates_skips_bad_candidate_keeps_txn_alive() -> None:
    """SAVEPOINT por candidato: id > 64 chars não envenena a transação.

    Reproduz o playtest real (2026-10-09, worker com chave de verdade):
    uma URL cheia como `platform_video_id` estourou o VARCHAR(64), o
    DataError abortava a transação inteira e o `runs.record` do `finally`
    do collect falhava em cascata — o InFailedSqlTransaction do audit
    mascarava o erro original (REVISÃO §6 #1). Com begin_nested por
    candidato, só o candidato doente é revertido: os vizinhos persistem,
    a transação segue viva e o audit do `finally` funciona.
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-skip-{uuid4().hex[:8]}",
                name="Teste skip",
                platforms=["youtube"],
                keywords=["vela"],
            )
        )
        repo = VideoRepositoryImpl(s)
        c1, c3 = candidate(21), candidate(23)
        # candidato doente no MEIO do lote: id de 100+ chars (o bug real)
        bad = CandidateVideo(
            platform="youtube",
            platform_video_id="https://example.com/" + "a" * 100,
            url="https://example.com/x",
            creator_handle="u",
        )
        stats = await repo.upsert_candidates(seg.id, "youtube", [c1, bad, c3])
        assert stats.skipped == 1 and stats.inserted == 2
        # transação viva: uma query seguinte na mesma sessão não levanta
        # InFailedSqlTransaction
        alive = (
            (
                await s.execute(
                    select(VideoRow.id).where(
                        VideoRow.platform_video_id.in_([c1.platform_video_id, c3.platform_video_id])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(alive) == 2
        await s.commit()
        # os vizinhos saudáveis persistiram apesar do doente
        async with factory() as check:
            persisted = (
                (
                    await check.execute(
                        select(VideoRow.id).where(
                            VideoRow.platform_video_id.in_(
                                [c1.platform_video_id, c3.platform_video_id]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(persisted) == 2
    await engine.dispose()


@requires_infra
async def test_latest_for_segment_picks_newest_metric_and_zero_fills() -> None:
    """1 snapshot por vídeo do segmento; métrica mais recente vence (DISTINCT ON).

    Vídeo sem captura em video_metrics entra com métricas None (Fase G:
    ausência ≠ zero) — `cf score` precisa pontuar tudo que o `cf collect`
    vinculou ao segmento, sem penalizar ausência como desempenho zero.
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
        assert snaps[vid].captured_at == base + timedelta(hours=1)
        # Fase H Task 4: captura anterior disponível pro momentum_score
        prev = snaps[vid].previous_metrics
        assert prev is not None
        assert prev.views == 10
        assert snaps[vid].previous_captured_at == base
        # vídeo com só 1 captura (ou nenhuma): sem par comparável
        assert snaps[vid_vazio].previous_metrics is None
        assert snaps[vid_vazio].metrics.views is None  # Fase G: ausente ≠ zero
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
async def test_score_upsert_accepts_none_performance_and_engagement() -> None:
    """Fase G bugfix: performance/engagement None (métrica ausente) deve persistir.

    video_scores.performance/engagement eram NOT NULL enquanto ScoreResult
    passou a aceitar None — upsert() estourava NotNullViolation para todo
    vídeo ainda sem captura em video_metrics (o caso normal entre a
    descoberta e a primeira coleta de métricas).
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-score-none-{uuid4().hex[:8]}",
                name="Teste score none",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        c = candidate(6)
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "youtube", [c])
        vid = await video_id_of(s, c)
        result = ScoreResult(
            total=35.0,
            performance=None,
            velocity=20.0,
            engagement=None,
            relevance=0.0,
            recency=50.0,
            trend_decay="none",
            metrics_available=[],
        )
        repo = ScoreRepositoryImpl(s)
        await repo.upsert(vid, seg.id, result, datetime(2026, 10, 7, 12, 0, tzinfo=UTC))

        row = (
            await s.execute(select(VideoScoreRow).where(VideoScoreRow.video_id == vid))
        ).scalar_one()
        assert row.performance is None
        assert row.engagement is None
        assert row.viral_score == 35.0
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
        # no flush (IntegrityError real, que envenena a transação).
        # Task 13: o caminho em lote usa add_all (que delega à sessão
        # síncrona e não passa pelo patch de instância de add) — por isso
        # o veneno é aplicado também em add_all; o fallback linha-a-linha
        # continua passando por add e isola a linha doente.
        poison_id = vids[1]

        def poison(obj: object) -> None:
            if isinstance(obj, VideoScoreRow) and obj.video_id == poison_id:
                cast(Any, obj).segment_id = None  # NOT NULL → IntegrityError real

        original_add = s.add

        def flaky_add(obj: object) -> None:
            original_add(obj)
            poison(obj)

        def flaky_add_all(objs: object) -> None:
            for item in cast(Any, objs):
                original_add(item)
                poison(item)

        monkeypatch.setattr(s, "add", flaky_add)
        monkeypatch.setattr(s, "add_all", flaky_add_all)

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
        assert (outcome.processed, outcome.persisted, outcome.failed) == (3, 2, 1)
        assert outcome.failures == {"data_error": 1}  # R5: categoria, não mensagem
        rows = (
            (await s.execute(select(VideoScoreRow.id).where(VideoScoreRow.segment_id == seg.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 2  # persistiram apesar do meio do lote ter falhado
    await engine.dispose()


@requires_infra
async def test_score_upsert_many_persists_batch_in_one_block() -> None:
    """Task 13: lote todo ok persiste N linhas (1 flush por bloco, não por linha)."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-batch-ok-{uuid4().hex[:8]}",
                name="Teste batch ok",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        cands = [candidate(40), candidate(41), candidate(42)]
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "youtube", cands)
        vids = [await video_id_of(s, c) for c in cands]
        now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

        def row(video_id: int) -> ScoreUpsertRow:
            return ScoreUpsertRow(
                video_id=video_id,
                segment_id=seg.id,
                result=ScoreResult(
                    total=42.5,
                    performance=10.0,
                    velocity=80.0,
                    engagement=20.0,
                    relevance=100.0,
                    recency=100.0,
                    trend_decay="low",
                ),
                computed_at=now,
            )

        report = await ScoreRepositoryImpl(s).upsert_many([row(vid) for vid in vids])
        assert report.persisted == 3
        assert report.failures == {}
        empty = await ScoreRepositoryImpl(s).upsert_many([])  # lote vazio é no-op
        assert empty.persisted == 0 and empty.failures == {}
        await s.commit()
        rows = (
            (await s.execute(select(VideoScoreRow).where(VideoScoreRow.segment_id == seg.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 3
        assert {r.video_id for r in rows} == set(vids)
    await engine.dispose()


@requires_infra
async def test_score_upsert_many_isolates_bad_row_without_poisoning_txn() -> None:
    """Task 13: 1 linha ruim no bloco ⇒ fallback linha-a-linha isola a ruim.

    A linha do meio usa um video_id inexistente: o flush do bloco estoura
    FK (IntegrityError real) e o fallback persiste as vizinhas. O rollback
    é só até o savepoint do bloco — a transação externa segue viva e o
    commit grava as saudáveis (prova contra PendingRollbackError).
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-batch-{uuid4().hex[:8]}",
                name="Teste batch",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        cands = [candidate(30), candidate(31)]
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "youtube", cands)
        vids = [await video_id_of(s, c) for c in cands]
        now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

        def row(video_id: int) -> ScoreUpsertRow:
            return ScoreUpsertRow(
                video_id=video_id,
                segment_id=seg.id,
                result=ScoreResult(
                    total=42.5,
                    performance=10.0,
                    velocity=80.0,
                    engagement=20.0,
                    relevance=100.0,
                    recency=100.0,
                    trend_decay="low",
                ),
                computed_at=now,
            )

        report = await ScoreRepositoryImpl(s).upsert_many(
            [row(vids[0]), row(2_147_483_647), row(vids[1])]
        )
        assert report.persisted == 2
        assert report.failures == {"data_error": 1}  # R5: categoria da linha descartada
        # transação externa viva: commit persiste as saudáveis
        await s.commit()
        rows = (
            (await s.execute(select(VideoScoreRow).where(VideoScoreRow.segment_id == seg.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert {r.video_id for r in rows} == set(vids)
    await engine.dispose()


@requires_infra
async def test_score_upsert_many_logs_warning_per_discarded_row(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5: cada linha descartada gera 1 WARNING com `video_id` e a classe.

    Antes o repo descartava em silêncio — o diagnóstico só existia no
    retorno. A mensagem leva id + nome da classe (`IntegrityError`), nunca
    o valor que estourou (dado bruto fica fora do log).
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-r5-{uuid4().hex[:8]}",
                name="Teste R5",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        cands = [candidate(60), candidate(61)]
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "youtube", cands)
        vids = [await video_id_of(s, c) for c in cands]
        now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

        def row(video_id: int) -> ScoreUpsertRow:
            return ScoreUpsertRow(
                video_id=video_id,
                segment_id=seg.id,
                result=ScoreResult(
                    total=42.5,
                    performance=10.0,
                    velocity=80.0,
                    engagement=20.0,
                    relevance=100.0,
                    recency=100.0,
                    trend_decay="low",
                ),
                computed_at=now,
            )

        with caplog.at_level(logging.WARNING, logger="content_factory.infra.db.repositories"):
            report = await ScoreRepositoryImpl(s).upsert_many(
                [row(vids[0]), row(2_147_483_647), row(vids[1])]
            )
        await s.rollback()

        assert report.persisted == 2
        assert report.failures == {"data_error": 1}
        warnings = [
            rec
            for rec in caplog.records
            if rec.name == "content_factory.infra.db.repositories" and rec.levelname == "WARNING"
        ]
        assert len(warnings) == 1  # 1 linha descartada ⇒ 1 WARNING
        message = warnings[0].getMessage()
        assert "2147483647" in message  # video_id da linha descartada
        assert "IntegrityError" in message  # classe da exceção, sem dado bruto
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


@requires_infra
async def test_keyword_signal_upsert_many_inserts_new_signals() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ks-{uuid4().hex[:8]}",
                name="Teste KS",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = KeywordSignalRepositoryImpl(s)
        signals = [
            KeywordSignal(keyword="vela aromatica", source="web_suggest", weight=0.8, depth=0),
            KeywordSignal(keyword="vela de soja", source="web_suggest", weight=0.6, depth=0),
        ]
        saved = await repo.upsert_many(seg.id, signals)
        assert saved == 2


@requires_infra
async def test_keyword_signal_upsert_many_dedupes_by_segment_keyword_source() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ks-{uuid4().hex[:8]}",
                name="Teste KS",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = KeywordSignalRepositoryImpl(s)
        sig = KeywordSignal(keyword="vela aromatica", source="web_suggest", weight=0.5, depth=0)
        await repo.upsert_many(seg.id, [sig])
        sig_updated = KeywordSignal(
            keyword="vela aromatica", source="web_suggest", weight=0.9, depth=0
        )
        await repo.upsert_many(seg.id, [sig_updated])  # mesmo (segment,keyword,source)
        top = await repo.top_for_segment(seg.id, source="web_suggest", limit=10)
        assert len(top) == 1  # não duplicou
        assert top[0].weight == 0.9  # peso foi atualizado


@requires_infra
async def test_keyword_signal_top_for_segment_orders_by_weight_desc() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ks-{uuid4().hex[:8]}",
                name="Teste KS",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = KeywordSignalRepositoryImpl(s)
        await repo.upsert_many(
            seg.id,
            [
                KeywordSignal(keyword="a", source="web_suggest", weight=0.1, depth=0),
                KeywordSignal(keyword="b", source="web_suggest", weight=0.9, depth=0),
            ],
        )
        top = await repo.top_for_segment(seg.id, source=None, limit=10)
        assert [s.keyword for s in top] == ["b", "a"]


@requires_infra
async def test_research_campaign_start_and_active_for_segment() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-rc-{uuid4().hex[:8]}",
                name="Teste RC",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = ResearchCampaignRepositoryImpl(s)
        ends = datetime.now(UTC) + timedelta(weeks=4)
        campaign = await repo.start(seg.id, ends)
        assert campaign.id > 0 and campaign.active is True
        active = await repo.active_for_segment(seg.id)
        assert active is not None and active.id == campaign.id
    await engine.dispose()


@requires_infra
async def test_research_campaign_expired_not_active() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-rc-{uuid4().hex[:8]}",
                name="Teste RC",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = ResearchCampaignRepositoryImpl(s)
        past = datetime.now(UTC) - timedelta(weeks=1)
        await repo.start(seg.id, past)  # vencida já na criação
        assert await repo.active_for_segment(seg.id) is None
    await engine.dispose()


@requires_infra
async def test_research_campaign_stop_removes_from_active() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-rc-{uuid4().hex[:8]}",
                name="Teste RC",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = ResearchCampaignRepositoryImpl(s)
        ends = datetime.now(UTC) + timedelta(weeks=4)
        await repo.start(seg.id, ends)
        assert await repo.active_for_segment(seg.id) is not None
        await repo.stop(seg.id)
        assert await repo.active_for_segment(seg.id) is None
    await engine.dispose()


@requires_infra
async def test_upsert_candidates_writes_video_metrics_snapshot() -> None:
    """upsert_candidates grava linha em video_metrics (bug Fase F: nunca gravava).

    Sem isso, latest_for_segment sempre cai no ramo `m is None` e devolve
    VideoMetrics() zerado — todo score rodava com views=0/likes=0.
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-metrics-{uuid4().hex[:8]}",
                name="Teste metrics",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        c = CandidateVideo(
            platform="youtube",
            platform_video_id=f"vid-metrics-{uuid4().hex[:12]}",
            url="https://youtu.be/vm",
            creator_handle="u",
            views=1000,
            likes=50,
            shares=None,  # YouTube não expõe shares — deve ficar NULL
        )
        repo = VideoRepositoryImpl(s)
        await repo.upsert_candidates(seg.id, "youtube", [c])

        # snapshot via latest_for_segment (views/likes visíveis no DTO)
        snaps = {snap.video_id: snap for snap in await repo.latest_for_segment(seg.id)}
        vid = await video_id_of(s, c)
        assert snaps[vid].metrics.views == 1000
        assert snaps[vid].metrics.likes == 50

        # shares NULL no banco (query crua — VideoMetrics colapsa None em 0,
        # correção de None-vs-zero é Fase G)
        row = (
            await s.execute(select(VideoMetricsRow).where(VideoMetricsRow.video_id == vid))
        ).scalar_one()
        assert row.views == 1000
        assert row.likes == 50
        assert row.shares is None  # não reportado → NULL, não zero
    await engine.dispose()


@requires_infra
async def test_upsert_candidates_skips_metrics_row_when_no_metric_reported() -> None:
    """Candidato sem NENHUMA métrica reportada não gera linha de metrics toda NULL."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-nometrics-{uuid4().hex[:8]}",
                name="Teste no metrics",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        c = CandidateVideo(
            platform="youtube",
            platform_video_id=f"vid-nometrics-{uuid4().hex[:12]}",
            url="https://youtu.be/vn",
            creator_handle="u",
            views=None,
            likes=None,
        )
        repo = VideoRepositoryImpl(s)
        await repo.upsert_candidates(seg.id, "youtube", [c])
        vid = await video_id_of(s, c)
        rows = (
            (await s.execute(select(VideoMetricsRow).where(VideoMetricsRow.video_id == vid)))
            .scalars()
            .all()
        )
        assert rows == []  # sem métrica reportada → sem linha
    await engine.dispose()


@requires_infra
async def test_score_upsert_persists_metrics_available_and_formula_version() -> None:
    """ScoreResult.metrics_available e formula_version são persistidos em video_scores.

    Fase H: a cobertura de métricas calculada em domain/scoring.py nunca
    chegava ao banco — video_scores não tinha a coluna, então qualquer
    análise posterior do score não conseguia distinguir "engagement=0
    porque views=0" de "engagement ausente porque views não reportada".
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-coverage-{uuid4().hex[:8]}",
                name="Teste coverage",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        c = candidate(6)
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
            metrics_available=["views", "likes"],
        )
        await ScoreRepositoryImpl(s).upsert(vid, seg.id, result, datetime.now(UTC))
        await s.commit()
        row = (
            await s.execute(select(VideoScoreRow).where(VideoScoreRow.video_id == vid))
        ).scalar_one()
        assert row.metrics_available == ["views", "likes"]
        assert row.formula_version == "fase-h"
        assert row.velocity == 80.0
    await engine.dispose()


@requires_infra
async def test_upsert_candidates_updates_metadata_on_conflict_when_new_value_present() -> None:
    """Segunda coleta com descrição/duração reais atualiza o que a primeira deixou vazio.

    Sem isso, relevance_of nunca vê a descrição real — title/description
    alimentam relevance_of (app/score.py), então um título vazio que
    nunca é corrigido deprime o score de relevância para sempre.
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-metadata-{uuid4().hex[:8]}",
                name="Teste metadata",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = VideoRepositoryImpl(s)
        c1 = candidate(7)  # title="", description="" (default), published_at=None
        await repo.upsert_candidates(seg.id, "youtube", [c1])
        vid = await video_id_of(s, c1)

        c2 = c1.model_copy(
            update={
                "title": "Como fazer vela de soja",
                "description": "Tutorial completo passo a passo",
                "duration_s": 180,
            }
        )
        await repo.upsert_candidates(seg.id, "youtube", [c2])

        row = (await s.execute(select(VideoRow).where(VideoRow.id == vid))).scalar_one()
        assert row.title == "Como fazer vela de soja"
        assert row.description == "Tutorial completo passo a passo"
        assert row.duration_s == 180
    await engine.dispose()


@requires_infra
async def test_upsert_candidates_preserves_metadata_when_new_value_is_empty() -> None:
    """Coleta posterior sem descrição não apaga a descrição já conhecida."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-preserva-{uuid4().hex[:8]}",
                name="Teste preserva",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = VideoRepositoryImpl(s)
        c1 = candidate(8).model_copy(update={"description": "descricao real"})
        await repo.upsert_candidates(seg.id, "youtube", [c1])
        vid = await video_id_of(s, c1)

        c2 = c1.model_copy(update={"description": ""})  # coleta seguinte sem descrição
        await repo.upsert_candidates(seg.id, "youtube", [c2])

        row = (await s.execute(select(VideoRow).where(VideoRow.id == vid))).scalar_one()
        assert row.description == "descricao real"  # não foi apagada
    await engine.dispose()


@requires_infra
async def test_trend_observation_record_many_upserts_same_period() -> None:
    """Reexecutar o ciclo de pesquisa na mesma semana atualiza a observação, não duplica.

    Sem isso, history_for_keyword(limit=N) conta o mesmo período 2x e
    infesta o histórico com a mesma medição repetida — distorce qualquer
    cálculo de inclinação/crescimento do interesse pela keyword.
    """
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-trend-{uuid4().hex[:8]}",
                name="Teste trend",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = TrendObservationRepositoryImpl(s)
        period = datetime(2026, 10, 1, tzinfo=UTC)
        point = TrendPoint(
            keyword="vela aromatica",
            period_start=period,
            period_granularity="week",
            interest=40.0,
        )
        await repo.record_many(seg.id, [point])
        # pytrends reconsultado no mesmo período, interesse mudou
        point_atualizado = point.model_copy(update={"interest": 65.0})
        await repo.record_many(seg.id, [point_atualizado])
        await s.commit()

        history = await repo.history_for_keyword(seg.id, "vela aromatica", limit=10)
        assert len(history) == 1  # não duplicou
        assert history[0].interest == 65.0  # valor mais recente venceu
    await engine.dispose()


@requires_infra
async def test_collect_run_record_persists_skipped_separate_from_failed() -> None:
    """CollectRun.skipped e .failed são colunas distintas no banco (Fase H)."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-skipped-run-{uuid4().hex[:8]}",
                name="Teste skipped run",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        run = CollectRun(
            collector="youtube",
            segment_id=seg.id,
            status="ok",
            found=3,
            inserted=1,
            updated=0,
            failed=0,
            skipped=2,
            started_at=datetime.now(UTC),
        )
        run_id = await CollectRunRepositoryImpl(s).record(run)
        await s.commit()
        row = (
            await s.execute(select(CollectRunRow).where(CollectRunRow.id == run_id))
        ).scalar_one()
        assert row.skipped == 2
        assert row.failed == 0
    await engine.dispose()


@requires_infra
async def test_collect_run_record_round_trips_timing_columns() -> None:
    """Task 4 (baseline): discover_ms/persist_ms fazem round-trip em collect_runs."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        run = CollectRun(
            collector="youtube",
            status="ok",
            found=3,
            inserted=3,
            discover_ms=150,
            persist_ms=40,
            started_at=datetime.now(UTC),
        )
        run_id = await CollectRunRepositoryImpl(s).record(run)
        await s.commit()
        row = (
            await s.execute(select(CollectRunRow).where(CollectRunRow.id == run_id))
        ).scalar_one()
        assert row.discover_ms == 150
        assert row.persist_ms == 40
    await engine.dispose()


@requires_infra
async def test_collect_run_timing_stats_aggregates_by_platform() -> None:
    """Task 4 (baseline): timing_stats agrega média/p95 de cada fase por plataforma."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-timings-{uuid4().hex[:8]}",
                name="Teste timings",
                platforms=["youtube", "tiktok"],
                keywords=["k"],
            )
        )
        now = datetime.now(UTC)
        repo = CollectRunRepositoryImpl(s)
        for discover_ms, persist_ms in ((100, 10), (200, 30)):
            await repo.record(
                CollectRun(
                    collector="youtube",
                    segment_id=seg.id,
                    status="ok",
                    discover_ms=discover_ms,
                    persist_ms=persist_ms,
                    started_at=now,
                )
            )
        # run pré-instrumentação: timings NULL entram na contagem, fora das médias
        await repo.record(
            CollectRun(collector="tiktok", segment_id=seg.id, status="ok", started_at=now)
        )
        stats = await repo.timing_stats(seg.id, now - timedelta(days=7))
        assert stats["youtube"]["n"] == 2
        assert stats["youtube"]["avg_discover_ms"] == 150.0
        assert stats["youtube"]["avg_persist_ms"] == 20.0
        assert stats["youtube"]["p95_discover_ms"] is not None
        assert 100 <= stats["youtube"]["p95_discover_ms"] <= 200
        assert stats["tiktok"]["n"] == 1
        assert stats["tiktok"]["avg_discover_ms"] is None
    await engine.dispose()


@requires_infra
async def test_active_segments_returns_only_segments_with_active_campaign() -> None:
    """active_segments() devolve só quem tem campanha ativa e não vencida."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        with_campaign = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ativo-{uuid4().hex[:8]}",
                name="Com campanha",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        without_campaign = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-sem-{uuid4().hex[:8]}",
                name="Sem campanha",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = ResearchCampaignRepositoryImpl(s)
        await repo.start(with_campaign.id, datetime.now(UTC) + timedelta(weeks=1))
        await s.commit()

        active = await repo.active_segments()
        active_ids = {seg.id for seg in active}
        assert with_campaign.id in active_ids
        assert without_campaign.id not in active_ids
    await engine.dispose()


@requires_infra
async def test_runs_since_returns_window_runs_in_order() -> None:
    """C2: o ops-report lê as runs da janela, em ordem de início."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ops-{uuid4().hex[:8]}", name="Ops", platforms=["web"], keywords=["k"]
            )
        )
        now = datetime.now(UTC)
        repo = CollectRunRepositoryImpl(s)
        for collector, at in (("web", 2), ("youtube", 1), ("web", 48)):
            await repo.record(
                CollectRun(
                    collector=collector,
                    segment_id=seg.id,
                    status="ok",
                    error_code=None,
                    started_at=now - timedelta(hours=at),
                    finished_at=now - timedelta(hours=at) + timedelta(seconds=3),
                )
            )
        await s.commit()
        runs = await repo.runs_since(seg.id, now - timedelta(hours=24))
        assert [r.collector for r in runs] == ["web", "youtube"]
        assert all(r.segment_id == seg.id and r.finished_at is not None for r in runs)
    await engine.dispose()


@requires_infra
async def test_ops_reader_metric_coverage_and_scored_since() -> None:
    """C2: cobertura (views no último snapshot, ≥ 2 snapshots) e scores da janela."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-cov-{uuid4().hex[:8]}",
                name="Cov",
                platforms=["youtube", "instagram"],
                keywords=["k"],
            )
        )
        videos = VideoRepositoryImpl(s)
        with_views = candidate(1).model_copy(update={"views": 100})
        no_views = candidate(2).model_copy(update={"likes": 5})
        reel = CandidateVideo(
            platform="instagram",
            platform_video_id=f"reel-{uuid4().hex[:12]}",
            url="https://www.instagram.com/reel/x/",
            creator_handle="",
            likes=3,
        )
        await videos.upsert_candidates(seg.id, "test", [with_views, no_views, reel])
        await videos.upsert_candidates(
            seg.id, "test", [with_views.model_copy(update={"views": 150})]
        )
        await s.commit()
        reader = OpsReportReaderImpl(s)
        cov = await reader.metric_coverage(seg.id)
        assert cov["youtube"].videos == 2
        assert cov["youtube"].with_views == 1
        assert cov["youtube"].with_two_snapshots == 1
        assert cov["instagram"].videos == 1 and cov["instagram"].with_views == 0

        vid = await video_id_of(s, with_views)
        s.add(
            VideoScoreRow(
                video_id=vid,
                segment_id=seg.id,
                viral_score=1.0,
                relevance=1.0,
                recency=1.0,
                trend_decay="fresh",
            )
        )
        await s.commit()
        assert await reader.scored_since(seg.id, datetime.now(UTC) - timedelta(hours=1)) == 1
        assert await reader.scored_since(seg.id, datetime.now(UTC) + timedelta(hours=1)) == 0
    await engine.dispose()


@requires_infra
async def test_ops_reader_coverage_can_be_restricted_to_window() -> None:
    """Auditoria 3, N-1: cobertura da janela conta só vídeos com snapshot na janela."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-covw-{uuid4().hex[:8]}",
                name="CovW",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        old = candidate(1).model_copy(update={"views": 10})
        new = candidate(2).model_copy(update={"views": 20})
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "test", [old, new])
        await s.commit()
        old_id = await video_id_of(s, old)
        await s.execute(
            update(VideoMetricsRow)
            .where(VideoMetricsRow.video_id == old_id)
            .values(captured_at=datetime.now(UTC) - timedelta(days=3))
        )
        await s.commit()
        reader = OpsReportReaderImpl(s)
        assert (await reader.metric_coverage(seg.id))["youtube"].videos == 2
        window = await reader.metric_coverage(seg.id, since=datetime.now(UTC) - timedelta(hours=24))
        assert window["youtube"].videos == 1 and window["youtube"].with_views == 1
    await engine.dispose()


@requires_infra
async def test_job_run_reader_last_runs_per_job_for_given_segments() -> None:
    """T2 catch-up: última run de research/collect/score, só dos segmentos pedidos."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        segs = SegmentRepositoryImpl(s)
        seg = await segs.upsert(
            SegmentConfig(
                slug=f"teste-cu-{uuid4().hex[:8]}", name="CU", platforms=["web"], keywords=["k"]
            )
        )
        other = await segs.upsert(
            SegmentConfig(
                slug=f"teste-cu-{uuid4().hex[:8]}", name="CU2", platforms=["web"], keywords=["k"]
            )
        )
        reader = JobRunReaderImpl(s)
        assert await reader.last_runs([seg.id]) == {
            "research": None,
            "collect": None,
            "score": None,
        }

        now = datetime.now(UTC).replace(microsecond=0)
        runs = CollectRunRepositoryImpl(s)
        for collector, seg_id, hours in (
            ("research:youtube", seg.id, 5),
            ("research:trends", seg.id, 3),
            ("youtube", seg.id, 2),
            ("web", seg.id, 4),
            ("youtube", other.id, 1),  # outro segmento não conta
        ):
            await runs.record(
                CollectRun(
                    collector=collector,
                    segment_id=seg_id,
                    status="ok",
                    error_code=None,
                    started_at=now - timedelta(hours=hours),
                    finished_at=now - timedelta(hours=hours) + timedelta(seconds=3),
                )
            )
        cand = candidate(1)
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "test", [cand])
        s.add(
            VideoScoreRow(
                video_id=await video_id_of(s, cand),
                segment_id=seg.id,
                computed_at=now - timedelta(minutes=30),
                viral_score=1.0,
                relevance=1.0,
                recency=1.0,
                trend_decay="low",
            )
        )
        await s.commit()

        last = await reader.last_runs([seg.id])
        assert last["research"] == now - timedelta(hours=3)
        assert last["collect"] == now - timedelta(hours=2)
        assert last["score"] == now - timedelta(minutes=30)
        assert (await reader.last_runs([]))["collect"] is None
    await engine.dispose()


@requires_infra
async def test_upsert_persists_language_and_keeps_known_one() -> None:
    """T3: `videos.language` vem do candidato; coleta sem idioma não apaga o conhecido."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-lang-{uuid4().hex[:8]}",
                name="L",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        videos = VideoRepositoryImpl(s)
        cand = candidate(1).model_copy(update={"language": "pt-BR"})
        await videos.upsert_candidates(seg.id, "test", [cand])
        await videos.upsert_candidates(seg.id, "test", [cand.model_copy(update={"language": None})])
        await s.commit()
        row = (
            await s.execute(
                select(VideoRow).where(VideoRow.platform_video_id == cand.platform_video_id)
            )
        ).scalar_one()
        assert row.language == "pt-BR"
    await engine.dispose()


@requires_infra
async def test_top_scored_for_segment_filters_by_platform() -> None:
    """T4: `cf score-sample --platform` — ranking de uma plataforma só."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ss-{uuid4().hex[:8]}",
                name="SS",
                platforms=["youtube", "instagram"],
                keywords=["k"],
            )
        )
        yt = candidate(1)
        reel = CandidateVideo(
            platform="instagram",
            platform_video_id=f"reel-{uuid4().hex[:12]}",
            url="https://www.instagram.com/reel/x/",
            creator_handle="",
        )
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "test", [yt, reel])
        for cand, total in ((yt, 40.0), (reel, 55.0)):
            s.add(
                VideoScoreRow(
                    video_id=await video_id_of(s, cand),
                    segment_id=seg.id,
                    viral_score=total,
                    relevance=0.0,
                    recency=100.0,
                    trend_decay="low",
                )
            )
        await s.commit()
        repo = ScoreSampleRepositoryImpl(s)
        both = await repo.top_scored_for_segment(seg.id, 10)
        assert [r.platform for r in both] == ["instagram", "youtube"]
        only_yt = await repo.top_scored_for_segment(seg.id, 10, platform="youtube")
        assert [(r.platform, r.total) for r in only_yt] == [("youtube", 40.0)]
    await engine.dispose()


def _web_sha1(url: str) -> str:
    import hashlib

    return hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()


def _web_candidate(canonical: str) -> CandidateVideo:
    return CandidateVideo(
        platform="web",
        platform_video_id=_web_sha1(canonical),
        url=canonical,
        creator_handle="",
        title="Como fazer vela artesanal",
        description="snippet com detalhe",
    )


def _web_hit(
    segment_id: int,
    now: datetime,
    *,
    position: int,
    status: str,
    canonical: str,
    reason: str | None = None,
) -> WebSearchHit:
    return WebSearchHit(
        segment_id=segment_id,
        collected_at=now,
        query="como fazer velas",
        query_id="q1:aprender:00",
        query_intent="aprender",
        provider="unknown",
        provider_result_position=position,
        url_original=canonical,
        canonical_url=canonical,
        status=status,
        discard_reason=reason,
        collector_version="web-c2",
        rules_version="ads-v1",
        title="resultado",
        snippet="",
    )


@requires_infra
async def test_web_repos_record_hits_provenance_and_latest_scores() -> None:
    """F1/F3: hits (inclusive descartados) → proveniência; scores append-only."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-web-{uuid4().hex[:8]}",
                name="Web",
                platforms=["web"],
                keywords=["vela"],
            )
        )
        canonical = f"https://site-{uuid4().hex[:8]}.example/vela"
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "web", [_web_candidate(canonical)])
        await s.commit()

        now = datetime.now(UTC)
        hits = WebHitRepositoryImpl(s)
        recorded = [
            _web_hit(seg.id, now, position=1, status="kept", canonical=canonical),
            _web_hit(
                seg.id,
                now,
                position=2,
                status="discarded_ad",
                canonical="https://bing.com/aclick?x=1",
                reason="ad:bing_aclick",
            ),
            _web_hit(
                seg.id,
                now,
                position=3,
                status="duplicate",
                canonical=canonical,
                reason="duplicate",
            ),
            _web_hit(seg.id, now, position=4, status="kept", canonical=canonical),
        ]
        assert await hits.record(seg.id, recorded) == 4
        await s.commit()

        # discards por motivo (o que o ops-report expõe)
        stats = await OpsReportReaderImpl(s).discard_stats(seg.id, now - timedelta(hours=1))
        assert stats == {"ad:bing_aclick": 1, "duplicate": 1}

        scores = WebScoreRepositoryImpl(s)
        pages = await scores.pages_for_segment(seg.id, ["vela"])
        assert len(pages) == 1
        page = pages[0]
        assert page.canonical_url == canonical
        assert page.best_position == 1  # melhor posição dos hits `kept`
        assert page.total_positions == 4  # maior posição no MESMO query_id

        vid = page.video_id
        assert (
            await scores.append_scores(
                [
                    WebPageScoreRow(
                        video_id=vid,
                        segment_id=seg.id,
                        computed_at=now - timedelta(hours=2),
                        score=10.0,
                        score_version="web-v1",
                        components={},
                        explanation={},
                    ),
                    WebPageScoreRow(
                        video_id=vid,
                        segment_id=seg.id,
                        computed_at=now,
                        score=55.0,
                        score_version="web-v1",
                        components={"tema": 1.0},
                        explanation={"tema": {"matched": ["vela"]}},
                    ),
                ]
            )
            == 2
        )
        await s.commit()
        assert await scores.latest_scores(seg.id) == {vid: 55.0}
        stored = await scores.hits_for_segment(seg.id)
        assert {h.status for h in stored} == {"kept", "discarded_ad", "duplicate"}
    await engine.dispose()


@requires_infra
async def test_web_page_position_total_is_query_result_set_size() -> None:
    """B1/B2: `total_positions` = tamanho da consulta; `query` = consulta exata."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-web-pos-{uuid4().hex[:8]}",
                name="Web",
                platforms=["web"],
                keywords=["vela"],
            )
        )
        canonical = f"https://alvo-{uuid4().hex[:8]}.example/vela"
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "web", [_web_candidate(canonical)])
        await s.commit()

        now = datetime.now(UTC)
        # a MESMA consulta (query_id `q1:aprender:00`) devolveu 8 resultados; a
        # página alvo está na posição 3 e as demais não têm vídeo próprio.
        await WebHitRepositoryImpl(s).record(
            seg.id,
            [
                _web_hit(seg.id, now, position=3, status="kept", canonical=canonical),
                _web_hit(seg.id, now, position=5, status="kept", canonical="https://o5.example/x"),
                _web_hit(seg.id, now, position=6, status="kept", canonical="https://o6.example/x"),
                _web_hit(
                    seg.id,
                    now,
                    position=7,
                    status="discarded_ad",
                    canonical="https://bing.com/aclick?z=7",
                    reason="ad:bing_aclick",
                ),
                _web_hit(seg.id, now, position=8, status="kept", canonical="https://o8.example/x"),
            ],
        )
        await s.commit()

        pages = await WebScoreRepositoryImpl(s).pages_for_segment(seg.id, ["vela"])
        target = next(p for p in pages if p.canonical_url == canonical)
        assert target.best_position == 3
        assert target.total_positions == 8  # N da consulta, não a posição da página
        assert target.query == "como fazer velas"
    await engine.dispose()
