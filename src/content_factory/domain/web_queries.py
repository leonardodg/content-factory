"""F1 — protocolo fixo e reproduzível de consultas web (`web-queries-q1`).

Puro (sem I/O). Classifica as frases do Suggest por intenção com léxico
fechado, sorteia 3 por classe com seed fixa (cego ao peso) + 1 consulta
`seed` de controle, e roda em ordem cíclica determinística.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from content_factory.domain.text import contains_phrase, normalize, word_tokens

INTENT_LEXICON_VERSION = "intent-v1"
QUERY_PROTOCOL_VERSION = "q1"
QUERY_SEED = 20261017
WEB_QUERIES_PER_RUN = 3
NO_INTENT = "sem_intencao"

# Ordem documentada de precedência: frase que casa mais de uma classe vai
# para a PRIMEIRA nesta ordem.
INTENT_ORDER: tuple[str, ...] = ("aprender", "resolver", "tendencia", "comercial")

INTENT_LEXICON: dict[str, tuple[str, ...]] = {
    "aprender": ("como", "passo a passo", "tutorial", "receita", "para iniciantes"),
    "resolver": ("erro", "não", "por que", "problema", "afunda", "rachou", "manchada", "derrete"),
    "tendencia": ("tendência", "2026", "moda", "novidade", "ideias"),
    "comercial": (
        "preço",
        "vender",
        "lucro",
        "dá dinheiro",
        "embalagem",
        "fornecedor",
        "comprar",
    ),
}


@dataclass(frozen=True)
class PlannedQuery:
    query_id: str
    query: str
    intent: str  # aprender|resolver|tendencia|comercial|seed


@dataclass(frozen=True)
class WebQueryProtocol:
    """Protocolo congelado `q1` de um segmento (o YAML revisado pelo usuário)."""

    segment_slug: str
    version: str
    seed: int
    lexicon_version: str
    queries: list[PlannedQuery]
    missing: dict[str, int] = field(default_factory=dict[str, int])
    vetoes: dict[str, str] = field(default_factory=dict[str, str])


def classify_intent(phrase: str) -> str | None:
    """Classe de intenção da frase, na ordem de precedência; `None` = sem.

    Marcador casa por palavra inteira (`contains_phrase`), não por substring.
    """
    words = word_tokens(phrase)
    for intent in INTENT_ORDER:
        if any(contains_phrase(words, marker) for marker in INTENT_LEXICON[intent]):
            return intent
    return None


def rank_intent_phrases(signals: Sequence[str], *, seed: int = QUERY_SEED) -> dict[str, list[str]]:
    """Por classe, as frases embaralhadas com seed fixa (ordem do sorteio).

    A ordem não olha o peso: entram só as frases, ordenadas por texto antes
    do shuffle para o resultado ser independente da ordem de entrada.
    """
    pools: dict[str, list[str]] = {intent: [] for intent in INTENT_ORDER}
    seen: set[tuple[str, str]] = set()
    for phrase in signals:
        intent = classify_intent(phrase)
        if intent is None:
            continue
        key = (intent, normalize(phrase))
        if key in seen:
            continue
        seen.add(key)
        pools[intent].append(phrase)
    rng = random.Random(seed)  # nosec B311 - reprodução por seed fixa
    ranked: dict[str, list[str]] = {}
    for intent in INTENT_ORDER:
        pool = sorted(pools[intent], key=normalize)
        rng.shuffle(pool)
        ranked[intent] = pool
    return ranked


def select_queries(
    signals: Sequence[str],
    keywords: Sequence[str],
    *,
    per_class: int = WEB_QUERIES_PER_RUN,
    seed: int = QUERY_SEED,
    vetoes: Mapping[str, str] | None = None,
) -> tuple[list[PlannedQuery], dict[str, int]]:
    """Seleção estratificada e cega: `per_class` por classe + 1 `seed`.

    Frase vetada (chave = `normalize(frase)`) é pulada; a substituta é a
    próxima do sorteio. Devolve as consultas e o nº faltante por classe.
    """
    vetoed = {normalize(k) for k in (vetoes or {})}
    ranked = rank_intent_phrases(signals, seed=seed)
    queries: list[PlannedQuery] = []
    missing: dict[str, int] = {}
    for intent in INTENT_ORDER:
        available = [p for p in ranked[intent] if normalize(p) not in vetoed]
        chosen = available[:per_class]
        if len(chosen) < per_class:
            missing[intent] = per_class - len(chosen)
        for index, phrase in enumerate(chosen):
            queries.append(
                PlannedQuery(
                    query_id=f"{QUERY_PROTOCOL_VERSION}:{intent}:{index:02d}",
                    query=phrase,
                    intent=intent,
                )
            )
    seed_query = " OR ".join(keywords[:3])
    if seed_query:
        queries.append(
            PlannedQuery(
                query_id=f"{QUERY_PROTOCOL_VERSION}:seed:00", query=seed_query, intent="seed"
            )
        )
    return queries, missing


def plan_web_queries(
    protocol: Sequence[PlannedQuery], run_index: int, n: int = WEB_QUERIES_PER_RUN
) -> list[PlannedQuery]:
    """`n` consultas do protocolo em ordem cíclica determinística."""
    total = len(protocol)
    if total == 0 or n <= 0:
        return []
    start = (run_index * n) % total
    return [protocol[(start + i) % total] for i in range(min(n, total))]


def run_index_for(now: datetime) -> int:
    """Índice do run pela data/hora: 2 coletas/dia (manhã e noite)."""
    return now.date().toordinal() * 2 + (0 if now.hour < 12 else 1)


def protocol_to_dict(protocol: WebQueryProtocol) -> dict[str, object]:
    """Protocolo → dict serializável (o YAML `q1` congelado no commit)."""
    return {
        "segment": protocol.segment_slug,
        "version": protocol.version,
        "seed": protocol.seed,
        "lexicon_version": protocol.lexicon_version,
        "queries": [
            {"id": q.query_id, "query": q.query, "intent": q.intent} for q in protocol.queries
        ],
        "missing": dict(protocol.missing),
        "vetoes": dict(protocol.vetoes),
    }


def _as_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in cast("dict[object, object]", value).items()}


def protocol_from_dict(data: Mapping[str, object]) -> WebQueryProtocol:
    """dict do YAML → `WebQueryProtocol`; levanta `ValueError` se malformado."""
    raw_queries = data.get("queries")
    if not isinstance(raw_queries, list):
        raise ValueError("protocolo sem lista 'queries'")
    queries: list[PlannedQuery] = []
    for item in cast("list[object]", raw_queries):
        entry = _as_map(item)
        if not entry:
            raise ValueError("query do protocolo não é um mapa")
        queries.append(
            PlannedQuery(
                query_id=str(entry.get("id", "")),
                query=str(entry.get("query", "")),
                intent=str(entry.get("intent", "")),
            )
        )
    missing = {k: int(str(v)) for k, v in _as_map(data.get("missing")).items()}
    vetoes = {k: str(v) for k, v in _as_map(data.get("vetoes")).items()}
    return WebQueryProtocol(
        segment_slug=str(data.get("segment", "")),
        version=str(data.get("version", QUERY_PROTOCOL_VERSION)),
        seed=int(str(data.get("seed", QUERY_SEED))),
        lexicon_version=str(data.get("lexicon_version", INTENT_LEXICON_VERSION)),
        queries=queries,
        missing=missing,
        vetoes=vetoes,
    )
