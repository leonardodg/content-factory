import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from content_factory.app.ports import (
    CollectRunRepository,
    JobRunReader,
    KeywordSignalRepository,
    OpsReportReader,
    ResearchCampaignRepository,
    ScoreRepository,
    ScoreUpsertRow,
    SegmentRepository,
    TrendObservationRepository,
    UpsertReport,
    VideoRepository,
    WebHitRepository,
    WebScoreRepository,
)
from content_factory.app.score_sample import ScoreSampleRow
from content_factory.domain.errors import SegmentNotFound
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    KeywordSignal,
    MetricCoverage,
    ResearchCampaign,
    ScoreResult,
    Segment,
    SegmentConfig,
    TrendPoint,
    UpsertStats,
    VideoMetrics,
    VideoSnapshot,
    WebPageScoreRow,
    WebSearchHit,
)
from content_factory.domain.scoring import FORMULA_VERSION
from content_factory.domain.web_relevance import WebPageInput
from content_factory.domain.web_urls import canonicalize
from content_factory.infra.db.orm import (
    CollectRunRow,
    KeywordSignalRow,
    ResearchCampaignRow,
    SegmentRow,
    TrendObservationRow,
    VideoMetricsRow,
    VideoRow,
    VideoScoreRow,
    VideoSegmentRow,
    WebSearchHitRow,
)
from content_factory.infra.db.orm import (
    WebPageScoreRow as WebPageScoreOrmRow,
)

logger = logging.getLogger(__name__)


