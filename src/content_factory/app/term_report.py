"""R10 (Bloco 5 Task 8): relatório NO CORTE — temas, origens e top vídeos.

Recalcula o `viral-v2` a partir da população até `as_of` (mesma regra do
score: `app/population`), em vez de juntar scores gravados em momentos
diferentes — percentis de horários diferentes não se comparam. Mesmo
`as_of` → mesmas linhas (só `report_id`/`computed_at` mudam).

Três escopos em `term_reports`, com o mesmo `report_id`:
- `term`: ranking de temas (vídeo único por termo; n < 5 = insuficiente);
- `origin`: o veredito PRÉ-REGISTRADO de cada origem contra `seed`
  (diferença de medianas, IC 95% bootstrap, n >= 30), e `seed` como base;
- `video`: os N maiores `viral-v2` da plataforma — o entregável do objetivo.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from content_factory.app.population import RELEVANCE_VERSION, build_population
from content_factory.app.ports import (
    PopulationReader,
    SegmentRepository,
    SegmentTermRepository,
    TermReportRepository,
)
from content_factory.domain.models import PopulationRow, TermReportRecord
from content_factory.domain.term_filter import TERM_FILTER_VERSION
from content_factory.domain.term_ranking import (
    BASELINE_ORIGIN,
    TERM_RANK_VERSION,
    TermSample,
    compare_origins,
    rank_terms,
)
from content_factory.domain.viral_v2 import (
    MIN_POPULATION,
    VIRAL_V2_VERSION,
    WINDOW_DAYS,
    ViralScore,
    score_population,
)

RULES_VERSION = f"{TERM_FILTER_VERSION}+{RELEVANCE_VERSION}+{VIRAL_V2_VERSION}"


@dataclass
class TermReportDeps:
    population: PopulationReader
    reports: TermReportRepository
    segments: SegmentRepository
    terms: SegmentTermRepository
    window_days: int = WINDOW_DAYS
    min_population: int = MIN_POPULATION
    new_report_id: Callable[[], str] = field(default_factory=lambda: lambda: str(uuid4()))


@dataclass
class TopVideo:
    """Linha do top para o CSV local (título é de terceiro: nunca no repositório)."""

    rank: int
    platform_video_id: str
    title: str
    viral_v2: float
    views: int | None


@dataclass
class TermReportOutcome:
    report_id: str
    records: list[TermReportRecord]
    top: list[TopVideo]

    def as_counts(self) -> dict[str, int | str]:
        by_scope: dict[str, list[TermReportRecord]] = {}
        for r in self.records:
            by_scope.setdefault(r.scope, []).append(r)
        terms = by_scope.get("term", [])
        origins = [r for r in by_scope.get("origin", []) if r.report_key != BASELINE_ORIGIN]
        return {
            "report_id": self.report_id,
            "terms": len(terms),
            "terms_ranked": sum(1 for r in terms if r.verdict == "ranked"),
            "origins": len(origins),
            "origins_decided": sum(1 for r in origins if r.verdict in ("better", "worse")),
            "videos": len(by_scope.get("video", [])),
            "rows_saved": len(self.records),
        }


def _samples(pairs: list[tuple[int, str, str]], scores: dict[int, ViralScore]) -> list[TermSample]:
    return [
        TermSample(
            video_id=vid,
            term=term,
            origin=origin,
            viral_v2=scores[vid].total,
            velocity=scores[vid].velocity,
            engagement=scores[vid].engagement,
            channel_relative=scores[vid].channel_relative,
        )
        for vid, term, origin in pairs
        if vid in scores  # fora da janela/relevância no corte: não entra
    ]


async def build_term_report(
    segment_slug: str,
    platform: str,
    as_of: datetime,
    deps: TermReportDeps,
    *,
    top_videos: int = 20,
) -> TermReportOutcome:
    seg = await deps.segments.get_by_slug(segment_slug)
    rows = await deps.population.population(seg.id, as_of, deps.window_days)
    inputs, _ = build_population(rows, seg)
    scored = score_population(inputs, deps.min_population)
    by_id: dict[int, PopulationRow] = {r.video_id: r for r in rows}
    scores = {s.video_id: s for s in scored if by_id[s.video_id].platform == platform}
    samples = _samples(await deps.population.attributions(seg.id, platform, as_of), scores)

    report_id = deps.new_report_id()

    def record(scope: str, key: str, **values: object) -> TermReportRecord:
        return TermReportRecord.model_validate(
            {
                "report_id": report_id,
                "segment_id": seg.id,
                "platform": platform,
                "scope": scope,
                "report_key": key,
                "version": TERM_RANK_VERSION,
                "rules_version": RULES_VERSION,
                "as_of": as_of,
                **values,
            }
        )

    records: list[TermReportRecord] = []
    ranked = rank_terms(samples)
    for i, t in enumerate(ranked, start=1):
        records.append(
            record(
                "term",
                t.key,
                n=t.n,
                median_viral_v2=t.median_viral_v2,
                median_velocity=t.median_velocity,
                median_engagement=t.median_engagement,
                median_channel_relative=t.median_channel_relative,
                top10_fraction=t.top10_fraction,
                engagement_score=t.engagement_score,
                verdict=t.verdict,
                rank=i if t.verdict == "ranked" else None,
            )
        )
    comparisons = compare_origins(samples)
    if comparisons:
        first = comparisons[0]
        records.append(
            record(
                "origin",
                BASELINE_ORIGIN,
                n=first.baseline_n,
                median_viral_v2=first.baseline_median,
                verdict="baseline",
            )
        )
    for c in comparisons:
        records.append(
            record(
                "origin",
                c.origin,
                n=c.n,
                baseline_n=c.baseline_n,
                median_viral_v2=c.median_viral_v2,
                diff=c.diff,
                ci_low=c.ci_low,
                ci_high=c.ci_high,
                overlap=c.overlap,
                verdict=c.verdict,
            )
        )
    best = sorted(
        (s for s in scores.values() if s.total is not None),
        key=lambda s: (-(s.total or 0.0), by_id[s.video_id].platform_video_id),
    )[:top_videos]
    top: list[TopVideo] = []
    for i, s in enumerate(best, start=1):
        row = by_id[s.video_id]
        records.append(
            record(
                "video",
                row.platform_video_id,
                n=1,
                median_viral_v2=s.total,
                median_velocity=s.velocity,
                median_engagement=s.engagement,
                median_channel_relative=s.channel_relative,
                verdict="top",
                rank=i,
            )
        )
        top.append(TopVideo(i, row.platform_video_id, row.title, s.total or 0.0, row.views))

    await deps.reports.append_many(records)
    engagement = {t.key: t.engagement_score for t in ranked if t.engagement_score is not None}
    if engagement:
        await deps.terms.set_engagement_scores(seg.id, engagement)
    return TermReportOutcome(report_id=report_id, records=records, top=top)
