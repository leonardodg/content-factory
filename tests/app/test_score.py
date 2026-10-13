from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from content_factory.app.ports import ScoreUpsertRow
from content_factory.app.score import ScoreDeps, relevance_of, score
from content_factory.domain.models import (
    ScoreResult,
    Segment,
    SegmentConfig,
    UpsertStats,
    VideoMetrics,
    VideoSnapshot,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class FakeVideos:
    def __init__(self, snaps: list[VideoSnapshot]) -> None:
        self._s = snaps

    async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
        return self._s

    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: object
    ) -> UpsertStats:
        # pyright strict: o fake precisa satisfazer o Protocolo inteiro
        # (retorno UpsertStats, não object — covariance do Protocol)
        raise NotImplementedError


class FakeScores:
    """Fake com contadores de round-trips (Task 13 — evidência do ganho).

    Cada `upsert` = 1 flush (caminho antigo: N vídeos = N flushes); cada
    `upsert_many` = 1 flush por chamada (caminho novo: N vídeos = 1 flush).
    `fail_on` modela a linha ruim: como o fallback por linha do impl real,
    a linha doente é isolada e as demais persistem.
    """

    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.saved: list[tuple[int, ScoreResult]] = []
        self.upsert_calls = 0
        self.upsert_many_calls = 0
        self.flushes = 0
        self._fail_on = fail_on or set()

    async def upsert(
        self, video_id: int, segment_id: int, result: ScoreResult, computed_at: datetime
    ) -> None:
        self.upsert_calls += 1
        self.flushes += 1
        if video_id in self._fail_on:
            raise RuntimeError("repo indisponível")
        self.saved.append((video_id, result))

    async def upsert_many(self, rows: Sequence[ScoreUpsertRow]) -> int:
        self.upsert_many_calls += 1
        self.flushes += 1
        saved = 0
        for row in rows:
            if row.video_id in self._fail_on:
                continue
            self.saved.append((row.video_id, row.result))
            saved += 1
        return saved


class FailingScores(FakeScores):
    """Fronteira falha num subconjunto de vídeos (repo em falha ≠ lote derrubado)."""

    def __init__(self, fail_on: set[int]) -> None:
        super().__init__(fail_on=fail_on)


SEG = Segment(
    id=1,
    **SegmentConfig(
        slug="candles", name="Velas", platforms=["youtube"], keywords=["vela", "soja"]
    ).model_dump(),
)


def snap(video_id: int, views: int, title: str = "vela de soja", age_h: int = 6) -> VideoSnapshot:
    return VideoSnapshot(
        video_id=video_id,
        title=title,
        published_at=NOW - timedelta(hours=age_h),
        metrics=VideoMetrics(views=views, likes=views // 20),
    )


async def test_score_writes_one_result_per_video() -> None:
    repo = FakeVideos([snap(1, 100_000), snap(2, 50_000)])
    scores = FakeScores()
    deps = ScoreDeps(
        videos=repo, scores=scores, segment=SEG, keywords=["vela", "soja"], clock=lambda: NOW
    )
    out = await score(deps)
    assert out.scored == 2 and out.ok is True
    assert len(scores.saved) == 2
    assert all(0 <= r.total <= 100 for _, r in scores.saved)


async def test_zero_metrics_totals_zero() -> None:
    """Métricas reportadas como ZERO (não ausentes) + relevância zero → total 0.0."""
    zero = VideoSnapshot(
        video_id=9,
        title="x",
        published_at=None,
        metrics=VideoMetrics(views=0, likes=0, comments=0, shares=0, saves=0),
    )
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([zero]), scores=scores, segment=SEG, keywords=["vela"], clock=lambda: NOW
    )
    await score(deps)
    assert scores.saved[0][1].total == 0.0


async def test_none_metrics_not_penalized_as_zero() -> None:
    """Fase G: métricas AUSENTES (None) não são penalizadas como zero."""
    from content_factory.domain.models import VideoMetrics as VM

    none_metrics = VideoSnapshot(
        video_id=10,
        title="x",
        published_at=None,
        metrics=VM(views=None, likes=None, comments=None, shares=None, saves=None),
    )
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([none_metrics]),
        scores=scores,
        segment=SEG,
        keywords=["vela"],
        clock=lambda: NOW,
    )
    await score(deps)
    r = scores.saved[0][1]
    # relevância zero, mas velocity/recency ainda pontuam (não colapsa em 0)
    assert r.total > 0.0
    assert r.performance is None  # ausente, não zero
    assert r.engagement is None


async def test_relevance_counts_keyword_hits() -> None:
    # título "vela de soja" casa com 2/2 keywords → relevance 100
    hits = VideoSnapshot(
        video_id=1, title="vela de soja", published_at=NOW, metrics=VideoMetrics(views=10_000)
    )
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([hits]),
        scores=scores,
        segment=SEG,
        keywords=["vela", "soja"],
        clock=lambda: NOW,
    )
    await score(deps)
    assert scores.saved[0][1].relevance == 100.0


