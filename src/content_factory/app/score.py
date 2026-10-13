from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from content_factory.app.ports import ScoreRepository, VideoRepository
from content_factory.domain.models import Segment, VideoSnapshot
from content_factory.domain.scoring import viral_score


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ScoreDeps:
    """Deps do score — @dataclass para permitir `dataclasses.replace` por segmento.

    Paridade com ResearchDeps/CollectDeps: o worker monta 1 por UoW trocando
    videos/scores/segment/keywords. Mesmos campos, mesma ordem e mesmo default
    do `__init__` anterior (construção posicional e nomeada preservadas).
    """

    videos: VideoRepository
    scores: ScoreRepository
    segment: Segment
    keywords: list[str]
    # default é o CHAMÁVEL (não default_factory — a factory seria chamada e
    # viraria datetime): mesma semântica do `lambda` do `__init__` anterior.
    clock: Callable[[], datetime] = _utcnow


class ScoreOutcome:
    def __init__(self, ok: bool, partial: bool, scored: int) -> None:
        self.ok = ok
        self.partial = partial
        self.scored = scored


def relevance_of(snap: VideoSnapshot, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    text = f"{snap.title} {snap.description}".lower()
    hits = sum(1 for k in keywords if k.lower().removeprefix("#") in text)
    return hits / len(keywords)


async def score(deps: ScoreDeps) -> ScoreOutcome:
    now = deps.clock()
    snaps = await deps.videos.latest_for_segment(deps.segment.id)
    scored = 0
    errors = 0
    for snap in snaps:
        rel = relevance_of(snap, deps.keywords)
        result = viral_score(
            snap.metrics,
            rel,
            now,
            snap.published_at,
            captured_at=snap.captured_at,
            previous_metrics=snap.previous_metrics,
            previous_captured_at=snap.previous_captured_at,
        )
        try:
            await deps.scores.upsert(snap.video_id, deps.segment.id, result, now)
            scored += 1
        except Exception:  # repo em falha não derruba o lote
            errors += 1
    ok = errors == 0
    return ScoreOutcome(ok=ok, partial=(not ok and scored > 0), scored=scored)
