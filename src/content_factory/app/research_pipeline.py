import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from content_factory.app.concurrency import PlatformSlots
from content_factory.app.ports import (
    CollectRunRepository,
    KeywordExpander,
    KeywordSignalRepository,
    Lock,
    RateLimiter,
    SegmentRepository,
    SocialDiscoverer,
    TrendObservationRepository,
    TrendsProvider,
    VideoRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import CandidateVideo, CollectRun, DepthConfig, KeywordSignal

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ResearchDeps:
    web: KeywordExpander
    trends: TrendsProvider
    socials: dict[str, SocialDiscoverer]
    signals: KeywordSignalRepository
    trend_observations: TrendObservationRepository
    segments: SegmentRepository
    videos: VideoRepository
    runs: CollectRunRepository
    lock: Lock
    limiter: RateLimiter
    guard: FanoutGuard
    # Teto de discover_by_keywords() simultâneos na fase de rede (Task 8).
    # Mesmo nome de CollectDeps.max_concurrent_discoveries de propósito: um
    # só conceito ("fan-out de rede limitado") nos dois use-cases, ponto de
    # partida para benchmark, não limite seguro universal — a persistência
    # continua sempre sequencial na sessão compartilhada. ResearchDeps é
    # @dataclass como CollectDeps, então o default mantém as chamadas
    # existentes (worker/testes) funcionando sem mudança.
    max_concurrent_discoveries: int = 2
    # Mesmo padrão de CollectDeps: clock injetável, default = agora em UTC —
    # chamadas existentes (worker/CLI) continuam funcionando sem mudança.
    clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow)
    # Teto de discover_by_keywords() simultâneos POR PLATAFORMA, compartilhado
    # entre segmentos (Etapa 0 Task 7). Default vazio = sem teto.
    platform_slots: PlatformSlots = field(default_factory=PlatformSlots)


@dataclass
class SocialDiscoverResult:
    """Resultado da fase de rede para uma rede social (Task 8).

    Fronteira da concorrência: QUALQUER Exception é capturada aqui (não só
    CollectorError como em collect) — preserva o `except Exception` do loop
    sequencial, onde erro de 1 rede vira CollectRun failed sem abortar as
    irmãs. Cancelamento (BaseException) continua propagando.
    gate_hits_delta é medido nesta fase e transportado; started_at abre
    esta fase (antes do acquire) e é transportado para a run; finished_at
    é medido na fase de persistência, como em collect().
    """

    platform: str
    candidates: list[CandidateVideo] | None
    error: Exception | None
    gate_hits_delta: int
    started_at: datetime


async def _discover_social_one(
    segment_slug: str,
    platform: str,
    discoverer: SocialDiscoverer,
    keywords: list[str],
    deps: ResearchDeps,
) -> SocialDiscoverResult:
    """Fase de rede para uma rede social: limiter.acquire + discover_by_keywords.

    Devolve SocialDiscoverResult em vez de levantar — a fase de
    persistência decide status/counts/runs. gate_hits é lido como delta
    before/after porque o discoverer é singleton no registry
    (self.gate_hits acumula entre runs).
    """
    gate_hits_before = getattr(discoverer, "gate_hits", 0)
    # Teto por plataforma (Etapa 0 Task 7): a vaga cobre acquire + discover;
    # started_at é lido DENTRO da vaga para a duração medir trabalho (Task 6).
    async with deps.platform_slots.slot(platform):
        started_at = deps.clock()
        try:
            await deps.limiter.acquire(platform)
            candidates = await discoverer.discover_by_keywords(keywords, [], 1)
        except Exception as exc:
            if not isinstance(exc, CollectorError):
                # run só guarda error_code: a causa vai para o log (Task 8.1)
                logger.exception("discover fora do contrato: %s/%s", segment_slug, platform)
            gate_hits_after = getattr(discoverer, "gate_hits", 0)
            return SocialDiscoverResult(
                platform=platform,
                candidates=None,
                error=exc,
                gate_hits_delta=gate_hits_after - gate_hits_before,
                started_at=started_at,
            )
        gate_hits_after = getattr(discoverer, "gate_hits", 0)
        return SocialDiscoverResult(
            platform=platform,
            candidates=candidates,
            error=None,
            gate_hits_delta=gate_hits_after - gate_hits_before,
            started_at=started_at,
        )


@dataclass
class ResearchOutcome:
    ok: bool
    partial: bool = False
    counts: dict[str, int | bool] = field(default_factory=dict[str, int | bool])


