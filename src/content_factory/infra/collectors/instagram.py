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

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
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
            if self._is_public_content_gate(r):
                # gate #10: App Review pendente — caller decide fallback (não é token)
                return None
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

    @staticmethod
    def _is_public_content_gate(r: httpx.Response) -> bool:
        """Erro (#10) Instagram Public Content Access = feature não revisada, não token."""
        try:
            raw = r.json()
        except ValueError:
            return False
        if not isinstance(raw, dict):
            return False
        err = cast(dict[str, Any], raw).get("error")
        if not isinstance(err, dict):
            return False
        error = cast(dict[str, Any], err)
        code = error.get("code")
        message = str(error.get("message", ""))
        return code == 10 or "(#10)" in message

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = segment.keywords[0].replace("#", "") if segment.keywords else segment.slug
        search = await self._get("ig_hashtag_search", {"user_id": self._user, "q": q})
        if search is None:
            return await self._own_media(segment, limit)
        rows: Any = search.get("data") or []
        if not rows:
            return []
        tag_id = str(rows[0]["id"])
        top = await self._get(
            f"{tag_id}/top_media",
            {"user_id": self._user, "fields": FIELDS, "limit": str(min(limit, 25))},
        )
        if top is None:
            return await self._own_media(segment, limit)
        items: Any = top.get("data") or []
        return [self._map(cast(dict[str, Any], item)) for item in items[:limit]]

    async def _own_media(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        """Fallback no gate #10: mídia pública da própria conta, filtrada por keyword.

        A conta publica conteúdo misto (velas, sabonetes, macramê); sem filtro a
        relevância do segmento seria violada. Busca vazia de hashtag não cai aqui.
        """
        rows = await self._get(f"{self._user}/media", {"fields": FIELDS, "limit": "50"})
        if not rows:
            return []
        keywords = [k.casefold() for k in segment.keywords]
        items: Any = rows.get("data") or []
        out: list[CandidateVideo] = []
        for raw_item in items:
            item = cast(dict[str, Any], raw_item)
            caption = str(item.get("caption") or "").casefold()
            if not keywords or any(k in caption for k in keywords):
                out.append(self._map(item))
            if len(out) >= limit:
                break
        return out

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
