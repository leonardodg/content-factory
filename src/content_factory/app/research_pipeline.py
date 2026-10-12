from dataclasses import dataclass, field

from content_factory.app.ports import (
    CollectRunRepository,
    KeywordExpander,
    KeywordSignalRepository,
    Lock,
    RateLimiter,
    SegmentRepository,
    SocialDiscoverer,
    TrendsProvider,
    VideoRepository,
)
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, KeywordSignal


@dataclass
class ResearchDeps:
    web: KeywordExpander
    trends: TrendsProvider
    socials: dict[str, SocialDiscoverer]
    signals: KeywordSignalRepository
    segments: SegmentRepository
    videos: VideoRepository
    runs: CollectRunRepository
    lock: Lock
    limiter: RateLimiter
    guard: FanoutGuard


@dataclass
class ResearchOutcome:
    ok: bool
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

        all_signals = web_signals + trend_signals
        counts["signals_saved"] = await deps.signals.upsert_many(segment.id, all_signals)

        keywords_for_social = [s.keyword for s in all_signals]
        social_found = 0
        for platform, discoverer in deps.socials.items():
            if platform not in segment.platforms:
                continue
            try:
                await deps.limiter.acquire(platform)
                candidates = await discoverer.discover_by_keywords(keywords_for_social, [], 1)
                social_found += len(candidates)
            except Exception:
                # erro de 1 rede social não aborta as demais (padrão de collect())
                continue
        counts["social_found"] = social_found

        return ResearchOutcome(ok=True, counts=counts)
    finally:
        await deps.lock.release(lock_key)
