from datetime import UTC, datetime

from content_factory.domain.models import DepthConfig, KeywordSignal, SegmentConfig, TrendPoint


def test_keyword_signal_defaults() -> None:
    s = KeywordSignal(keyword="vela aromatica", source="web_suggest")
    assert s.weight == 0.0
    assert s.depth == 0


def test_trend_point_fields() -> None:
    p = TrendPoint(
        keyword="vela aromatica",
        period_start=datetime(2026, 10, 1, tzinfo=UTC),
        period_granularity="week",
        interest=42.5,
    )
    assert p.period_granularity == "week"


def test_depth_config_defaults() -> None:
    cfg = DepthConfig()
    assert cfg.max_depth == 2
    assert cfg.fanout_per_stage == {}
    assert cfg.max_nodes_per_cycle == 200
    assert cfg.recursion_speed_factor == 1.0


def test_segment_config_research_is_optional() -> None:
    seg = SegmentConfig(slug="candles", name="Velas", platforms=["web"], keywords=["vela"])
    assert seg.research is None
    seg2 = SegmentConfig(
        slug="candles",
        name="Velas",
        platforms=["web"],
        keywords=["vela"],
        research=DepthConfig(max_depth=3),
    )
    assert seg2.research is not None and seg2.research.max_depth == 3
