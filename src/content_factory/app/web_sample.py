"""F2 — amostra de avaliação (cega): 15 topo + 25 aleatórios + até 10 descartados.

O CSV não traz score, posição da linha de base nem estrato: a anotação é
cega e a ordem das linhas é embaralhada (seed fixa gravada).
"""

import csv
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from content_factory.app.ports import WebScoreRepository
from content_factory.app.web_rank import rank_baseline
from content_factory.domain.models import Segment, WebSearchHit
from content_factory.domain.web_hits import AD_DISCARD_STATUS
from content_factory.domain.web_queries import PlannedQuery
from content_factory.domain.web_relevance import WebPageInput
from content_factory.domain.web_urls import domain_of

WEB_SAMPLE_SEED = 20261019
TOP_N = 15
RANDOM_N = 25
DISCARDED_N = 10

WEB_SAMPLE_HEADER = [
    "item_id",
    "canonical_url",
    "domain",
    "title",
    "snippet",
    "query",
    "query_intent",
]


@dataclass(frozen=True)
class WebSampleRun:
    """Resultado do `cf web-sample`: linhas gravadas + data de corte.

    `as_of` é o `--as-of` do `cf web-eval` (avaliação reprodutível).
    `missing_queries` não vazio = ciclo incompleto (CSV não é gravado).
    """

    rows: int
    as_of: datetime
    missing_queries: list[str]


@dataclass(frozen=True)
class WebSampleItem:
    item_id: str
    canonical_url: str
    domain: str
    title: str
    snippet: str
    query: str
    query_intent: str


def _item_from_page(page: WebPageInput) -> WebSampleItem:
    return WebSampleItem(
        item_id=page.canonical_url,
        canonical_url=page.canonical_url,
        domain=domain_of(page.canonical_url),
        title=page.title,
        snippet=page.snippet,
        query=page.query,
        query_intent=page.query_intent,
    )


async def web_sample(
    segment: Segment,
    repo: WebScoreRepository,
    *,
    seed: int = WEB_SAMPLE_SEED,
    top_n: int = TOP_N,
    random_n: int = RANDOM_N,
    discarded_n: int = DISCARDED_N,
) -> list[WebSampleItem]:
    rng = random.Random(seed)  # nosec B311 - reprodução por seed fixa
    pages = await repo.pages_for_segment(segment.id, list(segment.keywords))

    # topo da linha de base (score atual congelado)
    baseline = {s.video_id: s.score for s in rank_baseline(pages) if s.score is not None}
    ordered = sorted(pages, key=lambda p: (-(baseline.get(p.video_id) or 0.0), p.video_id))
    top = ordered[:top_n]
    top_ids = {p.video_id for p in top}
    rest = [p for p in ordered if p.video_id not in top_ids]
    random_pick = rng.sample(rest, min(random_n, len(rest)))

    items = [_item_from_page(p) for p in top + random_pick]

    # descartados pelo FILTRO DE ANÚNCIO (falso positivo / cobertura). Só
    # `discarded_ad`: `duplicate` não é falso positivo do filtro (a página foi
    # preservada uma vez) e sua URL canônica é a de um `kept`, o que duplicaria
    # o `item_id` da amostra.
    hits = await repo.hits_for_segment(segment.id)
    discarded: dict[str, WebSampleItem] = {}
    for hit in hits:
        if hit.status != AD_DISCARD_STATUS:
            continue
        if hit.canonical_url in discarded:
            continue
        discarded[hit.canonical_url] = WebSampleItem(
            item_id=hit.canonical_url,
            canonical_url=hit.canonical_url,
            domain=domain_of(hit.canonical_url),
            title=hit.title,
            snippet=hit.snippet,
            query=hit.query,
            query_intent=hit.query_intent,
        )
    pool = sorted(discarded.values(), key=lambda i: i.canonical_url)
    items.extend(rng.sample(pool, min(discarded_n, len(pool))))

    rng.shuffle(items)
    return items


def queries_without_hits(
    protocol: Sequence[PlannedQuery], hits: Sequence[WebSearchHit]
) -> list[str]:
    """`query_id`s do protocolo que ainda não têm nenhum hit (ciclo incompleto).

    A amostra só sai com o ciclo completo: com 3 consultas por coleta, 13
    consultas pedem 5 coletas. Faltando uma, o estrato dela (pode ser a
    consulta de controle) não existe na avaliação.
    """
    collected = {hit.query_id for hit in hits}
    return [q.query_id for q in protocol if q.query_id not in collected]


def write_web_sample_csv(items: list[WebSampleItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(WEB_SAMPLE_HEADER)
        for item in items:
            writer.writerow(
                [
                    item.item_id,
                    item.canonical_url,
                    item.domain,
                    item.title,
                    item.snippet,
                    item.query,
                    item.query_intent,
                ]
            )
