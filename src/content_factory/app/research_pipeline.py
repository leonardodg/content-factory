from dataclasses import dataclass, field
from datetime import UTC, datetime

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
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import CollectRun, DepthConfig, KeywordSignal


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
        social_found = social_inserted = social_updated = social_skipped = 0
        social_platforms_attempted = 0
        social_platform_errors = 0
        for platform, discoverer in deps.socials.items():
            if platform not in segment.platforms:
                continue
            social_platforms_attempted += 1
            started = datetime.now(UTC)
            run = CollectRun(
                collector=f"research:{platform}", segment_id=segment.id, started_at=started
            )
            gate_hits_before = getattr(discoverer, "gate_hits", 0)
            try:
                await deps.limiter.acquire(platform)
                candidates = await discoverer.discover_by_keywords(keywords_for_social, [], 1)
                # Fase F2: persistir pelo MESMO contrato do collect() —
                # upsert_candidates faz ON CONFLICT DO UPDATE, ciclo
                # reexecutado não duplica vídeo.
                stats = await deps.videos.upsert_candidates(segment.id, platform, candidates)
                run.status = "ok"
                run.found = len(candidates)
                run.inserted, run.updated = stats.inserted, stats.updated
                run.skipped = stats.skipped  # Fase H: era run.failed = stats.skipped
                social_found += len(candidates)
                social_inserted += stats.inserted
                social_updated += stats.updated
                social_skipped += stats.skipped
            except Exception as exc:
                # erro de 1 rede social não aborta as demais (padrão de
                # collect(), que captura por coletor); visível no envelope
                run.status = "failed"
                run.error_code = getattr(exc, "code", "internal_error")
                run.failed = 1  # Fase H: explícito no campo numérico
                social_platform_errors += 1
                counts[f"{platform}_failed"] = int(counts.get(f"{platform}_failed", 0)) + 1
            finally:
                # mesmo padrão de collect() (REVISÃO Important #1/#2): lido no
                # finally (sobrevive a discover_by_keywords levantando depois
                # de incrementar) e por delta (coletor é singleton no registry,
                # self.gate_hits acumula entre runs). Sem isso, o envelope da
                # pesquisa por keyword não distingue busca ampla por hashtag
                # do fallback de conta própria (gate #10) do Instagram.
                gate_hits_after = getattr(discoverer, "gate_hits", 0)
                gate_hits_delta = gate_hits_after - gate_hits_before
                if gate_hits_delta:
                    counts[f"{platform}_gate_hits"] = gate_hits_delta
                run.finished_at = datetime.now(UTC)
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
