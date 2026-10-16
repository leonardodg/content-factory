"""F1 `app.web_queries_plan`: protocolo `q1` congelado (build/write/load)."""

from pathlib import Path
from typing import cast

import yaml

from content_factory.app.web_queries_plan import build_protocol, dump_protocol_yaml, write_protocol
from content_factory.domain.web_queries import (
    QUERY_PROTOCOL_VERSION,
    QUERY_SEED,
    WebQueryProtocol,
    protocol_from_dict,
)
from content_factory.infra.collectors.web_queries import load_protocol

SIGNALS = [
    "como fazer velas",  # aprender
    "vela rachou",  # resolver
    "tendencia velas 2026",  # tendencia
    "preco da cera",  # comercial
    "vela artesanal passo a passo",  # aprender
    "por que a vela afunda",  # resolver
    "moda em velas",  # tendencia — isolada não classifica (ambígua)
    "velas em alta",  # tendencia
    "lucro com velas",  # comercial
    "receita de vela",  # aprender
    "problema no pavio",  # resolver
    "novidade velas",  # tendencia
    "vender velas",  # comercial (mas 'preco' ganha precedencia? não: palavras distintas)
]
KEYWORDS = ["vela artesanal", "ceras", "pavio"]


def test_build_protocol_stratifies_by_intent_and_adds_seed() -> None:
    protocol = build_protocol("velas", SIGNALS, KEYWORDS)

    assert protocol.segment_slug == "velas"
    assert protocol.version == QUERY_PROTOCOL_VERSION
    assert protocol.seed == QUERY_SEED
    assert protocol.missing == {}
    intents = [q.intent for q in protocol.queries]
    # 3 por classe + 1 seed
    for intent in ("aprender", "resolver", "tendencia", "comercial"):
        assert intents.count(intent) == 3
    assert intents.count("seed") == 1
    # a consulta de controle usa as keywords do segmento
    seed_query = next(q for q in protocol.queries if q.intent == "seed")
    assert "vela artesanal" in seed_query.query


def test_veto_records_reason_and_pulls_the_next_phrase() -> None:
    baseline = build_protocol("velas", SIGNALS, KEYWORDS)
    vetoed = baseline.queries[0].query

    protocol = build_protocol("velas", SIGNALS, KEYWORDS, vetoes={vetoed: "não é do segmento"})

    assert protocol.vetoes == {vetoed: "não é do segmento"}
    assert all(q.query != vetoed for q in protocol.queries)


def test_missing_counts_classes_with_fewer_phrases() -> None:
    protocol = build_protocol("velas", ["como fazer velas"], KEYWORDS)
    assert protocol.missing["aprender"] == 2  # só 1 frase disponível
    assert any(q.intent == "aprender" for q in protocol.queries)


def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    protocol = build_protocol("velas", SIGNALS, KEYWORDS)
    path = tmp_path / "velas.q1.yaml"
    write_protocol(path, protocol)

    assert protocol_from_dict(_via_yaml(protocol)) == protocol
    assert load_protocol(path) == protocol


def _via_yaml(protocol: WebQueryProtocol) -> dict[str, object]:
    """Serializa → YAML → dict, exercitando o mesmo caminho do arquivo real."""
    return cast("dict[str, object]", yaml.safe_load(dump_protocol_yaml(protocol)))
