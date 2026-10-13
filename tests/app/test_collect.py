from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from tests.stubs import FakeCollector, FakeLimiter, FakeLock, FakeRepos

from content_factory.app.collect import CollectDeps, DiscoverResult, _discover_one, collect
from content_factory.app.ports import Collector, VideoRepository
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import CandidateVideo, SegmentConfig, UpsertStats, VideoSnapshot


class _FakeGatedCollector:
    """Incrementa gate_hits durante discover() (como instagram.py faz ao
    cair no fallback do gate #10) — necessário para o teste de delta
    por-run (Important #2): valor pré-existente no __init__ não conta
    como hit "deste run"."""

    platform: ClassVar[str] = "instagram"

    def __init__(self) -> None:
        self.gate_hits = 0

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        self.gate_hits += 2
        return []


class _FakeGatedThenFailingCollector:
    """Incrementa gate_hits (como instagram.py faz antes do fallback) e,
    em seguida, levanta CollectorError — reproduz o caso em que o gate #10
    acontece também na própria mídia (REVISÃO Important #1)."""

    platform: ClassVar[str] = "instagram"

    def __init__(self) -> None:
        self.gate_hits = 0

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        self.gate_hits += 1
        raise CollectorError("failed", "instagram")


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
    assert run.failed == 1  # Fase H: explícito no campo numérico, não só em status
    assert run.skipped == 0
    assert run.finished_at is not None


