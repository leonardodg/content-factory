import asyncio
from typing import ClassVar

# classe real: o re-export `ddgs.DDGS` é proxy lazy — parcialmente Unknown p/ pyright
from ddgs.ddgs import DDGS

from content_factory.domain.models import CandidateVideo, SegmentConfig


class WebCollector:
    """Busca web (DuckDuckGo via `ddgs`, sem chave) → candidatos platform="web"."""

    platform: ClassVar[str] = "web"

    def __init__(self) -> None:
        pass  # resolve DDGS no momento do discover (monkeypatchável)

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = " OR ".join(segment.keywords[:3])
        # DDGS é síncrono (rede) — roda em thread; o lambda lê o global DDGS a
        # cada chamada, que é o que monkeypatch.setattr(web_mod, "DDGS", ...) troca
        results = await asyncio.to_thread(lambda: DDGS().text(q, max_results=limit))
        return [
            CandidateVideo(
                platform=self.platform,
                platform_video_id=r.get("href", ""),
                url=r.get("href", ""),
                creator_handle="",
                title=r.get("title", ""),
                description=r.get("body", ""),
                views=None,
                likes=None,
                comments=None,
                shares=None,
                saves=None,
                raw=dict(r),
            )
            for r in results
        ]
