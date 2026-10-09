import asyncio
import hashlib
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
                # id estável ≤ 64 chars (VARCHAR do banco); URL cheia vive em url/raw
                # (bug E2E T18: href real >64 chars abortava o upsert inteiro);
                # usedforsecurity=False: é fingerprint de id, não criptografia
                platform_video_id=hashlib.sha1(
                    r.get("href", "").encode(), usedforsecurity=False
                ).hexdigest(),
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
