import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from content_factory.app.concurrency import PlatformSlots, bounded_gather
from content_factory.app.gate_hits import counting
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

logger = logging.getLogger(__name__)


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
    # Teto de discover() simultâneos POR PLATAFORMA, compartilhado entre
    # segmentos (Etapa 0 Task 7 — ex.: tiktok=1 serializa as sessões
    # Playwright no processo). Default vazio = sem teto.
    platform_slots: PlatformSlots = field(default_factory=PlatformSlots)


@dataclass
class DiscoverResult:
    """Resultado da fase de rede para uma plataforma (Task 6).

    Fronteira da concorrência (Etapa 0 Task 4): CollectorError E qualquer
    outra Exception viram `error` aqui — nenhuma exceção esperada escapa
    para cancelar as irmãs (paridade com o research).     Só cancelamento
    (BaseException) propaga. Exceção inesperada vira
    `CollectorError("internal_error", ...)` com o original em `__cause__`.
    discover_ms é medido nesta fase e transportado; started_at abre esta
    fase (antes do acquire) e é transportado para a run; finished_at e
    persist_ms são medidos na fase de persistência de collect().
    """

    platform: str
    found: list[CandidateVideo] | None
    error: CollectorError | None
    gate_hits: int  # desta chamada (Etapa 1 Task 9)
    discover_ms: int | None
    started_at: datetime


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
    gate_hits é contado POR CHAMADA (`counting()`, Etapa 1 Task 9): o
    collector é singleton e segmentos concorrentes o compartilham, então o
    delta do atributo somaria hits de outros segmentos.
    """
    platform = collector.platform
    found: list[CandidateVideo] | None = None
    error: CollectorError | None = None
    discover_start: datetime | None = None
    # Teto por plataforma (Etapa 0 Task 7): a vaga cobre acquire + discover.
    # started_at é lido DENTRO da vaga (mas antes do acquire) para a duração
    # não contar espera na fila. Comparar com o baseline por discover_ms +
    # persist_ms: finished_at sai após a rede de TODAS as irmãs (review).
    async with deps.platform_slots.slot(platform):
        started_at = deps.clock()
        with counting() as gate_hits:
            try:
                await deps.limiter.acquire(platform)
                discover_start = deps.clock()
                found = await collector.discover(segment, limit)
            except CollectorError as exc:
                error = exc
            except Exception as exc:
                # run só guarda error_code: a causa vai para o log (Task 8.1)
                logger.exception("discover fora do contrato: %s/%s", segment.slug, platform)
                error = CollectorError("internal_error", platform, str(exc))
                error.__cause__ = exc
        # discover_ms só existe se o acquire passou (falha no limiter = None)
        discover_ms = None if discover_start is None else _ms_between(discover_start, deps.clock())
        return DiscoverResult(
            platform=platform,
            found=found,  # só atribuído no sucesso: erro ⇒ None
            error=error,
            gate_hits=gate_hits.total,
            discover_ms=discover_ms,
            started_at=started_at,
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
        # Fase de rede (Task 7 + Etapa 0 Task 4 + Etapa 2 Task 12):
        # bounded_gather = TaskGroup + semáforo, resultado na ordem de
        # deps.collectors (não na de término). _discover_one captura TUDO na
        # própria fronteira — nenhuma exceção esperada escapa para cancelar
        # as irmãs; só cancelamento externo aborta o grupo.
        wanted = [c for c in deps.collectors if c.platform in platforms]
        discoveries = await bounded_gather(
            wanted,
            deps.max_concurrent_discoveries,
            lambda collector: _discover_one(collector, segment, limit, deps),
        )
        # Fase de persistência (sempre sequencial: mesma sessão Postgres).
        for result in discoveries:
            run = CollectRun(
                collector=result.platform, segment_id=segment.id, started_at=result.started_at
            )
            run.discover_ms = result.discover_ms
            # Sem try/finally (Etapa 1, /code-review): erro fora do contrato
            # (infra — a sessão fica inutilizável) PROPAGA original; gravar a
            # run num `finally` levantaria PendingRollbackError e mascararia
            # a causa. A UoW do segmento reverte tudo, então não há run a
            # gravar nesse caminho.
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
            if result.gate_hits:
                counts[f"{result.platform}_gate_hits"] = result.gate_hits
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
