from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, KeywordSignal


def test_admit_rejects_depth_beyond_max() -> None:
    guard = FanoutGuard(DepthConfig(max_depth=1))
    assert guard.admit("vela", depth=0) is True
    assert guard.admit("outra", depth=2) is False


def test_admit_dedupes_same_keyword_casefold() -> None:
    guard = FanoutGuard(DepthConfig())
    assert guard.admit("Vela Aromatica", depth=0) is True
    assert guard.admit("vela aromatica", depth=0) is False  # mesma kw, case/espaço


def test_admit_rejects_after_node_cap() -> None:
    guard = FanoutGuard(DepthConfig(max_nodes_per_cycle=2))
    assert guard.admit("a", depth=0) is True
    assert guard.admit("b", depth=0) is True
    assert guard.admit("c", depth=0) is False  # cap de 2 nós batido


def test_truncate_keeps_top_n_by_weight() -> None:
    guard = FanoutGuard(DepthConfig(fanout_per_stage={"web": 2}))
    signals = [
        KeywordSignal(keyword="a", source="web_suggest", weight=0.1),
        KeywordSignal(keyword="b", source="web_suggest", weight=0.9),
        KeywordSignal(keyword="c", source="web_suggest", weight=0.5),
    ]
    out = guard.truncate(signals, "web")
    assert [s.keyword for s in out] == ["b", "c"]


def test_truncate_without_configured_fanout_returns_all() -> None:
    guard = FanoutGuard(DepthConfig())
    signals = [KeywordSignal(keyword="a", source="web_suggest")]
    assert guard.truncate(signals, "web") == signals
