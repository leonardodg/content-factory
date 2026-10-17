import asyncio
import random
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from content_factory.domain.errors import AuthExpired, CollectorError
from content_factory.domain.models import CandidateVideo, SegmentConfig

MAX_ATTEMPTS = 5


class _SearchEndpoint:
    """Adapta client.search p/ o contrato .videos(count=) do _call_with_backoff.

    TikTokApi v7: client.search.search_type(query, "item", count=N) → gerador
    async de Video. A v6 tinha `search.videos(query, count=)`, removido na v7
    — a busca por keyword quebrava com AttributeError (QA 17/10).
    """

    def __init__(self, client: Any, query: str) -> None:
        self._search = client.search
        self._query = query

    def videos(self, count: int = 30, **kw: object) -> Any:
        return self._search.search_type(self._query, "item", count=count)


def _raw(item: Any) -> dict[str, Any]:
    """`Video.as_dict` do TikTokApi v6 ou dict cru (contrato de teste)."""
    data = getattr(item, "as_dict", None)
    return cast(dict[str, Any], item) if data is None else cast(dict[str, Any], data)


async def _collect(stream: Any) -> list[dict[str, Any]]:
    """Normaliza a resposta do endpoint em `list[dict]`.

    O TikTokApi v6 devolve um gerador async de `Video` (canário do risco §Em aberto:
    consumir e converter via `as_dict`); o contrato de teste (stub) devolve um
    awaitable de lista de dicts.
    """
    if hasattr(stream, "__anext__"):
        out: list[dict[str, Any]] = []
        async for item in stream:
            out.append(_raw(item))
        return out
    items: list[dict[str, Any]] = await stream
    return items


class TikTokCollector:
    platform: ClassVar[str] = "tiktok"

    def __init__(self, ms_token: str, proxy: str | None = None, client: Any | None = None) -> None:
        self._token = ms_token
        self._proxy = proxy
        self._client = client

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        if not self._token:
            raise AuthExpired(
                self.platform, "TIKTOK_MS_TOKEN ausente/expirado — ver docs/dev/credentials.md"
            )
        if self._client is not None:
            return await self._discover(self._client, segment, limit)
        api = await self._open_session()
        try:
            return await self._discover(api, segment, limit)
        finally:
            # uso por-invocação: fecha só a sessão que abrimos (a lib só loga em
            # __del__); close_sessions já para o Playwright, stop_playwright
            # extra por segurança (padrão __aexit__ da própria lib)
            await api.close_sessions()
            await api.stop_playwright()

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        """Busca por keywords+hashtags dinâmicas — mesma sessão/backoff de discover()."""
        if not self._token:
            raise AuthExpired(
                self.platform, "TIKTOK_MS_TOKEN ausente/expirado — ver docs/dev/credentials.md"
            )
        _ = depth  # profundidade é controle do FanoutGuard, não da API
        terms = [*keywords, *hashtags][:5]  # API não aceita query enorme
        query = " ".join(terms)
        if self._client is not None:
            return await self._search(self._client, query)
        api = await self._open_session()
        try:
            return await self._search(api, query)
        finally:
            await api.close_sessions()
            await api.stop_playwright()

    async def _search(self, client: Any, query: str) -> list[CandidateVideo]:
        """Busca por keyword via client.search.videos — mesma sessão Playwright."""
        videos = await self._call_with_backoff(_SearchEndpoint(client, query), count=30)
        seen: set[str] = set()
        out: list[CandidateVideo] = []
        for v in videos:
            if v.get("id") not in seen:
                seen.add(v.get("id", ""))
                out.append(self._map(v))
        return out

    async def _discover(
        self, client: Any, segment: SegmentConfig, limit: int
    ) -> list[CandidateVideo]:
        videos = await self._call_with_backoff(client.trending, count=min(limit * 2, 60))
        if segment.keywords:
            videos += await self._call_with_backoff(
                client.hashtag(segment.keywords[0]), count=min(limit, 30)
            )
        seen: set[str] = set()
        out: list[CandidateVideo] = []
        for v in videos:
            if v.get("id") not in seen:
                seen.add(v.get("id", ""))
                out.append(self._map(v))
        return out[:limit]

    async def _call_with_backoff(self, endpoint: Any, count: int) -> list[dict[str, Any]]:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await _collect(endpoint.videos(count=count))
            except Exception as exc:  # fronteira classifica depois
                from TikTokApi.exceptions import (  # type: ignore[reportMissingTypeStubs]
                    EmptyResponseException,
                )

                if not isinstance(exc, EmptyResponseException):
                    raise
                if attempt == MAX_ATTEMPTS - 1:
                    raise CollectorError(
                        "blocked", self.platform, f"sem resposta após {MAX_ATTEMPTS} tentativas"
                    ) from exc
                # jitter de backoff não é uso criptográfico (B311 aceito aqui)
                await asyncio.sleep(2**attempt + random.uniform(0, 2))  # nosec B311
        return []

    async def _open_session(self) -> Any:
        from TikTokApi import TikTokApi  # type: ignore[reportMissingTypeStubs]

        # create_sessions gera UA/viewport reais (fingerprint coerente — spec §7),
        # sem forjar nada; TikTokApi v6 não aceita proxy no construtor — vai como
        # ProxySettings do Playwright em create_sessions
        api = TikTokApi()
        if self._proxy:
            await api.create_sessions(
                ms_tokens=[self._token],
                num_sessions=1,
                headless=True,
                proxies=[{"server": self._proxy}],
            )
        else:
            await api.create_sessions(ms_tokens=[self._token], num_sessions=1, headless=True)
        return api

    def _map(self, v: dict[str, Any]) -> CandidateVideo:
        author = v.get("author", {})
        stats = v.get("stats", {})
        handle = author.get("uniqueId", "")
        return CandidateVideo(
            platform=self.platform,
            platform_video_id=v.get("id", ""),
            url=f"https://www.tiktok.com/@{handle}/video/{v.get('id', '')}",
            creator_handle=handle,
            title="",
            description=v.get("desc", ""),
            published_at=datetime.fromtimestamp(int(v.get("createTime", 0)), tz=UTC),
            duration_s=int(v.get("duration") or 0) or None,
            views=int(stats.get("playCount", 0)),
            likes=int(stats.get("diggCount", 0)),
            comments=int(stats.get("commentCount", 0)),
            shares=int(stats.get("shareCount", 0)),
            saves=None,  # TikTok não expõe (ADR-0003)
            thumbnail_url=cast(dict[str, Any], v.get("video") or {}).get("cover"),
            raw=v,
        )
