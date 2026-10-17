"""F1 — protocolo fixo e reproduzível de consultas web (`web-queries-q1`).

Puro (sem I/O). Filtra as frases do Suggest (`filter-v1`: só PT, sem marca
industrial), classifica por intenção com léxico fechado, sorteia 3 por classe
com seed fixa (cego ao peso) + 1 consulta `seed` de controle, e roda em ordem
cíclica determinística.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from content_factory.domain.text import contains_phrase, normalize, word_tokens

INTENT_LEXICON_VERSION = "intent-v4"
QUERY_FILTER_VERSION = "filter-v2"
QUERY_PROTOCOL_VERSION = "q1"
QUERY_SEED = 20261017
WEB_QUERIES_PER_RUN = 3
NO_INTENT = "sem_intencao"

# Ordem documentada de precedência: frase que casa mais de uma classe vai
# para a PRIMEIRA nesta ordem — entre os marcadores ESPECÍFICOS. Os genéricos
# (`GENERIC_MARKERS`) só decidem quando nenhum específico casa (`intent-v4`).
INTENT_ORDER: tuple[str, ...] = ("aprender", "resolver", "tendencia", "comercial")

# Cada marcador é uma CONJUNÇÃO de frases: TODAS precisam aparecer no texto,
# como palavra inteira. Marcador de 1 frase = indicador direto; de 2+ =
# contextual, para palavras ambíguas (ex.: "ideias" só é tendência quando vem
# com contexto de atualidade/sazonalidade/popularidade). Ancorado no
# vocabulário real do Suggest; mudar o léxico sobe a versão (`intent-v4`).
IntentMarker = tuple[str, ...]

# `intent-v3`: o léxico é SÓ português (o experimento é em PT) e o "não" solto
# deixou de ser marcador. Ver o registro em `docs/manual/evidencia-sondas-suggest.md`.

# Falhas reais: "não" só é `resolver` junto de um desfecho ruim. Sem isto,
# "ou não", "velas que não pingam" e "sabonete não perfumado" (atributo
# desejável) entravam como problema.
_NEGATIVE_OUTCOMES = (
    "acende",
    "acender",
    "acesa",
    "queima",
    "queimou",
    "queimando",
    "endurece",
    "endureceu",
    "seca",
    "secou",
    "funciona",
    "funcionou",
    "encerado",
    "faz espuma",
    "da certo",
)

# `intent-v4` (revisão externa de 17/10): "como" é GENÉRICO — aparece em
# "como resolver…", "como vender…", "como evitar…". Com `aprender` avaliado
# primeiro, ele vencia o marcador específico e a frase de problema/venda saía
# como aprendizagem. Agora ele só decide quando nada específico casa.
GENERIC_MARKERS: frozenset[IntentMarker] = frozenset({("como",)})

INTENT_LEXICON: dict[str, tuple[IntentMarker, ...]] = {
    "aprender": (
        ("como",),
        ("passo a passo",),
        ("receita",),
        ("para iniciantes",),
        ("iniciante",),
        ("iniciantes",),
    ),
    "resolver": (
        ("por que",),
        ("para que serve",),
        ("o que fazer",),
        ("como resolver",),
        ("vale a pena",),
        ("funciona",),
        ("erro",),
        ("problema",),
        ("mesma coisa",),
        ("diferenca",),
        ("tem que",),
        ("tem cheiro",),
        ("duvida",),
        ("faz mal",),
        ("anvisa",),
        ("legislacao",),
        ("afunda",),
        ("rachou",),
        ("rachado",),
        ("manchada",),
        ("manchado",),
        # intent-v4: verbos/estados de problema que vinham depois de "como"
        ("evitar",),
        ("consertar",),
        ("corrigir",),
        ("bolha",),
        ("bolhas",),
        *(("nao", outcome) for outcome in _NEGATIVE_OUTCOMES),
    ),
    "tendencia": (
        ("tendencia",),
        ("tendencias",),
        ("novidade",),
        ("novidades",),
        ("em alta",),
        ("lancamento",),
        ("lancamentos",),
        ("mais vendidos",),
        ("mais vendido",),
        # ambíguas: só com contexto de atualidade/sazonalidade
        ("ideias", "2026"),
        ("ideias", "natal"),
        ("natal", "2026"),
        ("moda", "2026"),
    ),
    "comercial": (
        ("preco",),
        ("valor",),
        ("comprar",),
        ("vender",),
        ("lucro",),
        ("da lucro",),
        ("da dinheiro",),
        ("embalagem",),
        ("fornecedor",),
        ("atacado",),
        ("revenda",),
        ("loja",),
    ),
}

# --- `filter-v1`: filtros objetivos ANTES do sorteio ------------------------

# Palavras funcionais que não existem em PT: a frase que as contém não é
# portuguesa. Lista curta e conservadora (só o que é inequivocamente EN/ES);
# `de`/`para`/`que`/`como`/`la` ficam de fora porque também são PT.
FOREIGN_MARKERS: frozenset[str] = frozenset(
    {
        # inglês
        "for",
        "the",
        "and",
        "with",
        "without",
        "your",
        "you",
        "how",
        "what",
        "why",
        "best",
        "is",
        "are",
        "of",
        "to",
        "from",
        "this",
        "that",
        "my",
        "it",
        "not",
        "or",
        "if",
        "can",
        "will",
        "does",
        "into",
        "about",
        "beginners",
        "recipe",
        "recipes",
        # `filter-v2`: "tutorial" saiu — é palavra corrente em PT ("tutorial de
        # vela aromática"); frase em inglês com ela é barrada por outro marcador
        "guide",
        "ideas",
        "wholesale",
        "sale",
        "sell",
        "price",
        "safe",
        "benefits",
        "bad",
        "short",
        "too",
        "free",
        "download",
        "making",
        # conteúdo típico do nicho em inglês (pega frases sem palavra funcional,
        # ex.: "cold process soap vs hot process soap")
        "soap",
        "candle",
        "candles",
        "wick",
        "wax",
        "handmade",
        "melt",
        "pour",
        "lye",
        "cold",
        "hot",
        "crafts",
        "tray",
        "molds",
        "keychains",
        # espanhol
        "en",
        "con",
        "sin",
        "los",
        "las",
        "del",
        "el",
        "y",
        "son",
        "una",
        "uno",
        "alcista",
        "bajista",
        "martillo",
        "cambio",
    }
)

# Marcas industriais de PRODUTO ACABADO: fora do nicho artesanal. Fornecedores
# de matéria-prima do artesão (ex.: "nossa terra") NÃO entram.
BLOCKED_BRANDS: frozenset[str] = frozenset(
    {
        "natura",
        "boticario",
        "phebo",
        "granado",
        "dove",
        "avon",
        "eudora",
        "jequiti",
        "ngk",
        "voluspa",
        "malone",
        "bulhoes",
    }
)


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
    filter_version: str = QUERY_FILTER_VERSION
    missing: dict[str, int] = field(default_factory=dict[str, int])
    vetoes: dict[str, str] = field(default_factory=dict[str, str])


def marker_matches(words: list[str], marker: IntentMarker) -> bool:
    """Todas as frases do marcador aparecem em `words` (conjunção, palavra inteira)."""
    return all(contains_phrase(words, phrase) for phrase in marker)


def is_eligible(phrase: str) -> bool:
    """Filtro objetivo `filter-v1`: frase em PT e sem marca industrial.

    Aplicado ANTES do sorteio — frase filtrada não entra no protocolo nem
    como substituta de uma vetada.
    """
    words = set(word_tokens(phrase))
    return not (words & FOREIGN_MARKERS) and not (words & BLOCKED_BRANDS)


def eligible_phrases(phrases: Sequence[str]) -> list[str]:
    """As frases que passam no `filter-v1`, na ordem de entrada."""
    return [phrase for phrase in phrases if is_eligible(phrase)]


def classify_intent(phrase: str) -> str | None:
    """Classe de intenção da frase, na ordem de precedência; `None` = sem.

    Cada marcador casa por palavra inteira (`contains_phrase`); marcadores de
    2+ frases exigem TODAS (indicador contextual).
    """
    words = word_tokens(phrase)
    for generic in (False, True):  # específicos primeiro; genéricos de fallback
        for intent in INTENT_ORDER:
            markers = (m for m in INTENT_LEXICON[intent] if (m in GENERIC_MARKERS) is generic)
            if any(marker_matches(words, marker) for marker in markers):
                return intent
    return None


def intent_coverage(phrases: Sequence[str]) -> dict[str, int]:
    """Contagem de frases por classe (`sem_intencao` incluída), ordem estável.

    Base das tabelas de cobertura (por segmento e por classe) — usada pelo
    script de medição das sondas (`scripts/measure_suggest_probes.py`).
    """
    counts = {intent: 0 for intent in INTENT_ORDER}
    counts[NO_INTENT] = 0
    for phrase in phrases:
        counts[classify_intent(phrase) or NO_INTENT] += 1
    return counts


def rank_intent_phrases(signals: Sequence[str], *, seed: int = QUERY_SEED) -> dict[str, list[str]]:
    """Por classe, as frases embaralhadas com seed fixa (ordem do sorteio).

    Só entram frases aprovadas pelo `filter-v1` (`is_eligible`). A ordem não
    olha o peso: entram só as frases, ordenadas por texto antes do shuffle
    para o resultado ser independente da ordem de entrada.
    """
    pools: dict[str, list[str]] = {intent: [] for intent in INTENT_ORDER}
    seen: set[tuple[str, str]] = set()
    for phrase in signals:
        if not is_eligible(phrase):
            continue
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
        "filter_version": protocol.filter_version,
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
        filter_version=str(data.get("filter_version", QUERY_FILTER_VERSION)),
        queries=queries,
        missing=missing,
        vetoes=vetoes,
    )
