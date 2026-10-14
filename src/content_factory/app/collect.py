import asyncio
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
from content_factory.domain.models import CandidateVideo, CollectRun, SegmentConfig


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
    # Teto de discover() simultâneos na fase de rede (Task 7). Ponto de
    # partida para benchmark, não limite seguro universal — a persistência
    # continua sempre sequencial na sessão compartilhada.
    max_concurrent_discoveries: int = 2


@dataclass
class DiscoverResult:
    """Resultado da fase de rede para uma plataforma (Task 6).

    Fronteira da concorrência (Etapa 0 Task 4): CollectorError E qualquer
    outra Exception viram `error` aqui — nenhuma exceção esperada escapa
    para cancelar as irmãs (paridade com o research). Só cancelamento
    (BaseException) propaga. Exceção inesperada vira
    `CollectorError("internal_error", ...)` com o original em `__cause__`.
    discover_ms é medido nesta fase e transportado; started_at/finished_at e
    persist_ms são medidos na fase de persistência de collect().
    """

    platform: str
    found: list[CandidateVideo] | None
    error: CollectorError | None
    gate_hits_delta: int
    discover_ms: int | None


@dataclass
class CollectOutcome:
    ok: bool
    partial: bool
    counts: dict[str, int | bool]  # bool só no caso {"ja_em_execucao": True}
    runs: list[CollectRun] = field(default_factory=list[CollectRun])


async def _discover_one(
    collector: Collector, segment: SegmentConfig, limit: int, deps: CollectDeps
) -> DiscoverResult:
    """Fase de rede para uma plataforma: limiter.acquire + discover.

    Devolve DiscoverResult em vez de levantar — a fase de persistência
    decide status/counts/runs. Exceção fora do contrato vira
    `internal_error` (defesa em profundidade); só cancelamento propaga.
    gate_hits é lido como delta before/after porque o collector é singleton
    no registry (self.gate_hits acumula entre runs).
    """
    gate_hits_before = getattr(collector, "gate_hits", 0)
    try:
        await deps.limiter.acquire(collector.platform)
    except CollectorError as exc:
        gate_hits_after = getattr(collector, "gate_hits", 0)
        return DiscoverResult(
            platform=collector.platform,
            found=None,
            error=exc,
            gate_hits_delta=gate_hits_after - gate_hits_before,
            discover_ms=None,
        )
    except Exception as exc:
        gate_hits_after = getattr(collector, "gate_hits", 0)
        error = CollectorError("internal_error", collector.platform, str(exc))
        error.__cause__ = exc
        return DiscoverResult(
            platform=collector.platform,
            found=None,
            error=error,
            gate_hits_delta=gate_hits_after - gate_hits_before,
            discover_ms=None,
        )
    discover_start = deps.clock()
    try:
        found = await collector.discover(segment, limit)
    except CollectorError as exc:
        discover_ms = _ms_between(discover_start, deps.clock())
        gate_hits_after = getattr(collector, "gate_hits", 0)
        return DiscoverResult(
            platform=collector.platform,
            found=None,
            error=exc,
            gate_hits_delta=gate_hits_after - gate_hits_before,
            discover_ms=discover_ms,
        )
    except Exception as exc:
        discover_ms = _ms_between(discover_start, deps.clock())
        gate_hits_after = getattr(collector, "gate_hits", 0)
        error = CollectorError("internal_error", collector.platform, str(exc))
        error.__cause__ = exc
        return DiscoverResult(
            platform=collector.platform,
            found=None,
            error=error,
            gate_hits_delta=gate_hits_after - gate_hits_before,
            discover_ms=discover_ms,
        )
    discover_ms = _ms_between(discover_start, deps.clock())
    gate_hits_after = getattr(collector, "gate_hits", 0)
    return DiscoverResult(
        platform=collector.platform,
        found=found,
        error=None,
        gate_hits_delta=gate_hits_after - gate_hits_before,
        discover_ms=discover_ms,
    )


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
        # Fase de rede (Task 7 + Etapa 0 Task 4): TaskGroup + semáforo
        # limitado. Cada task captura TUDO na PRÓPRIA fronteira (dentro de
        # _discover_one, que devolve DiscoverResult) — nenhuma exceção
        # esperada escapa, para não cancelar as irmãs; só cancelamento
        # externo (BaseException) aborta o grupo. Resultados indexados por
        # posição: a persistência segue a ordem de deps.collectors, não a
        # de término.
        wanted = [c for c in deps.collectors if c.platform in platforms]
        slots: dict[int, DiscoverResult] = {}
        semaphore = asyncio.Semaphore(max(1, deps.max_concurrent_discoveries))

        async def _run(index: int, collector: Collector) -> None:
            async with semaphore:
                slots[index] = await _discover_one(collector, segment, limit, deps)

        async with asyncio.TaskGroup() as task_group:
            for index, collector in enumerate(wanted):
                task_group.create_task(_run(index, collector))
        discoveries: list[DiscoverResult] = [slots[index] for index in range(len(wanted))]
        # Fase de persistência (sempre sequencial: mesma sessão Postgres).
        for result in discoveries:
            started = deps.clock()
            run = CollectRun(collector=result.platform, segment_id=segment.id, started_at=started)
            run.discover_ms = result.discover_ms
            try:
                if result.error is not None:
                    exc = result.error
                    run.status = "blocked" if exc.code == "blocked" else "failed"
                    run.error_code = exc.code
                    run.failed = 1  # Fase H: explícito no campo numérico
                    errors += 1
                    counts["failed"] = int(counts["failed"]) + 1
                else:
                    # invariante: erro↔found None (sem assert: B101; erro interno
                    # explícito se a fronteira de _discover_one for violada)
                    if result.found is None:
                        raise RuntimeError(f"discover sem erro nem found: {result.platform}")
                    try:
                        persist_start = deps.clock()
                        stats = await deps.videos.upsert_candidates(
                            segment.id, result.platform, result.found
                        )
                    except CollectorError as exc:
                        run.status = "blocked" if exc.code == "blocked" else "failed"
                        run.error_code = exc.code
                        run.failed = 1
                        errors += 1
                        counts["failed"] = int(counts["failed"]) + 1
                    else:
                        run.persist_ms = _ms_between(persist_start, deps.clock())
                        run.status = "ok"
                        run.found = len(result.found)
                        run.inserted, run.updated = stats.inserted, stats.updated
                        run.skipped = stats.skipped
                        counts["found"] = int(counts["found"]) + len(result.found)
                        counts["inserted"] = int(counts["inserted"]) + stats.inserted
                        counts["updated"] = int(counts["updated"]) + stats.updated
                        counts["skipped"] = int(counts["skipped"]) + stats.skipped
            finally:
                if result.gate_hits_delta:
                    counts[f"{result.platform}_gate_hits"] = result.gate_hits_delta
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
