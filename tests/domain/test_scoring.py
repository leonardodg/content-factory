from datetime import UTC, datetime, timedelta
from typing import Any

from content_factory.domain.models import ScoreResult, VideoMetrics
from content_factory.domain.scoring import engagement_rate, momentum_score, viral_score

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


def test_engagement_rate_views_known_but_no_submetric_reported_returns_none() -> None:
    """views conhecido mas likes/comments/shares/saves todos ausentes → None, não 0.0.

    Simétrico ao caso de views=None: sem NENHUM sinal de engajamento
    reportado, 0.0 mentiria "engajamento zero" quando a verdade é
    "plataforma não informou nenhuma submétrica de engajamento".
    """
    from content_factory.domain.models import VideoMetrics

    m = VideoMetrics(views=100_000, likes=None, comments=None, shares=None, saves=None)
    assert engagement_rate(m) is None


def test_engagement_rate_partial_submetrics_sums_only_available() -> None:
    """Cobertura parcial (ex.: YouTube sem shares/saves): soma só o disponível,
    sem extrapolar peso — extrapolar exigiria calibração com dados reais
    (deliberadamente fora de escopo, ver roadmap Fase G)."""
    from content_factory.domain.models import VideoMetrics

    m = VideoMetrics(views=100_000, likes=5_000, comments=200, shares=None, saves=None)
    # likes*1 + comments*2 = 5000 + 400 = 5400 / 100000 * 100
    assert engagement_rate(m) == 5.4


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


def test_viral_score_metrics_available_lists_present_metrics() -> None:
    """G2: ScoreResult.metrics_available lista só as métricas com valor real."""
    m = metrics(views=100_000, likes=5_000, comments=None, shares=None, saves=None)
    r = viral_score(m, relevance=0.5, now=NOW, published_at=NOW - timedelta(hours=6))
    assert r.metrics_available == ["views", "likes"]


def test_viral_score_metrics_available_empty_when_all_none() -> None:
    from content_factory.domain.models import VideoMetrics

    m = VideoMetrics(views=None, likes=None, comments=None, shares=None, saves=None)
    r = viral_score(m, relevance=0.5, now=NOW, published_at=NOW - timedelta(hours=6))
    assert r.metrics_available == []


# --- H Task 3: velocity mede crescimento real entre snapshots ---


def test_momentum_score_none_without_previous_snapshot() -> None:
    """Sem captura anterior não há par comparável → velocity None."""
    assert momentum_score(metrics(views=1000), None, NOW, None) is None


def test_momentum_score_none_when_views_missing_in_either_snapshot() -> None:
    """views ausente em qualquer um dos snapshots → None (ausência ≠ zero)."""
    prev = VideoMetrics(views=None)
    assert momentum_score(metrics(views=1000), prev, NOW, NOW - timedelta(hours=1)) is None


def test_momentum_score_none_when_interval_not_positive() -> None:
    """Intervalo zero/negativo entre capturas (relógio incoerente) → None."""
    prev = VideoMetrics(views=1000)
    assert momentum_score(metrics(views=2000), prev, NOW, NOW) is None


def test_momentum_score_computes_views_per_hour_rate() -> None:
    """1000 views em 1h = 1000 views/h = cap → 100.0."""
    prev = VideoMetrics(views=1000)
    cur = VideoMetrics(views=2000)
    assert momentum_score(cur, prev, NOW, NOW - timedelta(hours=1)) == 100.0


def test_momentum_score_below_cap_scales_linearly() -> None:
    """100 views em 2h = 50 views/h = 5% do cap (1000) → 5.0."""
    prev = VideoMetrics(views=1000)
    cur = VideoMetrics(views=1100)
    assert momentum_score(cur, prev, NOW, NOW - timedelta(hours=2)) == 5.0


def test_momentum_score_negative_delta_clamped_to_zero() -> None:
    """views 'caiu' (correção de contagem da plataforma) → 0.0, não negativo."""
    prev = VideoMetrics(views=5000)
    cur = VideoMetrics(views=4000)
    assert momentum_score(cur, prev, NOW, NOW - timedelta(hours=1)) == 0.0


def test_viral_score_velocity_none_without_snapshot_history() -> None:
    """Sem previous_metrics/captured_at → velocity None (não inventa idade)."""
    r = viral_score(metrics(), 0.9, NOW, NOW - timedelta(hours=6))
    assert r.velocity is None


def test_viral_score_velocity_from_real_momentum() -> None:
    """Velocity vem do diff de views entre snapshots, não da idade."""
    prev = VideoMetrics(views=1000)
    cur = metrics(views=2000)
    r = viral_score(
        cur,
        0.9,
        NOW,
        NOW - timedelta(hours=6),
        captured_at=NOW,
        previous_metrics=prev,
        previous_captured_at=NOW - timedelta(hours=1),
    )
    assert r.velocity == 100.0


def test_viral_score_missing_published_at_does_not_invent_velocity() -> None:
    """published_at=None não força mais velocity=20 (age_days=999 fake)."""
    r = viral_score(metrics(), 0.9, NOW, None)
    assert r.velocity is None
