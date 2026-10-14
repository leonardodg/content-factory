import asyncio
import hashlib
from typing import ClassVar

# classe real: o re-export `ddgs.DDGS` é proxy lazy — parcialmente Unknown p/ pyright
from ddgs.ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from content_factory.domain.errors import (
    CollectorError,
    NetworkError,
    RateLimited,
    UpstreamChanged,
)
from content_factory.domain.models import CandidateVideo, SegmentConfig


class WebCollector:
    """Busca web (DuckDuckGo via `ddgs`, sem chave) → candidatos platform="web"."""

    platform: ClassVar[str] = "web"

    def __init__(self) -> None:
        pass  # resolve DDGS no momento do discover (monkeypatchável)

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = " OR ".join(segment.keywords[:3])
        # DDGS é síncrono (rede) — roda em thread; o lambda lê o global DDGS a
        # cada chamada, que é o que monkeypatch.setattr(web_mod, "DDGS", ...) troca.
        # Erros do ddgs viram o contrato de CollectorError (`_classify`);
        # "No results found" é busca vazia, não erro.
        try:
            results = await asyncio.to_thread(lambda: DDGS().text(q, max_results=limit))
        except DDGSException as exc:
            raise_or_empty = _classify(self.platform, exc)
            if raise_or_empty is None:
                return []
            raise raise_or_empty from exc
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


def _classify(platform: str, exc: DDGSException) -> CollectorError | None:
    """`DDGSException` → contrato; `None` = busca vazia.

    `DDGS.text` agrega as engines e levanta `DDGSException(err)` com o erro
    da ÚLTIMA engine como argumento (só "timed out" vira `TimeoutException`),
    então o tipo real pode estar em `exc.args[0]` — e um erro de cliente
    HTTP (429) chega só como texto.
    """
    inner = exc.args[0] if exc.args and isinstance(exc.args[0], BaseException) else exc
    text = str(exc).lower()
    if isinstance(inner, RatelimitException) or "ratelimit" in text or "429" in text:
        return RateLimited(platform, str(exc))
    if isinstance(inner, TimeoutException) or "timed out" in text or "timeout" in text:
        return NetworkError(platform, str(exc))
    if "no results found" in text:
        return None
    return UpstreamChanged(platform, str(exc))
