from collections.abc import Callable
from datetime import UTC, datetime

from content_factory.app.ports import ScoreRepository, VideoRepository
from content_factory.domain.models import Segment, VideoSnapshot
from content_factory.domain.scoring import viral_score


class ScoreDeps:
    def __init__(
        self,
        videos: VideoRepository,
        scores: ScoreRepository,
        segment: Segment,
        keywords: list[str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.videos = videos
        self.scores = scores
        self.segment = segment
        self.keywords = keywords
        self.clock = clock


class ScoreOutcome:
    def __init__(self, ok: bool, partial: bool, scored: int) -> None:
        self.ok = ok
        self.partial = partial
        self.scored = scored


def relevance_of(snap: VideoSnapshot, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    text = f"{snap.title} {snap.description}".lower()
    hits = sum(1 for k in keywords if k.lower() in text)
    return hits / len(keywords)


async def score(deps: ScoreDeps) -> ScoreOutcome:
    now = deps.clock()
    snaps = await deps.videos.latest_for_segment(deps.segment.id)
    scored = 0
    errors = 0
    for snap in snaps:
        rel = relevance_of(snap, deps.keywords)
        result = viral_score(snap.metrics, rel, now, snap.published_at)
        try:
            await deps.scores.upsert(snap.video_id, deps.segment.id, result, now)
            scored += 1
        except Exception:  # repo em falha não derruba o lote
            errors += 1
    ok = errors == 0
    return ScoreOutcome(ok=ok, partial=(not ok and scored > 0), scored=scored)
