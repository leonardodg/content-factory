"""F3 `app.web_rank`: score `web-v1` + linha de base congelada, só na CLI."""

from datetime import UTC, datetime

import pytest
from tests.stubs import FakeWebScores

from content_factory.app.web_rank import web_rank
from content_factory.domain.models import Segment
from content_factory.domain.web_relevance import WebPageInput

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SEG = Segment(id=1, slug="velas", name="Velas", platforms=["web"], keywords=["vela"])


def _page(
    vid: int,
    title: str,
    *,
    url: str | None = None,
    snippet: str = "",
    intent: str = "",
    position: int | None = None,
    total: int = 0,
) -> WebPageInput:
    return WebPageInput(
        video_id=vid,
        title=title,
        snippet=snippet,
        keywords=list(SEG.keywords),
        canonical_url=url or f"https://site{vid}.example/p/{vid}",
        canonical_ok=True,
        query_intent=intent,
        best_position=position,
        total_positions=total,
    )


@pytest.mark.asyncio
async def test_web_rank_persists_deduped_rows_and_returns_top() -> None:
    pages = [
        _page(
            1,
            "Como fazer vela artesanal passo a passo",
            intent="aprender",
            position=1,
            total=10,
        ),
        _page(2, "Como fazer vela artesanal passo a passo", url="https://outro.example/x"),
        _page(3, "Comprar vela pronta no atacado"),
    ]
    repo = FakeWebScores(pages)

    out = await web_rank(SEG, repo, now=NOW, k=10)

    assert out.ok and out.segment == "velas"
    assert out.score_version == "web-v1"
    assert out.scored == 2  # dedupe por título normalizado
    assert len(repo.appended) == 2
    assert {row.segment_id for row in repo.appended} == {1}
    assert {row.computed_at for row in repo.appended} == {NOW}
    # v1: a página com intenção + posição fica no topo
    assert out.top_v1[0].video_id == 1
    assert len(out.top_base) == 2


@pytest.mark.asyncio
async def test_web_rank_k_and_max_per_domain_limit_the_final_list() -> None:
    pages = [
        _page(i, f"Vela artesanal dica {i}", url=f"https://mesmo.example/{i}") for i in range(1, 6)
    ]
    repo = FakeWebScores(pages)

    full = await web_rank(SEG, repo, now=NOW, k=3)
    assert len(full.top_v1) == 3  # k limita a lista final

    capped = await web_rank(SEG, repo, now=NOW, k=5, max_per_domain=1)
    assert len(capped.top_v1) == 1  # todos do mesmo domínio


@pytest.mark.asyncio
async def test_web_rank_does_not_touch_video_scores() -> None:
    """F3: o score web tem tabela própria — o repo só recebe `web_page_scores`."""
    repo = FakeWebScores([_page(1, "Vela artesanal em resina passo a passo")])

    await web_rank(SEG, repo, now=NOW)

    assert all(row.score_version == "web-v1" for row in repo.appended)
    # `append_scores` é do repositório web; não há escrita em video_scores aqui.
    assert len(repo.appended) == 1


@pytest.mark.asyncio
async def test_web_rank_empty_repo_scores_nothing() -> None:
    repo = FakeWebScores([])
    out = await web_rank(SEG, repo, now=NOW)
    assert out.scored == 0
    assert out.top_v1 == [] and out.top_base == []
    assert repo.appended == []
