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


@dataclass
class CollectDeps:
    collectors: list[Collector]
    videos: VideoRepository
    segments: SegmentRepository
    runs: CollectRunRepository
    limiter: RateLimiter
    lock: Lock


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
        }
        runs: list[CollectRun] = []
        errors = 0
        for collector in deps.collectors:
            if collector.platform not in platforms:
                continue
            started = datetime.now(UTC)
            run = CollectRun(
                collector=collector.platform, segment_id=segment.id, started_at=started
            )
            try:
                await deps.limiter.acquire(collector.platform)
                found = await collector.discover(segment, limit)
                stats = await deps.videos.upsert_candidates(segment.id, collector.platform, found)
                run.status = "ok"
                run.found = len(found)
                run.inserted, run.updated = stats.inserted, stats.updated
                counts["found"] = int(counts["found"]) + len(found)
                counts["inserted"] = int(counts["inserted"]) + stats.inserted
                counts["updated"] = int(counts["updated"]) + stats.updated
            except CollectorError as exc:
                run.status = "blocked" if exc.code == "blocked" else "failed"
                run.error_code = exc.code
                errors += 1
                counts["failed"] = int(counts["failed"]) + 1
            finally:
                run.finished_at = datetime.now(UTC)
                await deps.runs.record(run)
                runs.append(run)
        ok = errors == 0
        return CollectOutcome(
            ok=ok,
            partial=(not ok and int(counts["inserted"]) > 0),
            counts=counts,
            runs=runs,
        )
    finally:
        # SEM release o lock eterno bloqueia a próxima execução — bug clássico
        await deps.lock.release(f"collect:{segment_slug}")
