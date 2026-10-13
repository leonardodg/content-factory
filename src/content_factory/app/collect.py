from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from content_factory.app.ports import (
    Collector,
    CollectRunRepository,
    Lock,
    RateLimiter,
    SegmentRepository,
    VideoRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import CollectRun


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ms_between(start: datetime, end: datetime) -> int:
    """Milissegundos entre duas leituras do clock (baseline Task 4)."""
    return int((end - start).total_seconds() * 1000)


@dataclass
class CollectDeps:
    collectors: list[Collector]
    videos: VideoRepository
    segments: SegmentRepository
    runs: CollectRunRepository
    limiter: RateLimiter
    lock: Lock
    # Mesmo padrão de ScoreDeps: clock injetável, default = agora em UTC —
    # chamadas existentes (worker/CLI) continuam funcionando sem mudança.
    clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow)


@dataclass
class CollectOutcome:
    ok: bool
    partial: bool
    counts: dict[str, int | bool]  # bool só no caso {"ja_em_execucao": True}
    runs: list[CollectRun] = field(default_factory=list[CollectRun])


async def collect(
    segment_slug: str, platforms: list[str], limit: int, deps: CollectDeps
) -> CollectOutcome:
    if not await deps.lock.acquire_once(f"collect:{segment_slug}"):
        return CollectOutcome(ok=False, partial=False, counts={"ja_em_execucao": True})
    try:
        segment = await deps.segments.get_by_slug(segment_slug)
        counts: dict[str, int | bool] = {
            "found": 0,
            "inserted": 0,
            "updated": 0,
            "failed": 0,
            # candidatos revertidos pelo savepoint: semântica separada de
            # "failed" (plataformas com erro) para a CLI não mentir
            "skipped": 0,
        }
        runs: list[CollectRun] = []
        errors = 0
        for collector in deps.collectors:
            if collector.platform not in platforms:
                continue
            started = deps.clock()
            run = CollectRun(
                collector=collector.platform, segment_id=segment.id, started_at=started
            )
            gate_hits_before = getattr(collector, "gate_hits", 0)
            discover_start: datetime | None = None
            try:
                await deps.limiter.acquire(collector.platform)
                discover_start = deps.clock()
                found = await collector.discover(segment, limit)
                run.discover_ms = _ms_between(discover_start, deps.clock())
                persist_start = deps.clock()
                stats = await deps.videos.upsert_candidates(segment.id, collector.platform, found)
                run.persist_ms = _ms_between(persist_start, deps.clock())
                run.status = "ok"
                run.found = len(found)
                run.inserted, run.updated = stats.inserted, stats.updated
                run.skipped = stats.skipped  # Fase H: era run.failed = stats.skipped
                counts["found"] = int(counts["found"]) + len(found)
                counts["inserted"] = int(counts["inserted"]) + stats.inserted
                counts["updated"] = int(counts["updated"]) + stats.updated
                counts["skipped"] = int(counts["skipped"]) + stats.skipped
            except CollectorError as exc:
                if discover_start is not None and run.discover_ms is None:
                    # discover() (ou o upsert) abriu a fase de rede antes de
                    # falhar — registra o custo até o erro; sem persistência.
                    run.discover_ms = _ms_between(discover_start, deps.clock())
                run.status = "blocked" if exc.code == "blocked" else "failed"
                run.error_code = exc.code
                run.failed = 1  # Fase H: explícito no campo numérico, não só em status
                errors += 1
                counts["failed"] = int(counts["failed"]) + 1
            finally:
                # Lido no finally (não só no caminho de sucesso) para não
                # perder o contador quando discover() levanta exceção DEPOIS
                # de já ter incrementado gate_hits (REVISÃO: Important #1).
                # Delta before/after porque o collector é singleton no
                # registry — self.gate_hits acumula entre runs (Important #2).
                gate_hits_after = getattr(collector, "gate_hits", 0)
                gate_hits_delta = gate_hits_after - gate_hits_before
                if gate_hits_delta:
                    counts[f"{collector.platform}_gate_hits"] = gate_hits_delta
                run.finished_at = deps.clock()
                await deps.runs.record(run)
                runs.append(run)
        ok = errors == 0
        return CollectOutcome(
            ok=ok,
            partial=(not ok and (int(counts["inserted"]) + int(counts["updated"]) > 0)),
            counts=counts,
            runs=runs,
        )
    finally:
        # SEM release o lock eterno bloqueia a próxima execução — bug clássico
        await deps.lock.release(f"collect:{segment_slug}")
