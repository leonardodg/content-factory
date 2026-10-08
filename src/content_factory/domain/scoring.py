from datetime import datetime

from content_factory.domain.models import ScoreResult, VideoMetrics

WEIGHTS = {
    "performance": 0.25,
    "velocity": 0.20,
    "engagement": 0.15,
    "relevance": 0.20,
    "recency": 0.20,
}


def engagement_rate(m: VideoMetrics) -> float:
    if m.views <= 0:
        return 0.0
    weighted = m.likes * 1 + m.comments * 2 + m.shares * 3 + m.saves * 4
    return round(weighted / m.views * 100, 2)


def trend_decay(published_at: datetime | None, now: datetime) -> str:
    if published_at is None:
        return "none"
    age_days = (now - published_at).days
    if age_days <= 7:
        return "low"
    if age_days <= 30:
        return "medium"
    return "high"


def viral_score(
    m: VideoMetrics, relevance: float, now: datetime, published_at: datetime | None
) -> ScoreResult:
    performance = min(m.views / 1_000_000 * 100, 100.0)
    eng = min(engagement_rate(m) * 10, 100.0)  # 10%+ = 100
    decay = trend_decay(published_at, now)
    age_days = (now - published_at).days if published_at else 999
    velocity = (
        100.0 if age_days <= 1 else 80.0 if age_days <= 3 else 50.0 if age_days <= 7 else 20.0
    )
    recency = {"none": 50.0, "low": 100.0, "medium": 50.0, "high": 15.0}[decay]
    rel = min(max(relevance, 0.0), 1.0) * 100

    total = round(
        performance * WEIGHTS["performance"]
        + velocity * WEIGHTS["velocity"]
        + eng * WEIGHTS["engagement"]
        + rel * WEIGHTS["relevance"]
        + recency * WEIGHTS["recency"],
        2,
    )
    if m.views == 0 and m.likes == 0 and relevance == 0:
        total = 0.0
    return ScoreResult(
        total=total,
        performance=round(performance, 2),
        velocity=velocity,
        engagement=round(eng, 2),
        relevance=round(rel, 2),
        recency=recency,
        trend_decay=decay,
    )
