from datetime import UTC, datetime, timedelta
from functools import partial

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

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
# o relógio entra por parâmetro: `domain/` não lê a hora (reprodutível)
_ws = partial(web_score, now=NOW)


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
    a = _ws(page())
    b = _ws(page())
    assert a.score == b.score
    assert a.components == b.components
    assert a.explanation == b.explanation


def test_score_is_weighted_mean_of_evaluated_only() -> None:
    # só metadados avaliado (sem keywords/intenção/posição/data) → score = metadados*100
    s = _ws(
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
    s = _ws(page())
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
    s = _ws(page(keywords=["cera de soja"]))
    # título/snippet têm "cera de soja" → casa
    assert s.explanation["tema"]["matched"] == ["cera de soja"]


def test_theme_token_mismatch_does_not_match() -> None:
    s = _ws(page(title="Como usar soja", snippet="soja " * 30, keywords=["cera de soja"]))
    assert s.explanation["tema"]["matched"] == []


def test_theme_counts_repeated_keyword_once() -> None:
    s = _ws(
        page(
            title="Guia de vela artesanal",
            snippet="vela artesanal " * 8,
            keywords=["vela artesanal", "VELA ARTESANAL", "vela  artesanal"],
        )
    )
    assert s.explanation["tema"]["matched"] == ["vela artesanal"]


def test_theme_is_accent_and_case_insensitive() -> None:
    s = _ws(
        page(
            title="RESINA EPÓXI",
            snippet="resina epóxi para iniciantes " * 4,
            keywords=["resina epoxi"],
        )
    )
    assert s.explanation["tema"]["matched"] == ["resina epoxi"]


def test_theme_none_without_keywords() -> None:
    assert _ws(page(keywords=[])).components["tema"] is None


def test_intent_uses_marker_of_same_class() -> None:
    s = _ws(page(query_intent="resolver", title="Por que a resina rachou", snippet="x" * 70))
    assert s.components["intencao"] == 1.0
    assert s.explanation["intencao"]["marker"] == "por que"


def test_intent_none_for_seed_or_unknown_class() -> None:
    assert _ws(page(query_intent="seed")).components["intencao"] is None
    assert _ws(page(query_intent="")).components["intencao"] is None


def test_intent_ignores_markers_that_rest_on_generic_words() -> None:
    # "não" aparece em quase todo texto em PT: marcador que depende dele não é
    # prova de intenção na PÁGINA (no `intent-v3` ele só vale na frase do
    # Suggest, junto de um desfecho ruim).
    s = _ws(page(query_intent="resolver", title="Vela que não acende", snippet="x" * 70))
    assert s.components["intencao"] == 0.0
    assert s.explanation["intencao"]["marker"] is None


def test_metadata_never_none_and_empty_snippet_fails() -> None:
    s = _ws(page(snippet=""))
    assert s.components["metadados"] is not None
    assert s.explanation["metadados"]["snippet_ok"] is False


def test_position_none_without_hit() -> None:
    assert _ws(page(best_position=None)).components["posicao"] is None


def test_position_best_first() -> None:
    assert _ws(page(best_position=1, total_positions=10)).components["posicao"] == 1.0


def test_select_top_does_not_change_scores() -> None:
    items = [_ws(page(video_id=i, title=f"t {i} " + "x" * 20)) for i in range(5)]
    before = {i.video_id: i.score for i in items}
    top = select_top(items, 3)
    assert {i.video_id: i.score for i in top} == {
        k: before[k] for k in before if k in {i.video_id for i in top}
    }


def test_select_top_max_per_domain() -> None:
    items = [_ws(page(video_id=i, canonical_url=f"https://ex.com/{i}")) for i in range(4)] + [
        _ws(page(video_id=9, canonical_url="https://outro.com/x"))
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


def test_baseline_is_frozen_literal_values() -> None:
    """Valores literais congelados: a base não pode seguir o score de vídeo.

    Sem keywords → relevance 0 → recency 50 → 25,0. Keyword única que casa
    inteira → relevance 1 → 25 + 50 = 75,0. Se a fórmula do vídeo mudar, este
    teste continua verde (a base está congelada) e o de equivalência acima é
    que acusa a deriva.
    """
    assert baseline_web_score(page(keywords=[])) == 25.0
    assert baseline_web_score(page(title="Velas artesanais", keywords=["velas artesanais"])) == 75.0


def test_dedupe_titles_keeps_every_empty_title() -> None:
    """Título vazio não é duplicata de nada: nenhuma página some sem rastro."""
    a = WebScore(video_id=1, title="", domain="a", score=50.0)
    b = WebScore(video_id=2, title="  ", domain="b", score=40.0)
    c = WebScore(video_id=3, title="X", domain="c", score=30.0)
    kept, dropped = dedupe_titles([a, b, c])
    assert sorted(k.video_id for k in kept) == [1, 2, 3]
    assert dropped == {}


def test_recency_uses_injected_clock_not_wall_clock() -> None:
    published = NOW - timedelta(days=2)
    fresh = web_score(page(published_at=published), now=NOW)
    later = web_score(page(published_at=published), now=NOW + timedelta(days=400))
    assert fresh.components["atualidade"] == 1.0
    assert later.components["atualidade"] != fresh.components["atualidade"]


def test_theme_matches_whole_words_not_substrings() -> None:
    # "uv" não pode casar dentro de "luva"
    s = _ws(page(title="Luva de proteção para resina", snippet="luva " * 20, keywords=["uv"]))
    assert s.components["tema"] == 0.0
    assert s.explanation["tema"]["matched"] == []


def test_intent_marker_must_be_whole_word() -> None:
    # "ferro" contém "erro", mas não é marcador de resolver
    s = _ws(page(query_intent="resolver", title="Ferro de solda para resina", snippet="x" * 70))
    assert s.components["intencao"] == 0.0


def test_intent_ignores_generic_negation_on_page_text() -> None:
    # "não" aparece em quase todo texto em PT: não prova intenção de resolver
    s = _ws(
        page(
            query_intent="resolver",
            title="Resina epóxi para mesas",
            snippet="Você não precisa de muito material para começar " * 2,
        )
    )
    assert s.components["intencao"] == 0.0
