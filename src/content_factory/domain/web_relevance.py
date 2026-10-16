"""F3 — score `web-v1`, linha de base congelada e seleção (puros).

Cada componente vive em [0, 1] ou `None` (não avaliado); o score é a média
ponderada SÓ dos avaliados, com pesos renormalizados e o resultado x100.
Tudo `None` dá score `None` — nunca 0. Nada aqui toca `viral_score`.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from content_factory.domain import web_queries
from content_factory.domain.models import VideoMetrics
from content_factory.domain.scoring import trend_decay, viral_score
from content_factory.domain.text import looks_like_domain, normalize
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

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_TITLE_MIN = 15
_TITLE_MAX = 120
_SNIPPET_MIN = 60


@dataclass(frozen=True)
class WebPageInput:
    """Página + proveniência mínima para o score (montada a partir do banco)."""

    video_id: int
    title: str
    snippet: str
    keywords: list[str]
    canonical_url: str
    canonical_ok: bool
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
    text = normalize(f"{page.title} {page.snippet}")
    matched: list[str] = []
    seen: set[str] = set()
    for keyword in page.keywords:
        norm = normalize(keyword)
        if not norm or norm in seen:  # cada keyword conta UMA vez
            continue
        seen.add(norm)
        parts = norm.split()
        if all(part in text for part in parts):
            matched.append(keyword)
    return min(1.0, len(matched) / 2), {"matched": matched}


def _intent_component(page: WebPageInput) -> tuple[float | None, dict[str, Any]]:
    intent = page.query_intent
    if intent not in web_queries.INTENT_ORDER:
        return None, {"intent": intent or None, "marker": None}
    text = normalize(f"{page.title} {page.snippet}")
    for marker in web_queries.INTENT_LEXICON[intent]:
        if normalize(marker) in text:
            return 1.0, {"intent": intent, "marker": marker}
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


def _recency_component(page: WebPageInput) -> tuple[float | None, dict[str, Any]]:
    if page.published_at is None:
        return None, {"decay": "none"}
    now = datetime.now(UTC)
    decay = trend_decay(page.published_at, now)
    return {"low": 1.0, "medium": 0.5, "high": 0.15, "none": 0.0}[decay], {"decay": decay}


def _position_component(page: WebPageInput) -> tuple[float | None, dict[str, Any]]:
    if page.best_position is None or page.total_positions <= 0:
        return None, {"position": None, "total": page.total_positions}
    pos = max(1, min(page.best_position, page.total_positions))
    score = 1.0 - (pos - 1) / page.total_positions
    return score, {"position": page.best_position, "total": page.total_positions}


def web_score(page: WebPageInput) -> WebScore:
    """Score `web-v1` com componentes e explicação por componente."""
    tema, tema_x = _theme_component(page)
    intencao, intencao_x = _intent_component(page)
    metadados, metadados_x = _metadata_component(page)
    atualidade, atualidade_x = _recency_component(page)
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

    `viral_score` com métricas ausentes + `relevance_of` + `recency` = 50
    (sem data o `ddgs` devolve `trend_decay="none"`). Congelada: se o score
    de vídeo mudar no futuro, esta linha de base NÃO muda junto.
    """
    relevance = _baseline_relevance(f"{page.title} {page.snippet}", page.keywords)
    now = page.published_at or _EPOCH
    return viral_score(VideoMetrics(), relevance, now, page.published_at).total


# --- seleção (não é score) ---


def _rank_key(score: WebScore) -> tuple[float, int]:
    # maior score primeiro; None por último; empate fixo por video_id
    return (-(score.score if score.score is not None else float("-inf")), score.video_id)


def dedupe_titles(scores: Sequence[WebScore]) -> tuple[list[WebScore], dict[int, int]]:
    """Título normalizado idêntico fica só com o de maior score.

    Devolve os mantidos e o mapa `descartado → mantido` (vai na `explanation`).
    """
    kept: dict[str, WebScore] = {}
    dropped: dict[int, int] = {}
    for item in sorted(scores, key=_rank_key):
        key = normalize(item.title)
        if key and key in kept:
            dropped[item.video_id] = kept[key].video_id
        else:
            kept[key] = item
    return list(kept.values()), dropped


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
