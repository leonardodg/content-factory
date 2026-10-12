"""Web Suggest via Google Suggest — scraping não-oficial, ver ADR-0005."""

import asyncio
import json
from collections.abc import Callable
from typing import Any, ClassVar, cast

import httpx

from content_factory.domain.errors import CollectorError, NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.models import KeywordSignal

PLATFORM = "web_suggest"
SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
DEFAULT_BREAKER_THRESHOLD = 5
DEFAULT_BREAKER_COOLDOWN = 3  # ciclos


class WebSuggestProvider:
    """KeywordExpander via Google Suggest — não-oficial, risco documentado em ADR-0005.

    Endpoint `suggestqueries.google.com/complete/search?client=firefox&q=...`
    devolve JSON: `["query", ["sug1", "sug2", ...]]`. Síncrono (httpx sync)
    — roda em `asyncio.to_thread`. Circuit breaker: M falhas → N ciclos.
    """

    platform: ClassVar[str] = PLATFORM

    def __init__(
        self,
        transport: Callable[[httpx.Request], httpx.Response] | None = None,
        *,
        breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown_cycles: int = DEFAULT_BREAKER_COOLDOWN,
    ) -> None:
        self._transport = transport  # seam de teste; production usa rede real
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown = breaker_cooldown_cycles
        self._consecutive_failures = 0
        self._cooldown_remaining = 0

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            raise CollectorError("circuit_open", PLATFORM, "provider em cooldown")
        if not seeds:
            return []
        try:
            suggestions = await asyncio.to_thread(self._fetch_all, seeds)
        except NetworkError, RateLimited, UpstreamChanged:
            self._register_failure()
            raise
        except Exception as exc:
            self._register_failure()
            raise self._classify(exc) from exc
        self._consecutive_failures = 0  # sucesso reseta o breaker
        return self._map(suggestions, depth)

    def _fetch_all(self, seeds: list[str]) -> list[str]:
        """Busca sugestões para cada seed; dedupe preserva ordem."""
        seen: set[str] = set()
        out: list[str] = []
        for seed in seeds:
            for sug in self._fetch_one(seed):
                key = sug.casefold().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(sug)
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

    def _register_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._cooldown_remaining = self._breaker_cooldown
            self._consecutive_failures = 0

    def _map(self, suggestions: list[str], depth: int) -> list[KeywordSignal]:
        return [
            KeywordSignal(keyword=str(sug), source=PLATFORM, weight=0.5, depth=depth + 1)
            for sug in suggestions
        ]
