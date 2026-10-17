"""Web Suggest via Google Suggest — scraping não-oficial, ver ADR-0005."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any, ClassVar, cast

import httpx

from content_factory.domain.errors import NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.kw_rank import SuggestHit
from content_factory.infra.collectors.breaker import CooldownBreaker

PLATFORM = "web_suggest"
SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
DEFAULT_BREAKER_THRESHOLD = 5
# 3 ciclos do research (cron diário 07:00) = 72 h: o que "3 ciclos" valia
# com 1 worker por segmento; agora por tempo (Etapa 1 Task 10)
DEFAULT_BREAKER_COOLDOWN_S = 3 * 24 * 3600


class WebSuggestProvider:
    """KeywordExpander via Google Suggest — não-oficial, risco documentado em ADR-0005.

    Endpoint `suggestqueries.google.com/complete/search?client=firefox&q=...`
    devolve JSON: `["query", ["sug1", "sug2", ...]]`. Síncrono (httpx sync)
    — roda em `asyncio.to_thread`. Circuit breaker: M falhas → cooldown por
    tempo (`CooldownBreaker`).
    """

    platform: ClassVar[str] = PLATFORM

    def __init__(
        self,
        transport: Callable[[httpx.Request], httpx.Response] | None = None,
        *,
        breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown_s: float = DEFAULT_BREAKER_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport  # seam de teste; production usa rede real
        self._breaker = CooldownBreaker(
            PLATFORM, threshold=breaker_threshold, cooldown_s=breaker_cooldown_s, clock=clock
        )

    async def expand(self, seeds: list[str], depth: int) -> list[SuggestHit]:
        _ = depth  # a posição substitui o peso fixo 0,5 (R2); depth fica na porta
        self._breaker.check()
        if not seeds:
            return []
        try:
            hits = await asyncio.to_thread(self._fetch_all, seeds)
        except NetworkError, RateLimited, UpstreamChanged:
            self._breaker.record_failure()
            raise
        except Exception as exc:
            self._breaker.record_failure()
            raise self._classify(exc) from exc
        self._breaker.record_success()
        return hits

    def _fetch_all(self, seeds: list[str]) -> list[SuggestHit]:
        """Sugestões por semente, COM posição (1-based). Dedupe por (semente, termo)."""
        out: list[SuggestHit] = []
        for seed in seeds:
            seen: set[str] = set()
            for position, sug in enumerate(self._fetch_one(seed), start=1):
                key = sug.casefold().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(SuggestHit(term=sug, seed=seed, position=position))
        return out

    def _fetch_one(self, seed: str) -> list[str]:
        try:
            if self._transport is not None:
                request = httpx.Request("GET", SUGGEST_URL, params={"client": "firefox", "q": seed})
                response = self._transport(request)
            else:
                response = httpx.get(
                    SUGGEST_URL, params={"client": "firefox", "q": seed}, timeout=10
                )
        except httpx.HTTPError as exc:
            raise self._classify(exc) from exc
        if response.status_code == 429:
            raise RateLimited(PLATFORM, "429 Too Many Requests")
        if response.status_code >= 400:
            raise UpstreamChanged(PLATFORM, f"HTTP {response.status_code}")
        try:
            raw = cast("list[Any]", json.loads(response.content))
        except ValueError as exc:
            raise UpstreamChanged(PLATFORM, "resposta não-JSON") from exc
        # formato: ["query", ["sug1", "sug2", ...]]
        if len(raw) < 2 or not isinstance(raw[1], list):
            raise UpstreamChanged(PLATFORM, "JSON com shape inesperado")
        items = cast("list[str]", raw[1])
        return items

    def _classify(self, exc: Exception) -> Exception:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 429:
                return RateLimited(PLATFORM, "429 Too Many Requests")
            return UpstreamChanged(PLATFORM, f"HTTP {status}")
        if isinstance(exc, httpx.HTTPError | ConnectionError | TimeoutError | OSError):
            return NetworkError(PLATFORM, str(exc))
        return UpstreamChanged(PLATFORM, str(exc))
