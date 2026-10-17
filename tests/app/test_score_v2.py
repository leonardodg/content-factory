"""Bloco 5 Task 7: use-case `score_v2` — grava o viral-v2 e minera hashtags."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from tests.stubs import FakeSegmentTerms

from content_factory.app.score_v2 import ScoreV2Deps, score_v2
from content_factory.domain.models import PopulationRow, SearchConfig, Segment
from content_factory.domain.viral_v2 import ViralScore

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
SEG = Segment(
    id=1,
    slug="velas-artesanais",
    name="Velas",
    platforms=["youtube"],
    keywords=["vela artesanal"],
    anchor_terms=["vela", "velas"],
    search=SearchConfig(relevance_language="pt", accepted_language="pt-BR"),
)


class _Population:
    def __init__(self, rows: list[PopulationRow]) -> None:
        self._rows = rows
        self.calls: list[tuple[int, datetime, int]] = []

    async def population(
        self, segment_id: int, as_of: datetime, window_days: int
    ) -> list[PopulationRow]:
        self.calls.append((segment_id, as_of, window_days))
        return list(self._rows)

    async def attributions(
        self, segment_id: int, platform: str, as_of: datetime
    ) -> list[tuple[int, str, str]]:
        _ = segment_id, platform, as_of
        return []


class _ScoresV2:
    def __init__(self) -> None:
        self.saved: list[ViralScore] = []

    async def append_many(
        self, segment_id: int, as_of: datetime, scores: Sequence[ViralScore], window_days: int
    ) -> int:
        _ = segment_id, as_of, window_days
        self.saved.extend(scores)
        return len(scores)


def _row(
    vid: int, views: int, *, language: str = "pt-BR", tags: list[str] | None = None
) -> PopulationRow:
    return PopulationRow(
        video_id=vid,
        platform="youtube",
        platform_video_id=f"v{vid}",
        title="Como fazer vela aromática",
        language=language,
        tags=tags or [],
        views=views,
        likes=views // 10,
        comments=views // 100,
        captured_at=NOW,
        prev_views=views // 2,
        prev_captured_at=NOW - timedelta(hours=6),
        followers=1000,
        raw={"snippet": {"tags": tags or []}},
    )


def _deps(rows: list[PopulationRow]) -> tuple[ScoreV2Deps, _ScoresV2, FakeSegmentTerms]:
    scores, terms = _ScoresV2(), FakeSegmentTerms()
    deps = ScoreV2Deps(
        population=_Population(rows),
        scores_v2=scores,
        terms=terms,
        segment=SEG,
        clock=lambda: NOW,
    )
    return deps, scores, terms


@pytest.mark.asyncio
async def test_score_v2_filtra_relevancia_e_grava() -> None:
    rows = [_row(i, 100 * i) for i in range(1, 7)] + [_row(99, 5000, language="en")]
    deps, scores, _ = _deps(rows)
    out = await score_v2(deps)
    assert (out.scored, out.skipped_relevance, out.insufficient) == (6, 1, 0)
    assert {s.video_id for s in scores.saved} == {1, 2, 3, 4, 5, 6}  # inglês fora
    assert all(s.total is not None for s in scores.saved)


@pytest.mark.asyncio
async def test_hashtags_do_topo_vao_ao_pool_e_as_antigas_saem() -> None:
    rows = [_row(i, 100 * i, tags=["velaaromatica", "decoracao"]) for i in range(1, 9)]
    deps, _, terms = _deps(rows)
    out = await score_v2(deps)
    pooled = [t for t in terms.saved if t.origin == "hashtag:youtube"]
    assert [t.term for t in pooled] == ["velaaromatica"]  # "decoracao" sem âncora
    assert pooled[0].engagement_score is not None and pooled[0].rules_version == "hashtag-mine-v1"
    assert terms.status_calls == [(1, "hashtag:youtube", ["velaaromatica"], "stale")]
    assert out.hashtags_active == 1


@pytest.mark.asyncio
async def test_populacao_abaixo_do_minimo_nao_minera() -> None:
    rows = [_row(i, 100 * i, tags=["velaaromatica"]) for i in range(1, 4)]
    deps, scores, terms = _deps(rows)
    out = await score_v2(deps)
    assert out.insufficient == 3 and all(s.total is None for s in scores.saved)
    assert terms.saved == [] and terms.status_calls == []
