"""Bloco 5 Task 8: relatório no corte — temas, veredito por origem e top vídeos."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from tests.stubs import FakeSegmentTerms

from content_factory.app.term_report import TermReportDeps, build_term_report
from content_factory.domain.models import (
    PopulationRow,
    SearchConfig,
    Segment,
    SegmentConfig,
    TermReportRecord,
)

AS_OF = datetime(2026, 10, 21, 12, tzinfo=UTC)
SEG = Segment(
    id=1,
    slug="velas-artesanais",
    name="Velas",
    platforms=["youtube"],
    keywords=["vela artesanal"],
    anchor_terms=["vela"],
    search=SearchConfig(relevance_language="pt", accepted_language="pt-BR"),
)


class _Segments:
    async def get_by_slug(self, slug: str) -> Segment:
        _ = slug
        return SEG

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        raise NotImplementedError


class _Population:
    def __init__(self, rows: list[PopulationRow], pairs: list[tuple[int, str, str]]) -> None:
        self._rows, self._pairs = rows, pairs

    async def population(
        self, segment_id: int, as_of: datetime, window_days: int
    ) -> list[PopulationRow]:
        _ = segment_id, as_of, window_days
        return list(self._rows)

    async def attributions(
        self, segment_id: int, platform: str, as_of: datetime
    ) -> list[tuple[int, str, str]]:
        _ = segment_id, platform, as_of
        return list(self._pairs)


class _Reports:
    def __init__(self) -> None:
        self.saved: list[TermReportRecord] = []

    async def append_many(self, rows: Sequence[TermReportRecord]) -> int:
        self.saved.extend(rows)
        return len(rows)


class _Terms(FakeSegmentTerms):
    def __init__(self) -> None:
        super().__init__()
        self.engagement: dict[str, float] = {}

    async def set_engagement_scores(self, segment_id: int, scores: Mapping[str, float]) -> int:
        _ = segment_id
        self.engagement.update(scores)
        return len(scores)


def _row(vid: int, views: int) -> PopulationRow:
    return PopulationRow(
        video_id=vid,
        platform="youtube",
        platform_video_id=f"yt{vid}",
        title=f"Vela {vid}",
        language="pt-BR",
        views=views,
        likes=views // 10,
        comments=views // 100,
        captured_at=AS_OF,
        prev_views=views // 2,
        prev_captured_at=AS_OF - timedelta(hours=6),
        followers=1000,
    )


def _deps(rows: list[PopulationRow], pairs: list[tuple[int, str, str]]) -> TermReportDeps:
    return TermReportDeps(
        population=_Population(rows, pairs),
        reports=_Reports(),
        segments=_Segments(),
        terms=_Terms(),
        new_report_id=lambda: "rep-1",
    )


def _saved(deps: TermReportDeps) -> list[TermReportRecord]:
    return cast(_Reports, deps.reports).saved


def _cenario() -> tuple[list[PopulationRow], list[tuple[int, str, str]]]:
    """40 vídeos fortes trazidos por `suggest`, 40 fracos por `seed`."""
    rows = [_row(i, 10_000 + 1_000 * i) for i in range(1, 41)]
    rows += [_row(100 + i, 10 + i) for i in range(1, 41)]
    pairs = [(i, "como fazer vela", "suggest") for i in range(1, 41)]
    pairs += [(100 + i, "vela artesanal", "seed") for i in range(1, 41)]
    return rows, pairs


@pytest.mark.asyncio
async def test_veredito_por_origem_e_o_do_pre_registro() -> None:
    rows, pairs = _cenario()
    deps = _deps(rows, pairs)
    out = await build_term_report("velas-artesanais", "youtube", AS_OF, deps)
    saved = _saved(deps)
    origin = next(r for r in saved if r.scope == "origin" and r.report_key == "suggest")
    assert origin.verdict == "better" and origin.n == 40 and origin.baseline_n == 40
    assert origin.diff is not None and origin.ci_low is not None and origin.ci_low > 0
    assert out.as_counts()["origins_decided"] == 1


@pytest.mark.asyncio
async def test_mesmo_video_repetido_conta_uma_vez_no_termo() -> None:
    rows = [_row(i, 1000 * i) for i in range(1, 8)]
    pairs = [(1, "vela x", "seed")] * 16 + [(i, "vela y", "seed") for i in range(2, 8)]
    deps = _deps(rows, pairs)
    await build_term_report("velas-artesanais", "youtube", AS_OF, deps)
    saved = _saved(deps)
    term_x = next(r for r in saved if r.scope == "term" and r.report_key == "vela x")
    assert term_x.n == 1 and term_x.verdict == "insufficient"


@pytest.mark.asyncio
async def test_top_videos_ordenados_e_engagement_no_pool() -> None:
    rows, pairs = _cenario()
    deps = _deps(rows, pairs)
    await build_term_report("velas-artesanais", "youtube", AS_OF, deps, top_videos=20)
    saved = _saved(deps)
    videos = [r for r in saved if r.scope == "video"]
    assert [r.rank for r in videos] == list(range(1, 21))
    scores = [r.median_viral_v2 or 0.0 for r in videos]
    assert scores == sorted(scores, reverse=True)
    assert videos[0].report_key == "yt40"  # o de mais views/velocidade
    terms = deps.terms
    assert isinstance(terms, _Terms) and set(terms.engagement) == {
        "como fazer vela",
        "vela artesanal",
    }


@pytest.mark.asyncio
async def test_reprodutivel_no_mesmo_corte() -> None:
    rows, pairs = _cenario()
    a, b = _deps(rows, pairs), _deps(rows, pairs)
    await build_term_report("velas-artesanais", "youtube", AS_OF, a)
    await build_term_report("velas-artesanais", "youtube", AS_OF, b)
    dump = [r.model_dump() for r in _saved(a)]
    assert dump == [r.model_dump() for r in _saved(b)]
    assert all(r.as_of == AS_OF and r.version == "term-rank-v1" for r in _saved(a))
