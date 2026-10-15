import re
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, cast

import httpx

from content_factory.app.call_counters import record_quota
from content_factory.domain.errors import AuthExpired, NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.models import CandidateVideo, SegmentConfig
from content_factory.infra.redact import redact

API = "https://www.googleapis.com/youtube/v3"
DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


# Motivos de erro que são credencial/configuração da chave — ação humana, não
# cota: viram auth_expired e NÃO abrem o breaker. A API manda o motivo em
# `error.errors[].reason` (legado: keyInvalid, accessNotConfigured…) ou só em
# `error.details[].reason` (atual: chave inválida = 400 "badRequest" +
# API_KEY_INVALID — revisão N-1/N-2, payload real de 2026-10-14).
_CREDENTIAL_REASONS = frozenset(
    {
        "keyInvalid",
        "keyExpired",
        "accessNotConfigured",
        "ipRefererBlocked",
        "API_KEY_INVALID",
        "API_KEY_EXPIRED",
        "SERVICE_DISABLED",
        "API_KEY_SERVICE_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED",
        "API_KEY_IP_ADDRESS_BLOCKED",
    }
)


def _reasons_of(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return []
    out: list[str] = []
    for entry in cast(list[Any], entries):
        if isinstance(entry, dict):
            reason: Any = cast(dict[str, Any], entry).get("reason")
            if reason:
                out.append(str(reason))
    return out


def _error_reasons(response: httpx.Response) -> list[str]:
    """`reason` de `error.errors[]` e `error.details[]`, se o corpo for legível."""
    try:
        payload: Any = response.json()
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    error: Any = cast(dict[str, Any], payload).get("error")
    if not isinstance(error, dict):
        return []
    err = cast(dict[str, Any], error)
    return [*_reasons_of(err.get("errors")), *_reasons_of(err.get("details"))]


def _duration_seconds(iso: str) -> int | None:
    m = DURATION_RE.fullmatch(iso)
    if not m:
        return None
    h, mn, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mn * 60 + s


def _video_id(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    entry = cast(dict[str, Any], item)
    ident = entry.get("id")
    if not isinstance(ident, dict):
        return ""
    ident_data = cast(dict[str, Any], ident)
    value = ident_data.get("videoId")
    return str(value) if value else ""


class YoutubeCollector:
    platform: ClassVar[str] = "youtube"
    COST_SEARCH, COST_LIST = 100, 1

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self._client = httpx.AsyncClient(transport=transport, timeout=20)

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self._key:
            # auditoria A-3: credencial ausente não é cota — falha antes da
            # chamada (mesmo padrão do TikTok), sem abrir o breaker
            raise AuthExpired(
                self.platform, "YOUTUBE_API_KEY ausente — ver docs/dev/credentials.md"
            )
        try:
            r = await self._client.get(f"{API}/{path}", params={**params, "key": self._key})
        except httpx.HTTPError as exc:
            # redact: a URL de erro carrega a API key na query (`key=`), como
            # o access_token no Instagram (revisão 2026-10-14 A2)
            raise NetworkError(self.platform, redact(str(exc))) from exc
        if r.status_code >= 400:
            # corpo só é decodificado no caminho de erro (sucesso decodifica 1x)
            credential = [x for x in _error_reasons(r) if x in _CREDENTIAL_REASONS]
            if credential:
                # auditoria A-3 / revisão N-1/N-2: chave reprovada é credencial
                raise AuthExpired(
                    self.platform,
                    f"{credential[0]} — YOUTUBE_API_KEY recusada (docs/dev/credentials.md)",
                )
            if r.status_code == 403:
                raise RateLimited(self.platform, "quota ou origem bloqueada")
            raise UpstreamChanged(self.platform, f"HTTP {r.status_code}")
        try:
            raw: Any = r.json()
        except ValueError as exc:
            raise UpstreamChanged(self.platform, "resposta não-JSON") from exc
        if not isinstance(raw, dict):
            raise UpstreamChanged(self.platform, "JSON com shape inesperado")
        return cast(dict[str, Any], raw)

    async def aclose(self) -> None:
        """Fecha o AsyncClient no shutdown do worker (auditoria externa 4.1).

        Idempotente: o httpx ignora o 2º `aclose` de um cliente já fechado.
        """
        await self._client.aclose()

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = " OR ".join(segment.keywords[:5])
        return await self._search(q, limit)

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        """Busca por keywords+hashtags dinâmicas — mesmo fluxo de discover()."""
        _ = depth  # profundidade é controle do FanoutGuard, não da API
        terms = [*keywords, *hashtags][:5]  # API não aceita query enorme
        q = " OR ".join(terms)
        return await self._search(q, limit=25)

    async def _search(self, q: str, limit: int) -> list[CandidateVideo]:
        after = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        search = await self._get(
            "search",
            {
                "part": "snippet",
                "q": q,
                "type": "video",
                "order": "viewCount",
                "publishedAfter": after,
                "maxResults": str(min(limit, 50)),
            },
        )
        record_quota(self.COST_SEARCH)  # por chamada → collect_runs.quota_used
        items: Any = search.get("items")
        if not isinstance(items, list):
            raise UpstreamChanged(self.platform, "search sem items")
        entries = cast(list[Any], items)
        ids: list[str] = []
        for entry in entries:
            video_id = _video_id(entry)
            if video_id:
                ids.append(video_id)
        if not ids:
            return []
        stats = await self._get(
            "videos",
            {
                "part": "statistics,snippet,contentDetails",
                "id": ",".join(ids[:50]),
            },
        )
        record_quota(self.COST_LIST)
        stats_by_id = {str(v["id"]): v for v in stats.get("items", [])}
        channels = await self._get(
            "channels",
            {
                "part": "statistics",
                "id": ",".join({v["snippet"]["channelId"] for v in stats_by_id.values()}),
            },
        )
        record_quota(self.COST_LIST)
        followers = {
            str(c["id"]): int(c["statistics"].get("subscriberCount", 0))
            for c in channels.get("items", [])
        }
        return [self._map(v, followers) for v in stats_by_id.values()]

    def _map(self, v: dict[str, Any], followers: dict[str, int]) -> CandidateVideo:
        sn, st = v["snippet"], v.get("statistics", {})
        vid = v["id"]
        return CandidateVideo(
            platform=self.platform,
            platform_video_id=vid,
            url=f"https://www.youtube.com/watch?v={vid}",
            creator_handle=sn.get("channelTitle", ""),
            creator_followers=followers.get(sn.get("channelId", "")),
            title=sn.get("title", ""),
            description=sn.get("description", ""),
            published_at=datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
            duration_s=_duration_seconds(v.get("contentDetails", {}).get("duration", "")),
            views=int(st.get("viewCount", 0)),
            likes=int(st.get("likeCount", 0)),
            comments=int(st.get("commentCount", 0)),
            shares=None,
            saves=None,  # a API não expõe (ADR-0003)
            thumbnail_url=sn.get("thumbnails", {}).get("default", {}).get("url"),
            raw=v,
        )
