from typing import ClassVar

from tests.stubs import FakeCollector, FakeLimiter, FakeLock, FakeRepos

from content_factory.app.collect import CollectDeps, collect
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import CandidateVideo, SegmentConfig


class _FakeGatedCollector:
    platform: ClassVar[str] = "instagram"

    def __init__(self) -> None:
        self.gate_hits = 2

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        return []


async def test_collect_partial_failure_marks_partial() -> None:
    good = FakeCollector("youtube", n=3)
    bad = FakeCollector("tiktok", fail=CollectorError("blocked", "tiktok"))
    deps = CollectDeps(
        collectors=[good, bad],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    out = await collect("candles", ["youtube", "tiktok"], limit=10, deps=deps)
    assert out.ok is False and out.partial is True
    assert out.counts["inserted"] == 3
    run_bad = next(r for r in out.runs if r.collector == "tiktok")
    assert run_bad.status in {"blocked", "failed"} and run_bad.error_code == "blocked"


async def test_rerun_updates_only_with_failure_is_still_partial() -> None:
    repos = FakeRepos(updates_only=True)
    deps = CollectDeps(
        collectors=[
            FakeCollector("youtube", n=3),
            FakeCollector("tiktok", fail=CollectorError("blocked", "tiktok")),
        ],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    out = await collect("candles", ["youtube", "tiktok"], limit=10, deps=deps)
    assert out.ok is False
    assert out.counts["inserted"] == 0
    assert out.counts["updated"] == 3
    assert out.partial is True


async def test_lock_prevents_second_run() -> None:
    deps = CollectDeps(
        collectors=[FakeCollector("youtube")],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=FakeLock(acquired=False),
    )
    out = await collect("candles", ["youtube"], limit=5, deps=deps)
    assert out.ok is False and out.counts == {"ja_em_execucao": True}


async def test_lock_is_released_after_run() -> None:
    lock = FakeLock()
    deps = CollectDeps(
        collectors=[FakeCollector("youtube", n=2)],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=lock,
    )
    first = await collect("candles", ["youtube"], limit=5, deps=deps)
    second = await collect("candles", ["youtube"], limit=5, deps=deps)
    assert first.ok and second.ok, "segunda execucao devia adquirir o lock de novo"
    assert lock.acquired == ["collect:candles", "collect:candles"]
    assert lock.released == ["collect:candles", "collect:candles"]


async def test_limiter_blocked_marks_run_blocked_without_discover() -> None:
    collector = FakeCollector("youtube")
    limiter = FakeLimiter(fail=CollectorError("blocked", "youtube"))
    deps = CollectDeps(
        collectors=[collector],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=limiter,
        lock=FakeLock(),
    )
    out = await collect("candles", ["youtube"], limit=5, deps=deps)
    assert out.ok is False and out.partial is False
    assert out.counts["failed"] == 1 and out.counts["inserted"] == 0
    assert limiter.acquired == ["youtube"]
    run = out.runs[0]
    assert run.status == "blocked" and run.error_code == "blocked"
    assert run.finished_at is not None


async def test_skipped_candidates_visible_in_run_failed_and_counts() -> None:
    """Candidato descartado pelo savepoint (REVISÃO §6 #1) aparece no audit.

    O repositório reverte só o candidato doente; o use-case precisa
    refletir `UpsertStats.skipped` em `CollectRun.failed` e em
    `counts["skipped"]` — senão o playtest viraria `ok` silencioso.
    `counts["failed"]` fica só para falha de plataforma (mensagem da CLI).
    """
    repos = FakeRepos(skipped=2)
    deps = CollectDeps(
        collectors=[FakeCollector("youtube", n=3)],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    out = await collect("candles", ["youtube"], limit=5, deps=deps)
    assert out.ok is True  # plataforma respondeu; só candidatos caíram
    assert out.counts["skipped"] == 2
    assert out.counts["failed"] == 0
    assert out.runs[0].failed == 2
    assert out.runs[0].status == "ok"


async def test_collect_exposes_gate_hits_counter() -> None:
    """Contador do gate #10 (REVISÃO/telemetria): duck-typed, só aparece se > 0."""
    deps = CollectDeps(
        collectors=[_FakeGatedCollector()],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    out = await collect("candles", ["instagram"], limit=5, deps=deps)
    assert out.counts["instagram_gate_hits"] == 2
