"""Opcao (a) da revisao 16/10: sondar o Suggest pela INTENCAO.

Semeado so com o nome do segmento, o autocompletar devolve variacoes de
produto (quase nada de pergunta de problema nem de tendencia). As sondas
levam a intencao para o Google completar com frases REAIS dessas classes —
e so rendem na CABECA do termo ("vela"), nao na cauda longa ("vela
artesanal"), por isso os termos vem de `SegmentConfig.probe_terms`.
"""

from content_factory.domain.suggest_probes import (
    INTENT_PROBES,
    SUGGEST_PROBE_VERSION,
    suggest_probes,
)
from content_factory.domain.web_queries import classify_intent

# 5 modelos de sonda (aprender 1, resolver 1, tendencia 2, comercial 1)
_MODELS = sum(len(templates) for templates in INTENT_PROBES.values())


def test_probes_cover_the_four_intents_for_each_term() -> None:
    probes = suggest_probes(["vela", "velas", "pavio"])
    assert len(probes) == _MODELS * 3
    assert "como fazer vela" in probes  # aprender
    assert "vela nao" in probes  # resolver
    assert "vela tendencia" in probes  # tendencia
    assert "vela mais vendido" in probes  # tendencia
    assert "vela preco" in probes  # comercial
    assert "pavio nao" in probes


def test_probe_families_align_with_the_lexicon() -> None:
    """Cada família de sonda semeia o vocabulário que o léxico reconhece.

    A sonda em si é um PREFIXO de busca (não precisa classificar); o que
    precisa classificar é o que o autocompletar devolve. Aqui usamos um
    exemplo real de cada família.
    """
    representative = {
        "como fazer vela": "aprender",
        "vela nao acende": "resolver",  # "não" sozinho não basta no `intent-v3`
        "vela tendencia": "tendencia",
        "vela mais vendido": "tendencia",
        "vela preco": "comercial",
    }
    for phrase, intent in representative.items():
        assert classify_intent(phrase) == intent


def test_probes_are_deterministic_and_deduped() -> None:
    assert suggest_probes(["vela", "Vela", "vela"]) == suggest_probes(["vela"])
    probes = suggest_probes(["vela", "vela"])
    assert len(probes) == len(set(probes))


def test_probes_empty_without_terms() -> None:
    assert suggest_probes([]) == []
    assert suggest_probes(["   "]) == []


def test_probe_version_is_v2() -> None:
    assert SUGGEST_PROBE_VERSION == "probe-v2"
