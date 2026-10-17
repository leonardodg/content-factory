"""R7 — remede os vídeos do segmento descobertos nos últimos N dias.

Sem isso não há curva de engajamento: hoje um vídeo só é remedido quando a
busca o encontra de novo (103 de 427 com 2+ medições). A remedição grava só
`video_metrics` — nunca metadados nem `discovery_source` (papel do
`upsert_candidates`). Mesmo contrato da coleta: limiter, cota por
`counting()`, breaker e uma `CollectRun` `remediate:<plataforma>`; erro de
contrato vira run `failed` e não derruba a coleta da mesma UoW.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from content_factory.app.call_counters import counting
from content_factory.app.platform_health import NoPlatformHealth, report_discover
from content_factory.app.ports import (
    CollectRunRepository,
    PlatformHealth,
    RateLimiter,
    RefreshableCollector,
    RemediationRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    RemediationTarget,
    VideoMetrics,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _metrics_of(candidate: CandidateVideo) -> VideoMetrics:
    """Métrica medida na remedição (shares/saves só quando o coletor expõe)."""
    return VideoMetrics(
        views=candidate.views,
        likes=candidate.likes,
        comments=candidate.comments,
        shares=candidate.shares,
        saves=candidate.saves,
    )


@dataclass
class RemediationDeps:
    """Portas da remedição. Limiter e breaker são os mesmos do `CollectDeps`:
    a remedição e a busca disputam a mesma cota do YouTube."""

    videos: RemediationRepository
    refreshables: dict[str, RefreshableCollector]
    runs: CollectRunRepository
    limiter: RateLimiter
    health: PlatformHealth = field(default_factory=NoPlatformHealth)
    clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow)
    window_days: int = 7
    limit: int = 500
    min_gap_hours: int = 3


@dataclass
class RemediationOutcome:
    """Sub-envelope `remediation` da coleta (só `int`: vai direto pro JSON)."""

    targets: int = 0
    refreshed: int = 0
    metrics_written: int = 0
    removed: int = 0
    failed_platforms: int = 0
    quota_used: int = 0


async def remediate(segment_id: int, deps: RemediationDeps) -> RemediationOutcome:
    platforms = sorted(deps.refreshables)
    targets = await deps.videos.remediation_targets(
        segment_id, deps.window_days, deps.limit, deps.min_gap_hours, platforms
    )
    out = RemediationOutcome(targets=len(targets))
    by_platform: dict[str, list[RemediationTarget]] = {}
    for t in targets:
        by_platform.setdefault(t.platform, []).append(t)

    missing_video_ids: list[int] = []
    for platform, group in sorted(by_platform.items()):
        refreshable = deps.refreshables[platform]
        by_pid = {t.platform_video_id: t for t in group}
        run = CollectRun(
            collector=f"remediate:{platform}", segment_id=segment_id, started_at=deps.clock()
        )
        with counting() as counters:
            try:
                await deps.limiter.acquire(platform)
                result = await refreshable.refresh(group)
            except CollectorError as exc:
                await report_discover(deps.health, platform, exc.code)
                run.status, run.error_code, run.failed = "failed", exc.code, 1
                out.failed_platforms += 1
                result = None
            else:
                await report_discover(deps.health, platform, None)
        run.quota_used = counters.quota_used
        out.quota_used += counters.quota_used
        if result is not None:
            for cand in result.candidates:
                target = by_pid.get(cand.platform_video_id)
                if target is None:
                    continue
                await deps.videos.record_metrics(target, _metrics_of(cand), cand.creator_followers)
                out.metrics_written += 1
            missing_video_ids += [by_pid[m].video_id for m in result.missing_ids if m in by_pid]
            out.refreshed += len(result.candidates)
            run.status, run.found, run.updated = "ok", len(group), len(result.candidates)
        run.finished_at = deps.clock()
        await deps.runs.record(run)
    out.removed = await deps.videos.mark_removed(missing_video_ids)
    return out
