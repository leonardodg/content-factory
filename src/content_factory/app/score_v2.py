"""R9/R11 (Bloco 5 Task 7): `viral-v2` do segmento + hashtags do topo no pool.

Roda no job de score, DEPOIS do fase-h (transação própria, atrás de
`VIRAL_V2_ENABLED`): o score novo nunca custa o `viral_score` do segmento e
nunca escreve em `video_scores`.

- População no corte (`as_of` = agora) → relevância (filtro) → `viral-v2`
  (percentis por plataforma e segmento) → `video_scores_v2`.
- Hashtags (R11): dos vídeos no top 25% do `viral-v2`, filtradas pelo R1 em
  modo hashtag; as top 10 por plataforma entram/ficam ativas no pool como
  `hashtag:<plataforma>`; as que saíram do top viram `excluded`/`stale`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from content_factory.app.population import build_population
from content_factory.app.ports import PopulationReader, SegmentTermRepository, ViralV2Repository
from content_factory.domain.hashtag_mining import HASHTAG_MINE_VERSION, HashtagSource, mine
from content_factory.domain.models import Segment, SegmentTerm
from content_factory.domain.term_filter import TermFilterConfig
from content_factory.domain.viral_v2 import MIN_POPULATION, WINDOW_DAYS, score_population


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ScoreV2Deps:
    population: PopulationReader
    scores_v2: ViralV2Repository
    terms: SegmentTermRepository
    segment: Segment
    clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow)
    window_days: int = WINDOW_DAYS
    min_population: int = MIN_POPULATION


@dataclass
class ScoreV2Outcome:
    """Sub-envelope `viral_v2` do score (só `int`: vai direto pro JSON)."""

    scored: int = 0
    skipped_relevance: int = 0
    insufficient: int = 0
    hashtags_active: int = 0
    hashtags_stale: int = 0


async def score_v2(deps: ScoreV2Deps) -> ScoreV2Outcome:
    seg, as_of = deps.segment, deps.clock()
    rows = await deps.population.population(seg.id, as_of, deps.window_days)
    inputs, skipped = build_population(rows, seg)
    scores = score_population(inputs, deps.min_population)
    await deps.scores_v2.append_many(seg.id, as_of, scores, deps.window_days)

    by_id = {r.video_id: r for r in rows}
    sources = [
        HashtagSource(
            video_id=s.video_id,
            platform=by_id[s.video_id].platform,
            viral_v2=s.total,
            raw=by_id[s.video_id].raw,
            title=by_id[s.video_id].title,
            description=by_id[s.video_id].description,
        )
        for s in scores
    ]
    cfg = TermFilterConfig(anchors=tuple(seg.anchor_terms), blocked=tuple(seg.blocked_terms))
    mined = mine(sources, cfg)
    out = ScoreV2Outcome(
        scored=len(scores),
        skipped_relevance=skipped,
        insufficient=sum(1 for s in scores if s.total is None),
    )
    for platform in sorted({h.platform for h in mined}):
        tags = [h for h in mined if h.platform == platform]
        origin = f"hashtag:{platform}"
        out.hashtags_active += await deps.terms.upsert_many(
            seg.id,
            [
                SegmentTerm(
                    segment_id=seg.id,
                    term=h.tag,
                    origin=origin,
                    engagement_score=h.weight,
                    rules_version=HASHTAG_MINE_VERSION,
                )
                for h in tags
            ],
        )
        out.hashtags_stale += await deps.terms.set_status(
            seg.id, origin, [h.tag for h in tags], "stale"
        )
    return out
