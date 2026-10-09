import json
from datetime import datetime
from typing import Any, ClassVar, cast

import httpx

from content_factory.domain.errors import AuthExpired, NetworkError, UpstreamChanged
from content_factory.domain.models import CandidateVideo, SegmentConfig
from content_factory.infra.redact import redact

API = "https://graph.facebook.com/v21.0"
FIELDS = (
    "id,caption,like_count,comments_count,timestamp,media_type,permalink,thumbnail_url,username"
)


class InstagramCollector:
    platform: ClassVar[str] = "instagram"

    def __init__(
        self, access_token: str, ig_user_id: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._token = access_token
        self._user = ig_user_id
        self._client = httpx.AsyncClient(transport=transport, timeout=20)
        self.app_usage = 0

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            r = await self._client.get(
                f"{API}/{path}", params={**params, "access_token": self._token}
            )
        except httpx.HTTPError as exc:
            # redact: a URL de erro carrega access_token na query (spec §7)
            raise NetworkError(self.platform, redact(str(exc))) from exc
        usage = r.headers.get("X-App-Usage", "")
        if usage and "call_count" in usage:
            self.app_usage = int(json.loads(usage).get("call_count", 0))
        if r.status_code in (400, 401):
            raise AuthExpired(self.platform, f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise UpstreamChanged(self.platform, f"HTTP {r.status_code}")
        try:
            raw: Any = r.json()
        except ValueError as exc:
            raise UpstreamChanged(self.platform, "resposta não-JSON") from exc
        if not isinstance(raw, dict):
            raise UpstreamChanged(self.platform, "JSON inesperado")
        return cast(dict[str, Any], raw)

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = segment.keywords[0].replace("#", "") if segment.keywords else segment.slug
        search = await self._get(f"{self._user}/hashtags", {"user_search": q, "limit": "1"})
        rows: Any = search.get("data") or []
        if not rows:
            return []
        tag_id = str(rows[0]["id"])
        top = await self._get(
            f"{tag_id}/top_media", {"fields": FIELDS, "limit": str(min(limit, 25))}
        )
        items: Any = top.get("data") or []
        return [self._map(cast(dict[str, Any], item)) for item in items[:limit]]

    def _map(self, item: dict[str, Any]) -> CandidateVideo:
        return CandidateVideo(
            platform=self.platform,
            platform_video_id=item.get("id", ""),
            url=item.get("permalink", ""),
            creator_handle=item.get("username", ""),
            title="",
            description=item.get("caption") or "",
            published_at=datetime.fromisoformat(item["timestamp"])
            if item.get("timestamp")
            else None,
            views=None,  # Graph API não expõe views de terceiros (ADR-0003)
            likes=int(item.get("like_count", 0)),
            comments=int(item.get("comments_count", 0)),
            shares=None,
            saves=None,
            duration_s=None,  # Graph API não expõe duração (ADR-0003)
            thumbnail_url=item.get("thumbnail_url"),
            raw=item,
        )
