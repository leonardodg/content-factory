"""F3 — score `web-v1` e linha de base congelada, só na CLI.

Lê as páginas web do segmento + proveniência, calcula o score (puro),
persiste em `web_page_scores` (append-only) e devolve os dois rankings. O
`worker` só passa a gravar sob `WEB_SCORE_ENABLED` (F5). Não escreve em
`video_scores` — a web tem score próprio.
"""

from dataclasses import dataclass
from datetime import datetime

from content_factory.app.ports import WebScoreRepository
from content_factory.domain.models import Segment, WebPageScoreRow
from content_factory.domain.web_relevance import (
    WEB_SCORE_VERSION,
    WebPageInput,
    WebScore,
    baseline_web_score,
    dedupe_titles,
    select_top,
    web_score,
)


@dataclass
class WebRankOutcome:
    ok: bool
    segment: str
    scored: int
    score_version: str
    top_v1: list[WebScore]
    top_base: list[WebScore]


def rank_baseline(pages: list[WebPageInput]) -> list[WebScore]:
    """`base-fase-h` congelada como `WebScore` (para ordenar o mesmo conjunto)."""
    return [
        WebScore(
            video_id=p.video_id,
            title=p.title,
            domain=_domain(p),
            score=baseline_web_score(p),
        )
        for p in pages
    ]


def _domain(page: WebPageInput) -> str:
    from content_factory.domain.web_urls import domain_of

    return domain_of(page.canonical_url)


async def web_rank(
    segment: Segment,
    repo: WebScoreRepository,
    *,
    now: datetime,
    k: int = 10,
    max_per_domain: int | None = None,
) -> WebRankOutcome:
    pages = await repo.pages_for_segment(segment.id, list(segment.keywords))
    scored = [web_score(p, now=now) for p in pages]
    kept, _dropped = dedupe_titles(scored)
    rows = [
        WebPageScoreRow(
            video_id=s.video_id,
            segment_id=segment.id,
            computed_at=now,
            score=s.score,
            score_version=WEB_SCORE_VERSION,
            components={
                name: (None if value is None else round(value, 6))
                for name, value in s.components.items()
            },
            explanation=s.explanation,
        )
        for s in kept
    ]
    await repo.append_scores(rows)
    base_kept, _ = dedupe_titles(rank_baseline(pages))
    return WebRankOutcome(
        ok=True,
        segment=segment.slug,
        scored=len(rows),
        score_version=WEB_SCORE_VERSION,
        top_v1=select_top(kept, k, max_per_domain),
        top_base=select_top(base_kept, k, max_per_domain),
    )
