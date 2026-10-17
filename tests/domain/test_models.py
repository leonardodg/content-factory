from datetime import UTC, date, datetime

from content_factory.domain.models import (
    CandidateVideo,
    DepthConfig,
    Discovery,
    KeywordSignal,
    ResearchCampaign,
    SearchQuery,
    SearchQueryRun,
    SegmentConfig,
    SegmentTerm,
    TermTrendDay,
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


def test_segment_config_trends_anchor_opcional() -> None:
    """R3: a âncora do Trends vem do YAML; sem ela, o research usa a 1ª keyword."""
    seg = SegmentConfig(slug="candles", name="Velas", platforms=["web"], keywords=["vela"])
    assert seg.trends_anchor is None
    seg2 = SegmentConfig(
        slug="candles",
        name="Velas",
        platforms=["web"],
        keywords=["vela"],
        trends_anchor="vela aromatica",
    )
    assert seg2.trends_anchor == "vela aromatica"


def test_term_trend_day_fields() -> None:
    """R3: histórico por termo e dia — `segment_id` é do repositório, não do DTO."""
    d = TermTrendDay(
        term="vela aromatica",
        day=date(2026, 10, 20),
        trend_score=0.5,
        interest_mean=0.5,
        slope=0.0,
        anchor="vela",
        rules_version="trends-anchor-v1",
    )
    assert d.trend_score == 0.5
    assert d.anchor == "vela"
    assert (
        TermTrendDay(
            term="vela aromatica",
            day=date(2026, 10, 20),
            trend_score=None,
            interest_mean=None,
            slope=None,
            anchor="vela",
            rules_version="trends-anchor-v1",
        ).trend_score
        is None
    )


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


def test_search_query_discovery_e_run() -> None:
    q = SearchQuery(term="vela artesanal", origin="seed", query_id="search-plan-v1:0:0:seed")
    cand = CandidateVideo(
        platform="youtube",
        platform_video_id="v1",
        url="u",
        creator_handle="c",
        creator_platform_id="UC1",
    )
    d = Discovery(
        candidate=cand,
        term=q.term,
        origin=q.origin,
        query_id=q.query_id,
        result_position=1,
        run_kind="collect",
    )
    now = datetime(2026, 10, 20, tzinfo=UTC)
    run = SearchQueryRun(
        platform="youtube",
        query_id=q.query_id,
        term=q.term,
        origin=q.origin,
        results=0,
        kept=0,
        quota_used=100,
        started_at=now,
        finished_at=now,
    )
    assert d.candidate.creator_platform_id == "UC1"
    assert run.results == 0 and run.error_code is None  # busca vazia é registrada
