from datetime import UTC, datetime

from content_factory.domain.models import (
    DepthConfig,
    KeywordSignal,
    ResearchCampaign,
    SegmentConfig,
    SegmentTerm,
    TrendPoint,
)


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


def test_research_campaign_defaults() -> None:
    now = datetime.now(UTC)
    c = ResearchCampaign(id=1, segment_id=2, started_at=now, ends_at=now)
    assert c.active is True


def test_segment_config_tem_ancoras_e_bloqueios_por_padrao() -> None:
    cfg = SegmentConfig(slug="s", name="S", platforms=["youtube"], keywords=["k"])
    assert cfg.anchor_terms == []
    assert cfg.blocked_terms == []


def test_segment_term_valores_por_padrao() -> None:
    t = SegmentTerm(segment_id=1, term="vela", origin="suggest")
    assert t.status == "active"
    assert t.exclude_reason is None
    assert t.kw_rank is None