async def run_research_cycle(
    segment_slug: str, depth_cfg: DepthConfig, deps: ResearchDeps
) -> ResearchOutcome:
    lock_key = f"research:{segment_slug}"
    if not await deps.lock.acquire_once(lock_key):
        return ResearchOutcome(ok=False, counts={"ja_em_execucao": True})
    try:
        segment = await deps.segments.get_by_slug(segment_slug)
        counts: dict[str, int | bool] = {
            "web_found": 0,
            "trends_found": 0,
            "signals_saved": 0,
            "social_found": 0,
            "social_inserted": 0,
            "social_updated": 0,
            "social_skipped": 0,
        }

        seeds = [k for k in segment.keywords if deps.guard.admit(k, 0)]
        await deps.limiter.acquire(deps.web.platform)
        web_signals = deps.guard.truncate(await deps.web.expand(seeds, 0), "web")
        counts["web_found"] = len(web_signals)

        trend_keywords = [s.keyword for s in web_signals if deps.guard.admit(s.keyword, 1)]
        await deps.limiter.acquire("trends")
        trend_points = await deps.trends.interest_over_time(trend_keywords, "week")
        trend_signals = deps.guard.truncate(
            [
                KeywordSignal(keyword=p.keyword, source="trends", weight=p.interest / 100, depth=1)
                for p in trend_points
            ],
            "trends",
        )
        counts["trends_found"] = len(trend_signals)

        # Fase F3: gravar série temporal append-only (separada do signal
        # de descoberta que faz upsert e sobrescreve o peso anterior)
        await deps.trend_observations.record_many(segment.id, trend_points)

        all_signals = web_signals + trend_signals
        counts["signals_saved"] = await deps.signals.upsert_many(segment.id, all_signals)

        keywords_for_social = [s.keyword for s in all_signals]
        # Fase de rede (Task 8): TaskGroup + semáforo limitado, mesmo padrão
        # da Task 7 em collect(). Cada task captura Exception na PRÓPRIA
        # fronteira (dentro de _discover_social_one, que devolve
        # SocialDiscoverResult) — nenhum erro de rede escapa, para não
        # cancelar as irmãs. O FanoutGuard NÃO é consultado aqui dentro:
        # admit/truncate já aconteceram acima (seeds, web, trends), ANTES
        # de agendar as tasks — o guard muta `_seen`/`_visited_count` e não
        # é task-safe; consultá-lo sob concorrência corromperia o dedupe.
        # Resultados indexados por posição: a persistência segue a ordem de
        # deps.socials, não a de término.
        wanted: list[tuple[str, SocialDiscoverer]] = [
            (platform, discoverer)
            for platform, discoverer in deps.socials.items()
            if platform in segment.platforms
        ]
        slots: dict[int, SocialDiscoverResult] = {}
        semaphore = asyncio.Semaphore(max(1, deps.max_concurrent_discoveries))

        async def _run(index: int, platform: str, discoverer: SocialDiscoverer) -> None:
            async with semaphore:
                slots[index] = await _discover_social_one(
                    segment_slug, platform, discoverer, keywords_for_social, deps
                )

        async with asyncio.TaskGroup() as task_group:
            for index, (platform, discoverer) in enumerate(wanted):
                task_group.create_task(_run(index, platform, discoverer))
        discoveries: list[SocialDiscoverResult] = [slots[index] for index in range(len(wanted))]
        # Fase de persistência (sempre sequencial: mesma sessão Postgres).
        social_found = social_inserted = social_updated = social_skipped = 0
        social_platforms_attempted = len(discoveries)
        social_platform_errors = 0
        for result in discoveries:
            platform = result.platform
            run = CollectRun(
                collector=f"research:{platform}",
                segment_id=segment.id,
                started_at=result.started_at,
            )
            try:
                if result.error is not None:
                    # erro de 1 rede social não aborta as demais (padrão de
                    # collect(), que captura por coletor); visível no envelope
                    exc = result.error
                    run.status = "failed"
                    run.error_code = getattr(exc, "code", "internal_error")
                    run.failed = 1  # Fase H: explícito no campo numérico
                    social_platform_errors += 1
                    counts[f"{platform}_failed"] = int(counts.get(f"{platform}_failed", 0)) + 1
                else:
                    # invariante: erro↔candidates None (sem assert: B101; erro
                    # interno explícito se a fronteira for violada)
                    if result.candidates is None:
                        raise RuntimeError(f"discover sem erro nem candidates: {platform}")
                    try:
                        # Fase F2: persistir pelo MESMO contrato do collect() —
                        # upsert_candidates faz ON CONFLICT DO UPDATE, ciclo
                        # reexecutado não duplica vídeo.
                        stats = await deps.videos.upsert_candidates(
                            segment.id, platform, result.candidates
                        )
                    except Exception as exc:
                        if not isinstance(exc, CollectorError):
                            logger.exception(
                                "persistência fora do contrato: %s/%s", segment.slug, platform
                            )
                        run.status = "failed"
                        run.error_code = getattr(exc, "code", "internal_error")
                        run.failed = 1
                        social_platform_errors += 1
                        counts[f"{platform}_failed"] = int(counts.get(f"{platform}_failed", 0)) + 1
                    else:
                        run.status = "ok"
                        run.found = len(result.candidates)
                        run.inserted, run.updated = stats.inserted, stats.updated
                        run.skipped = stats.skipped  # Fase H: era run.failed = stats.skipped
                        social_found += len(result.candidates)
                        social_inserted += stats.inserted
                        social_updated += stats.updated
                        social_skipped += stats.skipped
            finally:
                # mesmo padrão de collect() (REVISÃO Important #1/#2):
                # transportado como delta da fase de rede (sobrevive a
                # discover_by_keywords levantando depois de incrementar; o
                # coletor é singleton no registry, self.gate_hits acumula
                # entre runs). Sem isso, o envelope da pesquisa por keyword
                # não distingue busca ampla por hashtag do fallback de conta
                # própria (gate #10) do Instagram.
                if result.gate_hits_delta:
                    counts[f"{platform}_gate_hits"] = result.gate_hits_delta
                run.finished_at = deps.clock()
                await deps.runs.record(run)
        counts["social_found"] = social_found
        counts["social_inserted"] = social_inserted
        counts["social_updated"] = social_updated
        counts["social_skipped"] = social_skipped
        counts["social_platforms_attempted"] = social_platforms_attempted
        counts["social_platform_errors"] = social_platform_errors

        # Mesmo contrato de collect(): ok = nenhuma plataforma social
        # configurada falhou; partial = falhou alguma mas outras
        # inseriram/atualizaram candidato.
        ok = social_platform_errors == 0
        partial = (not ok) and (social_inserted + social_updated > 0)
        return ResearchOutcome(ok=ok, partial=partial, counts=counts)
    finally:
        await deps.lock.release(lock_key)
