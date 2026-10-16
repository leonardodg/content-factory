"""F3 — score `web-v1`, linha de base congelada e seleção (puros).

Cada componente vive em [0, 1] ou `None` (não avaliado); o score é a média
ponderada SÓ dos avaliados, com pesos renormalizados e o resultado x100.
Tudo `None` dá score `None` — nunca 0. Nada aqui toca `viral_score`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from content_factory.domain import web_queries
from content_factory.domain.scoring import trend_decay
from content_factory.domain.text import looks_like_domain, normalize, word_tokens
from content_factory.domain.web_urls import domain_of

WEB_SCORE_VERSION = "web-v1"
BASE_SCORE_VERSION = "base-fase-h"

# Hipótese: constante com versão própria. Mudar regra ou peso sobe a versão.
WEB_WEIGHTS: dict[str, float] = {
    "tema": 0.40,
    "intencao": 0.25,
    "metadados": 0.15,
    "atualidade": 0.10,
    "posicao": 0.10,
}

_TITLE_MIN = 15
_TITLE_MAX = 120
_SNIPPET_MIN = 60

# Marcadores do léxico de intenção que NÃO provam intenção no texto da página:
# "não" aparece em quase todo texto em PT. Na FRASE do Suggest ("vela não
# acende") ele ainda classifica a consulta (`web_queries.classify_intent`).
_PAGE_GENERIC_MARKERS = frozenset({"nao"})

# Pesos/rótulos CONGELADOS de `base-fase-h`: literais de propósito. A linha de
# base não pode referenciar `domain.scoring.WEIGHTS`/`viral_score` vivos — se a
# fórmula do vídeo mudar, ela tem de continuar idêntica (o teste de
# equivalência é que sinaliza a deriva).
_BASE_RECENCY_WEIGHT = 0.20
_BASE_RELEVANCE_WEIGHT = 0.20
_BASE_RECENCY_NONE = 50.0  # sem data o `ddgs` devolve `trend_decay="none"`


@dataclass(frozen=True)
class WebPageInput:
    """Página + proveniência mínima para o score (montada a partir do banco)."""

    video_id: int
    title: str
    snippet: str
    keywords: list[str]
    canonical_url: str
    canonical_ok: bool
    query: str = ""  # consulta exata que trouxe a página (proveniência do hit)
    query_intent: str = ""  # aprender|resolver|tendencia|comercial|seed|""
    best_position: int | None = None  # melhor provider_result_position (1-based)
    total_positions: int = 0
    published_at: datetime | None = None


@dataclass(frozen=True)
class WebScore:
    video_id: int
    title: str
    domain: str
    score: float | None
    components: dict[str, float | None] = field(default_factory=dict[str, float | None])
    explanation: dict[str, Any] = field(default_factory=dict[str, Any])


def _theme_component(page: WebPageInput) -> tuple[float | None, dict[str, Any]]:
    if not page.keywords:
        return None, {"matched": []}
    words = set(word_tokens(f"{page.title} {page.snippet}"))
    matched: list[str] = []
    seen: set[str] = set()
    for keyword in page.keywords:
        norm = normalize(keyword)
        if not norm or norm in seen:  # cada keyword conta UMA vez
            continue
        seen.add(norm)
        parts = word_tokens(keyword)
        # todas as palavras da keyword, cada uma INTEIRA (não substring)
        if parts and all(part in words for part in parts):
            matched.append(keyword)
    return min(1.0, len(matched) / 2), {"matched": matched}


def _intent_component(page: WebPageInput) -> tuple[float | None, dict[str, Any]]:
    intent = page.query_intent
    if intent not in web_queries.INTENT_ORDER:
        return None, {"intent": intent or None, "marker": None}
    words = word_tokens(f"{page.title} {page.snippet}")
    for marker in web_queries.INTENT_LEXICON[intent]:
        if all(normalize(part) in _PAGE_GENERIC_MARKERS for part in marker):
            continue
        if web_queries.marker_matches(words, marker):
            return 1.0, {"intent": intent, "marker": " + ".join(marker)}
    return 0.0, {"intent": intent, "marker": None}


def _metadata_component(page: WebPageInput) -> tuple[float, dict[str, Any]]:
    title_ok = _TITLE_MIN <= len(page.title) <= _TITLE_MAX and not looks_like_domain(page.title)
    snippet_ok = len(page.snippet) >= _SNIPPET_MIN
    checks = {
        "title_ok": title_ok,
        "snippet_ok": snippet_ok,
        "canonical_ok": page.canonical_ok,
    }
    return sum(1 for ok in checks.values() if ok) / len(checks), checks


def _recency_component(page: WebPageInput, now: datetime) -> tuple[float | None, dict[str, Any]]:
    if page.published_at is None:
        return None, {"decay": "none"}
    decay = trend_decay(page.published_at, now)
    return {"low": 1.0, "medium": 0.5, "high": 0.15, "none": 0.0}[decay], {"decay": decay}


def _position_component(page: WebPageInput) -> tuple[float | None, dict[str, Any]]:
    if page.best_position is None or page.total_positions <= 0:
        return None, {"position": None, "total": page.total_positions}
    pos = max(1, min(page.best_position, page.total_positions))
    score = 1.0 - (pos - 1) / page.total_positions
    return score, {"position": page.best_position, "total": page.total_positions}


def web_score(page: WebPageInput, *, now: datetime) -> WebScore:
    """Score `web-v1` com componentes e explicação por componente.

    `now` entra por parâmetro (o domínio não lê o relógio): o mesmo dado e o
    mesmo `now` dão sempre o mesmo score.
    """
    tema, tema_x = _theme_component(page)
    intencao, intencao_x = _intent_component(page)
    metadados, metadados_x = _metadata_component(page)
    atualidade, atualidade_x = _recency_component(page, now)
    posicao, posicao_x = _position_component(page)
    components: dict[str, float | None] = {
        "tema": tema,
        "intencao": intencao,
        "metadados": metadados,
        "atualidade": atualidade,
        "posicao": posicao,
    }
    explanation: dict[str, Any] = {
        "tema": tema_x,
        "intencao": intencao_x,
        "metadados": metadados_x,
        "atualidade": atualidade_x,
        "posicao": posicao_x,
    }
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        score: float | None = None
    else:
        weight_sum = sum(WEB_WEIGHTS[k] for k in available)
        score = round(sum(available[k] * WEB_WEIGHTS[k] for k in available) / weight_sum * 100, 2)
    return WebScore(
        video_id=page.video_id,
        title=page.title,
        domain=domain_of(page.canonical_url),
        score=score,
        components=components,
        explanation=explanation,
    )


def _baseline_relevance(text: str, keywords: Sequence[str]) -> float:
    if not keywords:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for k in keywords if k.lower().removeprefix("#") in lowered)
    return hits / len(keywords)


def baseline_web_score(page: WebPageInput) -> float:
    """Cópia congelada da regra atual (`base-fase-h`) aplicada a uma página.

    Reproduz `viral_score` com métricas ausentes: só sobram `recency`
    (sem data → 50) e `relevance`, média ponderada renormalizada x100.
    Congelada de propósito: NÃO chama `domain.scoring` no runtime, então se o
    score de vídeo mudar no futuro esta linha de base NÃO muda junto — o teste
    de equivalência é que passa a falhar e sinaliza a deriva.
    """
    relevance = _baseline_relevance(f"{page.title} {page.snippet}", page.keywords)
    components = {
        "recency": _BASE_RECENCY_NONE,
        "relevance": min(max(relevance, 0.0), 1.0) * 100,
    }
    weights = {"recency": _BASE_RECENCY_WEIGHT, "relevance": _BASE_RELEVANCE_WEIGHT}
    weight_sum = sum(weights.values())
    return round(sum(components[k] * weights[k] for k in components) / weight_sum, 2)


# --- seleção (não é score) ---


def _rank_key(score: WebScore) -> tuple[float, int]:
    # maior score primeiro; None por último; empate fixo por video_id
    return (-(score.score if score.score is not None else float("-inf")), score.video_id)


def dedupe_titles(scores: Sequence[WebScore]) -> tuple[list[WebScore], dict[int, int]]:
    """Título normalizado idêntico fica só com o de maior score.

    Devolve os mantidos e o mapa `descartado → mantido` (vai na `explanation`).
    Título vazio não é duplicata de nada: fica sempre (nenhuma página some
    sem rastro em `dropped`).
    """
    kept: list[WebScore] = []
    by_title: dict[str, WebScore] = {}
    dropped: dict[int, int] = {}
    for item in sorted(scores, key=_rank_key):
        key = normalize(item.title)
        if key and key in by_title:
            dropped[item.video_id] = by_title[key].video_id
            continue
        if key:
            by_title[key] = item
        kept.append(item)
    return kept, dropped


def select_top(
    scores: Sequence[WebScore], k: int, max_per_domain: int | None = None
) -> list[WebScore]:
    """Top-k por score; opcionalmente limita páginas por domínio na lista final.

    Não altera score nenhum — é seleção.
    """
    ordered = sorted(scores, key=_rank_key)
    if max_per_domain is None:
        return ordered[:k]
    out: list[WebScore] = []
    per_domain: dict[str, int] = {}
    for item in ordered:
        if per_domain.get(item.domain, 0) >= max_per_domain:
            continue
        per_domain[item.domain] = per_domain.get(item.domain, 0) + 1
        out.append(item)
        if len(out) >= k:
            break
    return out