async def test_skipped_candidates_visible_in_run_skipped_and_counts() -> None:
    """Candidato descartado pelo savepoint (REVISÃO §6 #1) aparece no audit.

    Fase H: separado de falha de plataforma — CollectRun.skipped != .failed.
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
    assert out.runs[0].skipped == 2  # era out.runs[0].failed == 2
    assert out.runs[0].failed == 0  # Fase H: failed não carrega mais skip
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


async def test_collect_exposes_gate_hits_even_when_discover_fails() -> None:
    """Important #1: gate_hits incrementado e DEPOIS discover() levanta erro.

    O contador não pode se perder só porque o try pulou pro except — a
    leitura precisa acontecer no finally, não só no caminho de sucesso.
    """
    deps = CollectDeps(
        collectors=[_FakeGatedThenFailingCollector()],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    out = await collect("candles", ["instagram"], limit=5, deps=deps)
    assert out.ok is False
    assert out.counts["instagram_gate_hits"] == 1


class _FakeClock:
    """Relógio manual: só avança quando o teste manda (discover/upsert)."""

    def __init__(self) -> None:
        self._now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def advance_ms(self, ms: int) -> None:
        self._now += timedelta(milliseconds=ms)


def _TimedCollector(platform: str, clock: _FakeClock, discover_ms: int, n: int = 2) -> Collector:
    """Collector que consome `discover_ms` do relógio fake durante discover()."""
    name = platform

    class Impl:
        platform: ClassVar[str] = name

        async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
            clock.advance_ms(discover_ms)
            return [
                CandidateVideo(
                    platform=name,
                    platform_video_id=f"{name}-{i}",
                    url=f"https://example.invalid/v/{i}",
                    creator_handle="stub",
                    title=f"video {i}",
                )
                for i in range(n)
            ]

    return Impl()


def _TimedVideos(clock: _FakeClock, persist_ms: int) -> VideoRepository:
    """Videos que consome `persist_ms` do relógio fake durante upsert_candidates()."""

    class Impl:
        async def upsert_candidates(
            self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
        ) -> UpsertStats:
            clock.advance_ms(persist_ms)
            return UpsertStats(inserted=len(candidates))

        async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
            return []

    return Impl()


async def test_collect_records_discover_and_persist_ms_separately() -> None:
    """Task 4 (baseline): tempo de rede vs tempo de persistência separados.

    Clock fake avança 150ms em discover() e 40ms em upsert_candidates() —
    o CollectRun gravado precisa carregar os dois campos separados, não um total.
    """
    clock = _FakeClock()
    repos = FakeRepos()
    deps = CollectDeps(
        collectors=[_TimedCollector("youtube", clock, discover_ms=150)],
        videos=_TimedVideos(clock, persist_ms=40),
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
        clock=clock,
    )
    out = await collect("candles", ["youtube"], limit=5, deps=deps)
    assert out.ok is True
    run = out.runs[0]
    assert run.discover_ms == 150
    assert run.persist_ms == 40


async def test_collect_failure_still_records_discover_ms_without_persist_ms() -> None:
    """discover() que levanta registra o custo da fase de rede; sem persistência."""
    clock = _FakeClock()
    repos = FakeRepos()
    deps = CollectDeps(
        collectors=[
            _TimedCollector("youtube", clock, discover_ms=150),
            FakeCollector("tiktok", fail=CollectorError("blocked", "tiktok")),
        ],
        videos=_TimedVideos(clock, persist_ms=40),
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
        clock=clock,
    )
    out = await collect("candles", ["youtube", "tiktok"], limit=5, deps=deps)
    assert out.ok is False
    run_ok = next(r for r in out.runs if r.collector == "youtube")
    assert (run_ok.discover_ms, run_ok.persist_ms) == (150, 40)
    run_bad = next(r for r in out.runs if r.collector == "tiktok")
    assert run_bad.status == "blocked"
    assert run_bad.persist_ms is None


async def test_discover_one_returns_result_with_discover_ms() -> None:
    """Task 6 (fronteira de rede): DiscoverResult carrega discover_ms e delta.

    Escolha de temporização documentada: discover_ms é medido na fase de rede
    (dentro de _discover_one) e transportado no DiscoverResult; started_at /
    finished_at e persist_ms são medidos na fase de persistência de collect().
    Com execução sequencial e clock fake que só avança em discover/upsert,
    os valores são idênticos aos do caminho antigo entrelaçado.
    """
    clock = _FakeClock()
    repos = FakeRepos()
    segment = await repos.segments.get_by_slug("candles")
    deps = CollectDeps(
        collectors=[],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
        clock=clock,
    )
    result = await _discover_one(
        _TimedCollector("youtube", clock, discover_ms=150), segment, 5, deps
    )
    assert isinstance(result, DiscoverResult)
    assert result.platform == "youtube"
    assert result.found is not None and len(result.found) == 2
    assert result.error is None
    assert result.discover_ms == 150
    assert result.gate_hits_delta == 0


async def test_discover_one_captures_collector_error_without_losing_gate_delta() -> None:
    """CollectorError na rede vira DiscoverResult com erro (não levanta).

    Limiter bloqueado → discover_ms None (fase de rede nem abriu).
    discover() que falha após gate_hits → discover_ms medido até o erro.
    """
    clock = _FakeClock()
    repos = FakeRepos()
    segment = await repos.segments.get_by_slug("candles")

    blocked_deps = CollectDeps(
        collectors=[],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(fail=CollectorError("blocked", "youtube")),
        lock=FakeLock(),
        clock=clock,
    )
    blocked = await _discover_one(FakeCollector("youtube"), segment, 5, blocked_deps)
    assert blocked.found is None
    assert blocked.error is not None and blocked.error.code == "blocked"
    assert blocked.discover_ms is None
    assert blocked.gate_hits_delta == 0

    net_deps = CollectDeps(
        collectors=[],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
        clock=clock,
    )
    failed = await _discover_one(_FakeGatedThenFailingCollector(), segment, 5, net_deps)
    assert failed.found is None
    assert failed.error is not None and failed.error.code == "failed"
    assert failed.gate_hits_delta == 1
    assert failed.discover_ms == 0


async def test_collect_gate_hits_is_per_run_delta_not_cumulative() -> None:
    """Important #2: collector é singleton no registry — self.gate_hits

    acumula entre runs (vida do worker). O envelope de cada run deve
    reportar só o delta daquele run, não o total acumulado desde o boot.
    """
    collector = _FakeGatedCollector()  # incrementa +2 a cada discover()
    deps = CollectDeps(
        collectors=[collector],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    # 1ª run (mesma instância, como no worker singleton): +2 → total 2.
    first = await collect("candles", ["instagram"], limit=5, deps=deps)
    assert first.counts["instagram_gate_hits"] == 2

    # 2ª run na MESMA instância: discover() soma +2 de novo (total acumulado
    # vai a 4), mas o envelope precisa reportar só o delta deste run (2),
    # não o acumulado (4) nem o total absoluto do atributo.
    second = await collect("candles", ["instagram"], limit=5, deps=deps)
    assert second.counts["instagram_gate_hits"] == 2
    assert collector.gate_hits == 4  # atributo do singleton continua acumulando
