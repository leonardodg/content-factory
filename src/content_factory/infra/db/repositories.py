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
    KeywordSignalRepository,
    ResearchCampaignRepository,
    ScoreRepository,
    SegmentRepository,
    TrendObservationRepository,
    VideoRepository,
)
from content_factory.app.score_sample import ScoreSampleRow
from content_factory.domain.errors import SegmentNotFound
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    KeywordSignal,
    ResearchCampaign,
    ScoreResult,
    Segment,
    SegmentConfig,
    TrendPoint,
    UpsertStats,
    VideoMetrics,
    VideoSnapshot,
)
from content_factory.domain.scoring import FORMULA_VERSION
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
)


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
                    metrics_available=result.metrics_available,
                    formula_version=FORMULA_VERSION,
                )
            )
            await self._s.flush()


class ScoreSampleRepositoryImpl:
    """Task 8 — ranking mais recente de um segmento, joinado com `videos.url`.

    Mesmo padrão `DISTINCT ON` de `latest_for_segment`: pega a linha mais
    recente de `video_scores` por vídeo (append-only timeseries), ordena por
    `viral_score DESC` e limita.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def top_scored_for_segment(self, segment_id: int, limit: int) -> list[ScoreSampleRow]:
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
            select(LatestScore, VideoRow.url)
            .join(VideoRow, VideoRow.id == LatestScore.video_id)
            .order_by(LatestScore.viral_score.desc())
            .limit(limit)
        )
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
            )
            for score, url in rows
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
