from datetime import UTC, datetime

from content_factory.app.score import relevance_of
from content_factory.domain.models import VideoMetrics, VideoSnapshot
from content_factory.domain.scoring import viral_score
from content_factory.domain.web_relevance import (
    WEB_WEIGHTS,
    WebPageInput,
    WebScore,
    baseline_web_score,
    dedupe_titles,
    select_top,
    web_score,
)


def page(**kw: object) -> WebPageInput:
    base: dict[str, object] = dict(
        video_id=1,
        title="Tutorial completo de velas artesanais de soja",
        snippet="Neste guia passo a passo você aprende a fazer velas "
        "artesanais de cera de soja do zero, com dicas de aroma.",
        keywords=["vela artesanal", "cera de soja"],
        canonical_url="https://ex.com/vela",
        canonical_ok=True,
        query_intent="aprender",
        best_position=3,
        total_positions=10,
    )
    base.update(kw)
    return WebPageInput(**base)  # type: ignore[arg-type]


def test_web_score_is_deterministic() -> None:
    a = web_score(page())
    b = web_score(page())
    assert a.score == b.score
    assert a.components == b.components
    assert a.explanation == b.explanation


def test_score_is_weighted_mean_of_evaluated_only() -> None:
    # só metadados avaliado (sem keywords/intenção/posição/data) → score = metadados*100
    s = web_score(
        page(
            title="Um título suficientemente longo",
            snippet="x" * 80,
            keywords=[],
            query_intent="",
            best_position=None,
            total_positions=0,
        )
    )
    assert s.components == {
        "tema": None,
        "intencao": None,
        "metadados": 1.0,
        "atualidade": None,
        "posicao": None,
    }
    assert s.score == 100.0


def test_score_renormalizes_partial_components() -> None:
    s = web_score(page())
    assert s.score is not None
    evaluated = {k: v for k, v in s.components.items() if v is not None}
    expected = round(
        sum(v * WEB_WEIGHTS[k] for k, v in evaluated.items())
        / sum(WEB_WEIGHTS[k] for k in evaluated)
        * 100,
        2,
    )
    assert s.score == expected


def test_theme_matches_keyword_by_all_tokens() -> None:
    s = web_score(page(keywords=["cera de soja"]))
    # título/snippet têm "cera de soja" → casa
    assert s.explanation["tema"]["matched"] == ["cera de soja"]


def test_theme_token_mismatch_does_not_match() -> None:
    s = web_score(page(title="Como usar soja", snippet="soja " * 30, keywords=["cera de soja"]))
    assert s.explanation["tema"]["matched"] == []


def test_theme_counts_repeated_keyword_once() -> None:
    s = web_score(
        page(
            title="Guia de vela artesanal",
            snippet="vela artesanal " * 8,
            keywords=["vela artesanal", "VELA ARTESANAL", "vela  artesanal"],
        )
    )
    assert s.explanation["tema"]["matched"] == ["vela artesanal"]


def test_theme_is_accent_and_case_insensitive() -> None:
    s = web_score(
        page(
            title="RESINA EPÓXI",
            snippet="resina epóxi para iniciantes " * 4,
            keywords=["resina epoxi"],
        )
    )
    assert s.explanation["tema"]["matched"] == ["resina epoxi"]


def test_theme_none_without_keywords() -> None:
    assert web_score(page(keywords=[])).components["tema"] is None


def test_intent_uses_marker_of_same_class() -> None:
    s = web_score(page(query_intent="resolver", title="Por que a resina rachou", snippet="x" * 70))
    assert s.components["intencao"] == 1.0
    assert s.explanation["intencao"]["marker"] == "por que"


def test_intent_none_for_seed_or_unknown_class() -> None:
    assert web_score(page(query_intent="seed")).components["intencao"] is None
    assert web_score(page(query_intent="")).components["intencao"] is None


def test_metadata_never_none_and_empty_snippet_fails() -> None:
    s = web_score(page(snippet=""))
    assert s.components["metadados"] is not None
    assert s.explanation["metadados"]["snippet_ok"] is False


def test_position_none_without_hit() -> None:
    assert web_score(page(best_position=None)).components["posicao"] is None


def test_position_best_first() -> None:
    assert web_score(page(best_position=1, total_positions=10)).components["posicao"] == 1.0


def test_select_top_does_not_change_scores() -> None:
    items = [web_score(page(video_id=i, title=f"t {i} " + "x" * 20)) for i in range(5)]
    before = {i.video_id: i.score for i in items}
    top = select_top(items, 3)
    assert {i.video_id: i.score for i in top} == {
        k: before[k] for k in before if k in {i.video_id for i in top}
    }


def test_select_top_max_per_domain() -> None:
    items = [web_score(page(video_id=i, canonical_url=f"https://ex.com/{i}")) for i in range(4)] + [
        web_score(page(video_id=9, canonical_url="https://outro.com/x"))
    ]
    top = select_top(items, 3, max_per_domain=2)
    domains = [i.domain for i in top]
    assert domains.count("ex.com") <= 2
    assert len(top) == 3


def test_dedupe_titles_keeps_highest_score() -> None:
    low = WebScore(video_id=1, title="Igual", domain="a", score=10.0)
    high = WebScore(video_id=2, title="igual ", domain="b", score=90.0)
    kept, dropped = dedupe_titles([low, high])
    assert [k.video_id for k in kept] == [2]
    assert dropped == {1: 2}


def test_baseline_equivalence_with_viral_score() -> None:
    """A linha de base reproduz `domain/scoring.py` na data do congelamento."""
    p = page()
    snap = VideoSnapshot(
        video_id=p.video_id,
        title=p.title,
        description=p.snippet,
        metrics=VideoMetrics(),
    )
    expected = viral_score(
        VideoMetrics(),
        relevance_of(snap, p.keywords),
        datetime(1970, 1, 1, tzinfo=UTC),
        None,
    ).total
    assert baseline_web_score(p) == expected
