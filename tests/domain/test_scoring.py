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
        if comp is not None:  # None = métrica ausente (Fase G)
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


# --- G1: None vs Zero ---


def test_engagement_rate_none_views_returns_none() -> None:
    """views=None (métrica ausente) → engagement_rate devolve None, não 0.0."""
    from content_factory.domain.models import VideoMetrics

    m = VideoMetrics(views=None, likes=50, comments=None, shares=None, saves=None)
    assert engagement_rate(m) is None


def test_engagement_rate_zero_views_still_zero() -> None:
    """views=0 (métrica reportada como zero) → engagement_rate devolve 0.0."""
    from content_factory.domain.models import VideoMetrics

    m = VideoMetrics(views=0, likes=0, comments=0, shares=0, saves=0)
    assert engagement_rate(m) == 0.0


def test_viral_score_none_views_excludes_performance() -> None:
    """views=None → performance excluída da média (renormaliza pesos)."""
    from content_factory.domain.models import VideoMetrics

    m = VideoMetrics(views=None, likes=50, comments=None, shares=None, saves=None)
    r = viral_score(m, relevance=0.9, now=NOW, published_at=NOW - timedelta(hours=6))
    # performance não deve ser 0.0 (que seria "zero reportado") — deve ser None ou ausente
    assert r.performance is None or r.performance > 0.0
    # total ainda calculado (relevância + recency + velocity presentes)
    assert r.total > 0.0


def test_viral_score_all_none_metrics_uses_only_relevance_recency() -> None:
    """Todas as métricas None → só relevance/recency/velocity pontuam."""
    from content_factory.domain.models import VideoMetrics

    m = VideoMetrics(views=None, likes=None, comments=None, shares=None, saves=None)
    r = viral_score(m, relevance=1.0, now=NOW, published_at=NOW - timedelta(hours=6))
    assert r.total > 0.0  # relevância pura ainda pontua
    assert r.performance is None
    assert r.engagement is None
