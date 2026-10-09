from datetime import UTC, datetime, timedelta
from typing import Any

from content_factory.domain.models import ScoreResult, VideoMetrics
from content_factory.domain.scoring import engagement_rate, viral_score

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def metrics(**kw: Any) -> VideoMetrics:
    base: dict[str, Any] = dict(views=100_000, likes=5_000, comments=200, shares=100, saves=50)
    base.update(kw)
    return VideoMetrics(**base)


def test_engagement_rate_weighted() -> None:
    # likes*1 + comments*2 + shares*3 + saves*4 = 5000+400+300+200 = 5900 / 100000
    assert engagement_rate(metrics(views=100_000)) == 5.9


def test_engagement_rate_zero_views() -> None:
    assert engagement_rate(metrics(views=0)) == 0.0


def test_viral_score_returns_breakdown_and_total() -> None:
    r = viral_score(metrics(), relevance=0.9, now=NOW, published_at=NOW - timedelta(hours=6))
    assert isinstance(r, ScoreResult)
    for comp in (r.performance, r.velocity, r.engagement, r.relevance, r.recency):
        assert 0.0 <= comp <= 100.0
    assert 0.0 <= r.total <= 100.0
    assert r.trend_decay in {"none", "low", "medium", "high"}


def test_viral_score_prefers_recent_over_old() -> None:
    recent = viral_score(metrics(), 0.9, NOW, NOW - timedelta(hours=6))
    old = viral_score(metrics(), 0.9, NOW, NOW - timedelta(days=90))
    assert recent.total > old.total
    assert old.trend_decay == "high"


def test_viral_score_zero_metrics_is_zeroish() -> None:
    r = viral_score(
        metrics(views=0, likes=0, comments=0, shares=0, saves=0),
        relevance=0.0,
        now=NOW,
        published_at=None,
    )
    assert r.total == 0.0
