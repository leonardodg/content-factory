import pytest

from content_factory.domain.term_filter import (
    TERM_FILTER_VERSION,
    TermFilterConfig,
    filter_hashtag,
    filter_term,
    filter_terms,
)

VELAS = TermFilterConfig(
    anchors=("vela", "velas", "pavio", "parafina", "cera"),
    blocked=("tendencia alcista", "bajista", "grafico", "ignicao", "motor", "martillo"),
)
SABONETES = TermFilterConfig(
    anchors=("sabonete", "sabonetes", "saboaria", "glicerinada", "saponificacao"),
    blocked=("natura", "phebo", "dove", "lux", "protex", "granado"),
)
RESINA = TermFilterConfig(
    anchors=("resina", "epoxi", "epoxy"),
    blocked=("odontologica", "dente", "dentes", "clareia", "dental", "ortodontia", "mdf"),
)


def test_version() -> None:
    assert TERM_FILTER_VERSION == "term-filter-v1"


# Casos REAIS do Suggest (QA de 16/10, sondas probe-v2) + PT curto sem acento,
# que o Suggest devolve com frequência e NÃO pode cair por idioma.
@pytest.mark.parametrize(
    ("cfg", "term"),
    [
        (VELAS, "velas aromaticas"),
        (VELAS, "vela aromática"),
        (VELAS, "vela"),
        (VELAS, "como fazer vela de soja"),
        (VELAS, "pavio de madeira para vela"),
        (VELAS, "velas decorativas para casamento"),
        (SABONETES, "sabonete natural caseiro"),
        (SABONETES, "sabonete artesanal como fazer"),
        (RESINA, "resina epoxi para iniciantes"),
        (RESINA, "mesa de resina"),
    ],
)
def test_aceita_pt_do_nicho(cfg: TermFilterConfig, term: str) -> None:
    assert filter_term(term, cfg).accepted


@pytest.mark.parametrize(
    ("cfg", "term", "reason"),
    [
        (VELAS, "como hacer velas aromaticas", "lang"),  # espanhol com "como"
        (VELAS, "velas caseras faciles paso a paso", "lang"),
        (VELAS, "vela martillo en tendencia bajista", "blocked"),  # bloqueio vence
        (VELAS, "candle making for beginners", "lang"),
        (VELAS, "significado espiritual da cor", "no_anchor"),
        (VELAS, "envelope revelar", "no_anchor"),  # palavra inteira, não substring
        (SABONETES, "sabonete natura ekos", "blocked"),
        (SABONETES, "handmade soap for beginners", "lang"),
        (RESINA, "resina nao clareia", "blocked"),
        (RESINA, "resin art for beginners tutorial", "lang"),
        (RESINA, "artesanato em mdf", "blocked"),
    ],
)
def test_rejeita_com_motivo(cfg: TermFilterConfig, term: str, reason: str) -> None:
    verdict = filter_term(term, cfg)
    assert verdict.accepted is False
    assert verdict.reason == reason


def test_sem_ancoras_configuradas_nao_reprova_ancora() -> None:
    assert filter_term("qualquer coisa em portugues", TermFilterConfig()).accepted


def test_filter_terms_devolve_rejeitados_com_motivo() -> None:
    aceitos, rejeitados = filter_terms(["vela", "candle for x", "presente"], VELAS)
    assert aceitos == ["vela"]
    assert rejeitados == [("candle for x", "lang"), ("presente", "no_anchor")]


def test_hashtag_casa_ancora_por_substring_sem_idioma() -> None:
    assert filter_hashtag("velaaromatica", VELAS).accepted
    assert filter_hashtag("#VelasArtesanais", VELAS).accepted
    assert filter_hashtag("decoracao", VELAS).reason == "no_anchor"
    assert filter_hashtag("sabonetenatura", SABONETES).reason == "blocked"


# Revisão QA do PR #50: inglês sem palavra-função caía como `no_anchor`
# (descarte certo, motivo errado). Substantivos EN do nicho marcam o idioma.
@pytest.mark.parametrize(
    ("cfg", "term"),
    [
        (VELAS, "soy wax candles"),
        (VELAS, "candle wick too short"),
        (SABONETES, "handmade soap bars"),
        (SABONETES, "homemade soap gift ideas"),
        (RESINA, "resin tray mould"),
        (RESINA, "resin art workshop"),
    ],
)
def test_ingles_sem_palavra_funcao_cai_por_idioma(cfg: TermFilterConfig, term: str) -> None:
    assert filter_term(term, cfg).reason == "lang"


def test_substantivo_ingles_nao_casa_dentro_de_palavra_pt() -> None:
    # "resin" ≠ "resina", "wax" não aparece em PT: casamento por palavra inteira
    assert filter_term("resina para mesa", RESINA).accepted