def _snapshot(
    video: VideoRow, m: VideoMetricsRow | None, previous: VideoMetricsRow | None = None
) -> VideoSnapshot:
    """LEFT JOIN sem match vira `None` ⇒ métricas None (vídeo ainda não capturado).

    Fase G: `None` ≠ `0` — métrica ausente não é penalizada como zero.
    Fase H Task 4: `previous` é a captura imediatamente anterior — base
    para `momentum_score` medir crescimento real entre snapshots.
    """
    metrics = (
        VideoMetrics(
            views=m.views,
            likes=m.likes,
            comments=m.comments,
            shares=m.shares,
            saves=m.saves,
        )
        if m is not None
        else VideoMetrics()
    )
    previous_metrics = (
        VideoMetrics(
            views=previous.views,
            likes=previous.likes,
            comments=previous.comments,
            shares=previous.shares,
            saves=previous.saves,
        )
        if previous is not None
        else None
    )
    return VideoSnapshot(
        video_id=video.id,
        title=video.title,
        description=video.description,
        published_at=video.published_at,
        metrics=metrics,
        captured_at=m.captured_at if m is not None else None,
        previous_metrics=previous_metrics,
        previous_captured_at=previous.captured_at if previous is not None else None,
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
                        language=c.language,
                        raw=c.raw,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["platform", "platform_video_id"],
                        # Fase H Task 5: metadados são sobrescritos SÓ quando a
                        # nova coleta tem dado melhor — texto vazio não apaga
                        # descrição conhecida (NULLIF→COALESCE); campos opcionais
                        # só quando o novo valor não é NULL; raw sempre vence
                        # (payload bruto completo daquela coleta, não parcial).
                        set_={
                            "title": func.coalesce(
                                func.nullif(stmt.excluded.title, ""), VideoRow.title
                            ),
                            "description": func.coalesce(
                                func.nullif(stmt.excluded.description, ""), VideoRow.description
                            ),
                            "published_at": func.coalesce(
                                stmt.excluded.published_at, VideoRow.published_at
                            ),
                            "duration_s": func.coalesce(
                                stmt.excluded.duration_s, VideoRow.duration_s
                            ),
                            "thumbnail_url": func.coalesce(
                                stmt.excluded.thumbnail_url, VideoRow.thumbnail_url
                            ),
                            "language": func.coalesce(stmt.excluded.language, VideoRow.language),
                            "raw": stmt.excluded.raw,
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
                    # Fase F: gravar snapshot em video_metrics no MESMO
                    # begin_nested (atomicidade por candidato). Preserva
                    # None quando o coletor não reportou a métrica (YouTube
                    # não expõe shares; TikTok não expõe saves — ADR-0003).
                    # Candidato sem NENHUMA métrica não gera linha toda NULL.
                    has_metric = any(
                        v is not None for v in (c.views, c.likes, c.comments, c.shares, c.saves)
                    )
                    if has_metric:
                        self._s.add(
                            VideoMetricsRow(
                                video_id=video_id,
                                views=c.views,
                                likes=c.likes,
                                comments=c.comments,
                                shares=c.shares,
                                saves=c.saves,
                                followers_at_capture=c.creator_followers,
                            )
                        )
                if is_new:
                    stats.inserted += 1
                else:
                    stats.updated += 1
            except DataError, IntegrityError:
                stats.skipped += 1
        return stats

    async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
        """1 snapshot por vídeo do segmento com a métrica mais recente + a
        captura imediatamente anterior (Task 4 — base para `momentum_score`).

        Vídeo sem captura em `video_metrics` entra com `metrics=VideoMetrics()`
        (todos os campos `None` — Fase G: ausência ≠ zero).
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

        rn_col = (
            func.row_number()
            .over(
                partition_by=VideoMetricsRow.video_id,
                order_by=VideoMetricsRow.captured_at.desc(),
            )
            .label("rn")
        )
        ranked = (
            select(VideoMetricsRow, rn_col)
            .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoMetricsRow.video_id)
            .where(VideoSegmentRow.segment_id == segment_id)
            .subquery()
        )
        PrevMetrics = aliased(VideoMetricsRow, ranked)
        prev_stmt = select(PrevMetrics).where(ranked.c.rn == 2)
        prev_rows = (await self._s.execute(prev_stmt)).scalars().all()
        previous_by_video = {row.video_id: row for row in prev_rows}

        return [_snapshot(video, m, previous_by_video.get(video.id)) for video, m in rows]


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

    async def timing_stats(
        self, segment_id: int, since: datetime
    ) -> dict[str, dict[str, float | int | None]]:
        """Agregado de discover_ms/persist_ms por plataforma (baseline Task 4).

        `avg`/`percentile_cont` ignoram NULLs (runs pré-instrumentação) —
        plataforma só com NULLs volta com médias None e `n` contando tudo.
        """
        stmt = (
            select(
                CollectRunRow.collector,
                func.count(CollectRunRow.id).label("n"),
                func.avg(CollectRunRow.discover_ms).label("avg_discover_ms"),
                func.avg(CollectRunRow.persist_ms).label("avg_persist_ms"),
                func.percentile_cont(0.95)
                .within_group(CollectRunRow.discover_ms)
                .label("p95_discover_ms"),
                func.percentile_cont(0.95)
                .within_group(CollectRunRow.persist_ms)
                .label("p95_persist_ms"),
            )
            .where(CollectRunRow.segment_id == segment_id, CollectRunRow.started_at > since)
            .group_by(CollectRunRow.collector)
        )
        stats: dict[str, dict[str, float | int | None]] = {}
        for row in (await self._s.execute(stmt)).all():
            collector, n, avg_d, avg_p, p95_d, p95_p = row
            stats[collector] = {
                "n": int(n),
                "avg_discover_ms": float(avg_d) if avg_d is not None else None,
                "p95_discover_ms": float(p95_d) if p95_d is not None else None,
                "avg_persist_ms": float(avg_p) if avg_p is not None else None,
                "p95_persist_ms": float(p95_p) if p95_p is not None else None,
            }
        return stats

    async def runs_since(self, segment_id: int, since: datetime) -> list[CollectRun]:
        stmt = (
            select(CollectRunRow)
            .where(CollectRunRow.segment_id == segment_id, CollectRunRow.started_at > since)
            .order_by(CollectRunRow.started_at, CollectRunRow.id)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            CollectRun(
                collector=r.collector,
                segment_id=r.segment_id,
                status=r.status,
                found=r.found,
                inserted=r.inserted,
                updated=r.updated,
                failed=r.failed,
                skipped=r.skipped,
                quota_used=r.quota_used,
                retries=r.retries,
                error_code=r.error_code,
                started_at=r.started_at,
                finished_at=r.finished_at,
                discover_ms=r.discover_ms,
                persist_ms=r.persist_ms,
            )
            for r in rows
        ]


# views no ÚLTIMO snapshot e nº de snapshots por vídeo do segmento (C2);
# com `since`, só vídeos com snapshot capturado na janela (auditoria 3, N-1)
_COVERAGE_SQL = text(
    """
    SELECT v.platform,
           count(*) AS videos,
           count(*) FILTER (WHERE last.views IS NOT NULL) AS with_views,
           count(*) FILTER (WHERE snaps.n >= 2) AS with_two_snapshots
    FROM video_segments vs
    JOIN videos v ON v.id = vs.video_id
    LEFT JOIN LATERAL (
        SELECT m.views FROM video_metrics m
        WHERE m.video_id = v.id ORDER BY m.captured_at DESC, m.id DESC LIMIT 1
    ) last ON true
    LEFT JOIN LATERAL (
        SELECT count(*) AS n FROM video_metrics m WHERE m.video_id = v.id
    ) snaps ON true
    WHERE vs.segment_id = :segment_id
      AND (CAST(:since AS timestamptz) IS NULL OR EXISTS (
          SELECT 1 FROM video_metrics w
          WHERE w.video_id = v.id AND w.captured_at > CAST(:since AS timestamptz)
      ))
    GROUP BY v.platform
    """
)


class JobRunReaderImpl(JobRunReader):
    """Última run de cada job do cron, para o catch-up do worker (T2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def last_runs(self, segment_ids: list[int]) -> dict[str, datetime | None]:
        if not segment_ids:
            return {"research": None, "collect": None, "score": None}
        is_research = CollectRunRow.collector.like("research:%")
        in_segments = CollectRunRow.segment_id.in_(segment_ids)
        research = select(func.max(CollectRunRow.started_at)).where(in_segments, is_research)
        collect = select(func.max(CollectRunRow.started_at)).where(in_segments, ~is_research)
        scored = select(func.max(VideoScoreRow.computed_at)).where(
            VideoScoreRow.segment_id.in_(segment_ids)
        )
        return {
            "research": (await self._s.execute(research)).scalar_one(),
            "collect": (await self._s.execute(collect)).scalar_one(),
            "score": (await self._s.execute(scored)).scalar_one(),
        }


class OpsReportReaderImpl(OpsReportReader):
    """Leituras do `cf ops-report` (C2): cobertura de métricas e scores."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def metric_coverage(
        self, segment_id: int, since: datetime | None = None
    ) -> dict[str, MetricCoverage]:
        params = {"segment_id": segment_id, "since": since}
        rows = (await self._s.execute(_COVERAGE_SQL, params)).all()
        return {
            str(platform): MetricCoverage(
                videos=int(videos),
                with_views=int(with_views),
                with_two_snapshots=int(with_two),
            )
            for platform, videos, with_views, with_two in rows
        }

    async def scored_since(self, segment_id: int, since: datetime) -> int:
        stmt = select(func.count(VideoScoreRow.id)).where(
            VideoScoreRow.segment_id == segment_id, VideoScoreRow.computed_at > since
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def discard_stats(self, segment_id: int, since: datetime) -> dict[str, int]:
        stmt = (
            select(WebSearchHitRow.discard_reason, func.count(WebSearchHitRow.id))
            .where(
                WebSearchHitRow.segment_id == segment_id,
                WebSearchHitRow.collected_at > since,
                WebSearchHitRow.discard_reason.is_not(None),
            )
            .group_by(WebSearchHitRow.discard_reason)
        )
        return {str(reason): int(n) for reason, n in (await self._s.execute(stmt)).all()}


SCORE_UPSERT_BATCH_SIZE = 500
"""Linhas por bloco em `ScoreRepositoryImpl.upsert_many` (Task 13).

1 flush por bloco: N vídeos custam ~N/500 round-trips no caminho feliz.
Bloco pequeno o bastante para o fallback linha-a-linha isolar a ruim sem
repetir muito trabalho; grande o bastante para o ganho de lote valer.
"""


def _score_orm_row(row: ScoreUpsertRow) -> VideoScoreRow:
    """Mapeia a linha do lote (porta) para a linha ORM append-only."""
    return VideoScoreRow(
        video_id=row.video_id,
        segment_id=row.segment_id,
        computed_at=row.computed_at,
        viral_score=row.result.total,
        performance=row.result.performance,
        velocity=row.result.velocity,
        engagement=row.result.engagement,
        relevance=row.result.relevance,
        recency=row.result.recency,
        trend_decay=row.result.trend_decay,
        metrics_available=row.result.metrics_available,
        formula_version=FORMULA_VERSION,
    )


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
                _score_orm_row(
                    ScoreUpsertRow(
                        video_id=video_id,
                        segment_id=segment_id,
                        result=result,
                        computed_at=computed_at,
                    )
                )
            )
            await self._s.flush()

    async def upsert_many(self, rows: Sequence[ScoreUpsertRow]) -> UpsertReport:
        """1 flush por bloco de N; bloco quebrado cai para linha-a-linha.

        Sem ON CONFLICT, `add_all` + 1 `flush` por bloco troca N round-trips
        por 1 (Task 12 mediu ~0,7 ms/vídeo em begin_nested+flush por linha).
        O SAVEPOINT por bloco existe pelo mesmo motivo do por-vídeo: no
        Postgres um flush quebrado envenena a transação inteira — sem ele,
        uma linha ruim reverteria o lote todo no commit. Em falha do bloco,
        o fallback linha-a-linha (savepoint por linha) isola a ruim; o
        rollback do bloco falho é só até o savepoint e não envenena a
        transação externa. Só DataError/IntegrityError são isolados (dado
        da linha); conexão e afins propagam para o caller.

        R5: linha descartada NÃO some em silêncio — vira `failures` por
        categoria no relatório e 1 WARNING com `video_id` + classe da
        exceção (nunca o valor que estourou: dado bruto fica fora do log).
        """
        saved = 0
        failures: dict[str, int] = {}
        for start in range(0, len(rows), SCORE_UPSERT_BATCH_SIZE):
            block = list(rows[start : start + SCORE_UPSERT_BATCH_SIZE])
            try:
                async with self._s.begin_nested():
                    self._s.add_all([_score_orm_row(row) for row in block])
                    await self._s.flush()
                saved += len(block)
            except DataError, IntegrityError:
                for row in block:
                    try:
                        async with self._s.begin_nested():
                            self._s.add(_score_orm_row(row))
                            await self._s.flush()
                        saved += 1
                    except (DataError, IntegrityError) as exc:
                        failures["data_error"] = failures.get("data_error", 0) + 1
                        logger.warning(
                            "score: linha descartada video_id=%s classe=%s",
                            row.video_id,
                            type(exc).__name__,
                        )
        return UpsertReport(persisted=saved, failures=failures)


class ScoreSampleRepositoryImpl:
    """Task 8 — ranking mais recente de um segmento, joinado com `videos.url`.

    Mesmo padrão `DISTINCT ON` de `latest_for_segment`: pega a linha mais
    recente de `video_scores` por vídeo (append-only timeseries), ordena por
    `viral_score DESC` e limita.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def top_scored_for_segment(
        self, segment_id: int, limit: int, platform: str | None = None
    ) -> list[ScoreSampleRow]:
        # 1a passada: 1 linha por vídeo (DISTINCT ON — captura mais recente).
        # 2a passada: ordena pelo viral_score e aplica o LIMIT em SQL — evita
        # trazer todo o segmento pra memória só pra cortar em Python.
        latest = (
            select(VideoScoreRow)
            .where(VideoScoreRow.segment_id == segment_id)
            .ext(distinct_on(VideoScoreRow.video_id))
            .order_by(VideoScoreRow.video_id, VideoScoreRow.computed_at.desc())
            .subquery()
        )
        LatestScore = aliased(VideoScoreRow, latest)
        stmt = (
            select(LatestScore, VideoRow.url, VideoRow.platform)
            .join(VideoRow, VideoRow.id == LatestScore.video_id)
            .order_by(LatestScore.viral_score.desc())
            .limit(limit)
        )
        if platform is not None:
            stmt = stmt.where(VideoRow.platform == platform)
        rows = (await self._s.execute(stmt)).all()
        return [
            ScoreSampleRow(
                video_id=score.video_id,
                url=url,
                total=score.viral_score,
                performance=score.performance,
                velocity=score.velocity,
                engagement=score.engagement,
                relevance=score.relevance,
                recency=score.recency,
                metrics_available=list(score.metrics_available or []),
                platform=video_platform,
            )
            for score, url, video_platform in rows
        ]


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

    async def active_segments(self) -> list[Segment]:
        """Segmentos com campanha ativa e não vencida — fonte única do tick.

        `.distinct()` evita duplicar se um dia existir mais de 1 campanha
        ativa pro mesmo segmento (start() hoje não previne isso).
        """
        stmt = (
            select(SegmentRow)
            .join(ResearchCampaignRow, ResearchCampaignRow.segment_id == SegmentRow.id)
            .where(
                ResearchCampaignRow.active,
                ResearchCampaignRow.ends_at > datetime.now(UTC),
            )
            .distinct()
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            Segment(id=row.id, **SegmentConfig.model_validate(row.config).model_dump())
            for row in rows
        ]


class TrendObservationRepositoryImpl(TrendObservationRepository):
    """Upsert por (segment_id, keyword, period_start, period_granularity) —
    Fase H: mesmo período regravado atualiza `interest`/`collected_at`,
    não duplica a linha (ver ADR-0005 revisado)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record_many(self, segment_id: int, points: Sequence[TrendPoint]) -> int:
        for p in points:
            stmt = pg_insert(TrendObservationRow).values(
                segment_id=segment_id,
                keyword=p.keyword,
                period_start=p.period_start,
                period_granularity=p.period_granularity,
                interest=p.interest,
                collected_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["segment_id", "keyword", "period_start", "period_granularity"],
                set_={
                    "interest": stmt.excluded.interest,
                    "collected_at": stmt.excluded.collected_at,
                },
            )
            await self._s.execute(stmt)
        await self._s.flush()
        return len(points)

    async def history_for_keyword(
        self, segment_id: int, keyword: str, limit: int
    ) -> list[TrendPoint]:
        # com a unique constraint do Passo 6.1 nunca mais devolve o mesmo
        # período 2x
        stmt = (
            select(TrendObservationRow)
            .where(
                TrendObservationRow.segment_id == segment_id,
                TrendObservationRow.keyword == keyword,
            )
            .order_by(TrendObservationRow.period_start.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            TrendPoint(
                keyword=r.keyword,
                period_start=r.period_start,
                period_granularity=r.period_granularity,
                interest=r.interest,
            )
            for r in rows
        ]


def _web_platform_video_id(canonical_url: str) -> str:
    return hashlib.sha1(canonical_url.encode(), usedforsecurity=False).hexdigest()


class WebHitRepositoryImpl(WebHitRepository):
    """F1 — grava `web_search_hits`; resolve `video_id` (kept) por URL canônica."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, segment_id: int, hits: Sequence[WebSearchHit]) -> int:
        saved = 0
        for hit in hits:
            video_id: int | None = None
            if hit.status == "kept":
                video_id = (
                    await self._s.execute(
                        select(VideoRow.id).where(
                            VideoRow.platform == "web",
                            VideoRow.platform_video_id == _web_platform_video_id(hit.canonical_url),
                        )
                    )
                ).scalar_one_or_none()
            self._s.add(
                WebSearchHitRow(
                    segment_id=segment_id,
                    video_id=video_id,
                    collected_at=hit.collected_at or datetime.now(UTC),
                    query=hit.query,
                    query_id=hit.query_id,
                    query_intent=hit.query_intent,
                    provider=hit.provider,
                    provider_result_position=hit.provider_result_position,
                    url_original=hit.url_original,
                    canonical_url=hit.canonical_url,
                    status=hit.status,
                    discard_reason=hit.discard_reason,
                    collector_version=hit.collector_version,
                    rules_version=hit.rules_version,
                    title=hit.title,
                    snippet=hit.snippet,
                )
            )
            saved += 1
        await self._s.flush()
        return saved


class WebScoreRepositoryImpl(WebScoreRepository):
    """F3 — leitura das páginas web + escrita/leitura de `web_page_scores`."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def pages_for_segment(self, segment_id: int, keywords: list[str]) -> list[WebPageInput]:
        videos = (
            (
                await self._s.execute(
                    select(VideoRow)
                    .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoRow.id)
                    .where(VideoSegmentRow.segment_id == segment_id, VideoRow.platform == "web")
                    .order_by(VideoRow.id)
                )
            )
            .scalars()
            .all()
        )
        hits = (
            (
                await self._s.execute(
                    select(WebSearchHitRow)
                    .where(WebSearchHitRow.segment_id == segment_id)
                    .order_by(WebSearchHitRow.collected_at, WebSearchHitRow.id)
                )
            )
            .scalars()
            .all()
        )
        by_video: dict[int, list[WebSearchHitRow]] = {}
        # N por consulta = tamanho da lista devolvida por aquele `query_id`
        # (maior posição entre TODOS os resultados da consulta, não só os desta
        # página): a fórmula da F3 é `1 - (p - 1)/N`.
        query_size: dict[str, int] = {}
        for hit in hits:
            qid = hit.query_id
            query_size[qid] = max(query_size.get(qid, 0), hit.provider_result_position)
            if hit.video_id is not None:
                by_video.setdefault(hit.video_id, []).append(hit)
        pages: list[WebPageInput] = []
        for video in videos:
            video_hits = by_video.get(video.id, [])
            if video_hits:
                best = min(video_hits, key=lambda h: h.provider_result_position)
                total = query_size.get(best.query_id, best.provider_result_position)
                canonical = best.canonical_url
                snippet = best.snippet
                intent = best.query_intent
                query = best.query
                position: int | None = best.provider_result_position
            else:
                canonical = video.url
                snippet = video.description
                intent = ""
                query = ""
                position = None
                total = 0
            pages.append(
                WebPageInput(
                    video_id=video.id,
                    title=video.title,
                    snippet=snippet,
                    keywords=list(keywords),
                    canonical_url=canonical,
                    canonical_ok=canonicalize(canonical).ok,
                    query=query,
                    query_intent=intent,
                    best_position=position,
                    total_positions=total,
                    published_at=video.published_at,
                )
            )
        return pages

    async def append_scores(self, rows: Sequence[WebPageScoreRow]) -> int:
        for row in rows:
            self._s.add(
                WebPageScoreOrmRow(
                    video_id=row.video_id,
                    segment_id=row.segment_id,
                    computed_at=row.computed_at,
                    score=row.score,
                    score_version=row.score_version,
                    components=row.components,
                    explanation=row.explanation,
                )
            )
        await self._s.flush()
        return len(rows)

    async def latest_scores(self, segment_id: int) -> dict[int, float | None]:
        latest = (
            select(WebPageScoreOrmRow)
            .where(WebPageScoreOrmRow.segment_id == segment_id)
            .ext(distinct_on(WebPageScoreOrmRow.video_id))
            .order_by(WebPageScoreOrmRow.video_id, WebPageScoreOrmRow.computed_at.desc())
        )
        rows = (await self._s.execute(latest)).scalars().all()
        return {row.video_id: row.score for row in rows}

    async def hits_for_segment(self, segment_id: int) -> list[WebSearchHit]:
        stmt = (
            select(WebSearchHitRow)
            .where(WebSearchHitRow.segment_id == segment_id)
            .order_by(WebSearchHitRow.collected_at, WebSearchHitRow.id)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            WebSearchHit(
                segment_id=r.segment_id,
                video_id=r.video_id,
                collected_at=r.collected_at,
                query=r.query,
                query_id=r.query_id,
                query_intent=r.query_intent,
                provider=r.provider,
                provider_result_position=r.provider_result_position,
                url_original=r.url_original,
                canonical_url=r.canonical_url,
                status=r.status,
                discard_reason=r.discard_reason,
                collector_version=r.collector_version,
                rules_version=r.rules_version,
                title=r.title,
                snippet=r.snippet,
            )
            for r in rows
        ]
