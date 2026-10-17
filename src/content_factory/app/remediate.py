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
from content_factory.domain.language import language_matches
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
    a remedição e a busca disputam a mesma cota do YouTube.

    `accepted_language` é o idioma exigido do segmento (`accepted_language` ou
    `relevance_language` da busca): o alvo que não casa (`pt-PT` com `pt-BR`)
    sai antes do coletor, com a MESMA regra da coleta. Sem exigência (ou sem
    evidência de idioma), mantém.

    `batch_size` é o teto de alvos por chamada de `refresh` (`videos.list`
    aceita 50 ids): o use-case fatia e grava cada lote, para o erro de um lote
    não custar as métricas dos anteriores.
    """

    videos: RemediationRepository
    refreshables: dict[str, RefreshableCollector]
    runs: CollectRunRepository
    limiter: RateLimiter
    health: PlatformHealth = field(default_factory=NoPlatformHealth)
    clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow)
    window_days: int = 7
    limit: int = 500
    min_gap_hours: int = 3
    batch_size: int = 50
    accepted_language: str | None = None


@dataclass
class RemediationOutcome:
    """Sub-envelope `remediation` da coleta (só `int`: vai direto pro JSON).

    `targets` é o que a remediação TENTOU remedir (já sem os descartados pelo
    idioma); `skipped_language` conta os descartados — os dois somam os alvos
    que o repositório devolveu.
    """

    targets: int = 0
    skipped_language: int = 0
    refreshed: int = 0
    metrics_written: int = 0
    removed: int = 0
    failed_platforms: int = 0
    quota_used: int = 0


async def remediate(segment_id: int, deps: RemediationDeps) -> RemediationOutcome:
    platforms = sorted(deps.refreshables)
    found = await deps.videos.remediation_targets(
        segment_id, deps.window_days, deps.limit, deps.min_gap_hours, platforms
    )
    # R14/17-10: o público é o Brasil. O vídeo já foi descoberto pela busca
    # filtrada, mas linhas antigas (pt-PT) seguem no segmento; a remediação
    # aplica a mesma regra antes de gastar cota.
    targets = [t for t in found if language_matches(t.language, deps.accepted_language)]
    out = RemediationOutcome(targets=len(targets), skipped_language=len(found) - len(targets))
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
        error: CollectorError | None = None
        updated = 0
        with counting() as counters:
            # Lotes de `batch_size`: cada lote é gravado antes do próximo. Erro
            # adiante (contrato ou fora dele) PARA os lotes seguintes, mas o que
            # já foi medido fica (a UoW do segmento não reverte por causa daqui).
            for start in range(0, len(group), deps.batch_size):
                batch = group[start : start + deps.batch_size]
                try:
                    await deps.limiter.acquire(platform)
                    result = await refreshable.refresh(batch)
                except CollectorError as exc:
                    error = exc
                    break
                except Exception as exc:
                    # Fora do contrato (bug no parser etc.) vira `internal_error`
                    # com o traceback no log — MESMA defesa da coleta em
                    # profundidade: a exceção não derruba a UoW do segmento.
                    logger.exception(
                        "remediação fora do contrato: segmento=%s plataforma=%s",
                        segment_id,
                        platform,
                    )
                    error = CollectorError("internal_error", platform, str(exc))
                    error.__cause__ = exc
                    break
                for cand in result.candidates:
                    target = by_pid.get(cand.platform_video_id)
                    if target is None:
                        continue
                    await deps.videos.record_metrics(
                        target, _metrics_of(cand), cand.creator_followers
                    )
                    out.metrics_written += 1
                missing_video_ids += [by_pid[m].video_id for m in result.missing_ids if m in by_pid]
                out.refreshed += len(result.candidates)
                updated += len(result.candidates)
            await report_discover(deps.health, platform, error.code if error else None)
        run.quota_used = counters.quota_used
        out.quota_used += counters.quota_used
        run.found, run.updated = len(group), updated
        if error is None:
            run.status = "ok"
        else:
            run.status, run.error_code, run.failed = "failed", error.code, 1
            out.failed_platforms += 1
        run.finished_at = deps.clock()
        await deps.runs.record(run)
    out.removed = await deps.videos.mark_removed(missing_video_ids)
    return out
