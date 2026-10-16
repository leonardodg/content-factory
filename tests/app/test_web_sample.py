"""F2 `app.web_sample`: amostra cega (topo + aleatório + descartados)."""

import csv
from pathlib import Path

from tests.stubs import FakeWebScores

from content_factory.app.web_sample import (
    WEB_SAMPLE_HEADER,
    WEB_SAMPLE_SEED,
    queries_without_hits,
    web_sample,
    write_web_sample_csv,
)
from content_factory.domain.models import Segment, WebSearchHit
from content_factory.domain.web_queries import PlannedQuery
from content_factory.domain.web_relevance import WebPageInput

SEG = Segment(id=1, slug="velas", name="Velas", platforms=["web"], keywords=["vela"])


def _page(vid: int, query: str = "") -> WebPageInput:
    return WebPageInput(
        video_id=vid,
        title=f"Vela artesanal numero {vid}",
        snippet="snippet",
        keywords=list(SEG.keywords),
        canonical_url=f"https://site{vid}.example/p/{vid}",
        canonical_ok=True,
        query=query,
    )


def _hit(canonical: str, status: str) -> WebSearchHit:
    return WebSearchHit(
        segment_id=1,
        query="como fazer velas",
        query_id="q1:aprender:00",
        query_intent="aprender",
        provider_result_position=1,
        url_original=canonical,
        canonical_url=canonical,
        status=status,
        discard_reason=None if status == "kept" else "ad:bing_aclick",
        collector_version="web-c2",
        rules_version="ads-v1",
        title="resultado",
        snippet="",
    )


async def test_web_sample_strata_sizes_and_ceiling() -> None:
    pages = [_page(i) for i in range(1, 7)]  # 4 kept após top_n=2 + random_n=2
    hits = [
        _hit("https://x.example/a", "discarded_ad"),
        _hit("https://x.example/b", "discarded_ad"),
        _hit("https://x.example/c", "discarded_ad"),
        _hit("https://x.example/c", "discarded_ad"),  # mesma URL canônica: 1 item
    ]
    repo = FakeWebScores(pages, hits)

    items = await web_sample(SEG, repo, seed=WEB_SAMPLE_SEED, top_n=2, random_n=2, discarded_n=5)

    assert len(items) == 2 + 2 + 3  # topo + aleatório + 3 canônicas distintas
    assert len({i.item_id for i in items}) == len(items)  # sem duplicata na amostra


async def test_web_sample_kept_item_carries_exact_query() -> None:
    repo = FakeWebScores([_page(1, query="como fazer velas")])
    items = await web_sample(SEG, repo, seed=1, top_n=1, random_n=0, discarded_n=0)
    assert items[0].query == "como fazer velas"


async def test_web_sample_discarded_stratum_is_ad_only() -> None:
    """`duplicate` não é falso positivo do filtro e duplicaria o `item_id`."""
    page = _page(1)
    repo = FakeWebScores([page], [_hit(page.canonical_url, "duplicate")])
    items = await web_sample(SEG, repo, seed=1, top_n=1, random_n=0, discarded_n=5)
    assert [i.item_id for i in items] == [page.canonical_url]


async def test_web_sample_is_deterministic_for_a_seed() -> None:
    pages = [_page(i) for i in range(1, 12)]
    repo = FakeWebScores(pages)

    first = await web_sample(SEG, repo, seed=123, top_n=3, random_n=4, discarded_n=0)
    second = await web_sample(SEG, repo, seed=123, top_n=3, random_n=4, discarded_n=0)

    assert [i.item_id for i in first] == [i.item_id for i in second]


async def test_web_sample_csv_is_blind_and_roundtrips(tmp_path: Path) -> None:
    pages = [_page(i) for i in range(1, 5)]
    repo = FakeWebScores(pages)
    items = await web_sample(SEG, repo, seed=1, top_n=1, random_n=1, discarded_n=0)

    out = tmp_path / "web.csv"
    write_web_sample_csv(items, out)

    with out.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == WEB_SAMPLE_HEADER
    # a anotação é cega: nenhum score/posição/estrato no CSV
    assert not any("score" in col for col in rows[0])
    assert len(rows) == 1 + len(items)


def test_queries_without_hits_lists_protocol_queries_never_collected() -> None:
    """Ciclo incompleto: a amostra não pode sair sem todas as consultas coletadas."""
    protocol = [
        PlannedQuery("q1:aprender:00", "como fazer velas", "aprender"),
        PlannedQuery("q1:seed:00", "vela OR velas", "seed"),
    ]
    hits = [_hit("https://x.example/a", "kept")]  # só q1:aprender:00
    assert queries_without_hits(protocol, hits) == ["q1:seed:00"]
    assert queries_without_hits(protocol[:1], hits) == []