async def test_relevance_is_case_insensitive_and_uses_description() -> None:
    """Substring case-insensitive em título+descrição (R4 do trd-scoring)."""
    mixed = VideoSnapshot(
        video_id=1,
        title="VELA artesanal",
        description="feito com Soja",
        published_at=NOW,
        metrics=VideoMetrics(views=10_000),
    )
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([mixed]),
        scores=scores,
        segment=SEG,
        keywords=["vela", "soja"],
        clock=lambda: NOW,
    )
    await score(deps)
    assert scores.saved[0][1].relevance == 100.0


async def test_partial_when_some_upserts_fail() -> None:
    """R6: repo em falha não derruba o lote → ok=False, partial=True."""
    scores = FailingScores(fail_on={1})
    deps = ScoreDeps(
        videos=FakeVideos([snap(1, 100_000), snap(2, 50_000)]),
        scores=scores,
        segment=SEG,
        keywords=["vela"],
        clock=lambda: NOW,
    )
    out = await score(deps)
    assert out.scored == 1
    assert out.ok is False and out.partial is True
    assert [vid for vid, _ in scores.saved] == [2]


async def test_all_upserts_failing_is_not_partial() -> None:
    """R6: nada gravado → ok=False, partial=False (exit 1 na CLI)."""
    deps = ScoreDeps(
        videos=FakeVideos([snap(1, 100_000)]),
        scores=FailingScores(fail_on={1}),
        segment=SEG,
        keywords=["vela"],
        clock=lambda: NOW,
    )
    out = await score(deps)
    assert out.scored == 0
    assert out.ok is False and out.partial is False


async def test_score_persists_batch_in_single_round_trip() -> None:
    """Task 13: N vídeos = 1 chamada upsert_many = 1 flush (antes: N/N)."""
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([snap(i, 100_000) for i in range(1, 6)]),
        scores=scores,
        segment=SEG,
        keywords=["vela"],
        clock=lambda: NOW,
    )
    out = await score(deps)
    assert out.scored == 5 and out.ok is True and out.partial is False
    assert len(scores.saved) == 5
    assert scores.upsert_many_calls == 1  # o lote inteiro numa chamada
    assert scores.upsert_calls == 0  # caminho por-vídeo não é mais usado
    assert scores.flushes == 1  # era 5 no caminho antigo (1 flush por vídeo)


async def test_score_partial_when_one_batch_row_is_bad() -> None:
    """Task 13: 1 linha ruim no meio ⇒ demais persistem, partial=True."""
    scores = FakeScores(fail_on={3})
    deps = ScoreDeps(
        videos=FakeVideos([snap(i, 100_000) for i in range(1, 6)]),
        scores=scores,
        segment=SEG,
        keywords=["vela"],
        clock=lambda: NOW,
    )
    out = await score(deps)
    assert out.scored == 4
    assert out.ok is False and out.partial is True
    assert [vid for vid, _ in scores.saved] == [1, 2, 4, 5]


async def test_score_batch_connection_failure_scores_nothing() -> None:
    """Task 13: falha do lote inteiro (ex.: conexão) ⇒ scored=0, partial=False."""

    class ExplodingScores(FakeScores):
        async def upsert_many(self, rows: Sequence[ScoreUpsertRow]) -> int:
            raise RuntimeError("conexão caiu")

    deps = ScoreDeps(
        videos=FakeVideos([snap(1, 100_000), snap(2, 50_000)]),
        scores=ExplodingScores(),
        segment=SEG,
        keywords=["vela"],
        clock=lambda: NOW,
    )
    out = await score(deps)
    assert out.scored == 0
    assert out.ok is False and out.partial is False


def test_relevance_of_keyword_with_hash_matches_text_without_hash() -> None:
    """Keyword cadastrada como '#vela' deve casar texto 'Vela artesanal' (sem #)."""
    snap_ = VideoSnapshot(
        video_id=1,
        title="",
        description="Vela artesanal de soja",
        published_at=None,
        metrics=VideoMetrics(),
    )
    assert relevance_of(snap_, ["#vela"]) == 1.0


async def test_empty_segment_scores_nothing_and_is_ok() -> None:
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([]), scores=scores, segment=SEG, keywords=["vela"], clock=lambda: NOW
    )
    out = await score(deps)
    assert out.ok is True and out.partial is False and out.scored == 0
    assert scores.saved == []


def test_score_deps_is_replaceable_dataclass() -> None:
    """Task 10: ScoreDeps é @dataclass — o worker usa `replace` por segmento.

    Paridade com ResearchDeps em run_research_job: os campos do startup são
    placeholders e cada UoW troca videos/scores/segment/keywords. Todos os
    campos e defaults são preservados (construção posicional e nomeada).
    """
    import dataclasses

    scores = FakeScores()
    deps = ScoreDeps(FakeVideos([]), scores, SEG, ["vela"])
    assert dataclasses.is_dataclass(deps)
    assert deps.keywords == ["vela"]
    assert deps.clock() is not None  # default = agora em UTC
    other = Segment(
        id=2,
        **SegmentConfig(
            slug="sabonetes", name="Sabonetes", platforms=["youtube"], keywords=["sabonete"]
        ).model_dump(),
    )
    swapped = dataclasses.replace(deps, segment=other, keywords=["sabonete"])
    assert swapped.segment is other
    assert swapped.keywords == ["sabonete"]
    assert swapped.videos is deps.videos
    assert swapped.scores is deps.scores
    assert swapped.clock is deps.clock
