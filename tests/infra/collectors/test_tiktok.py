import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from content_factory.domain.errors import AuthExpired, CollectorError
from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.tiktok import TikTokCollector

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "tiktok_trending.json").read_text())
SEG = SegmentConfig(slug="candles", name="Velas", platforms=["tiktok"], keywords=["candle making"])


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff é política de espera, não comportamento asserido — dorme 0 (suite rápido)."""

    async def _noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop)


class StubTrending:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._p = payload

    async def videos(self, count: int = 30, **kw: object) -> list[dict[str, Any]]:
        return self._p


class StubHashtag:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._p = payload

    async def videos(self, count: int = 30, **kw: object) -> list[dict[str, Any]]:
        return self._p


class StubClient:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload
        self.trending = StubTrending(payload)
        self.search = StubSearch(payload)

    def hashtag(self, name: str) -> StubHashtag:
        return StubHashtag(self._payload)


class StubSearch:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    def videos(self, query: str, count: int = 30, **kw: object) -> Any:
        async def _run() -> list[dict[str, Any]]:
            return self._payload

        return _run()


@pytest.mark.asyncio
async def test_discover_maps_fixture() -> None:
    c = TikTokCollector(ms_token="tok", client=StubClient(FIXTURE))
    out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "tiktok"
    assert out[0].views == 250000 and out[0].likes == 18000
    assert out[0].url.endswith(FIXTURE[0]["id"])
    assert out[0].published_at is not None  # epoch → UTC


@pytest.mark.asyncio
async def test_discover_by_keywords_uses_search_endpoint() -> None:
    """discover_by_keywords usa client.search.videos (mesma sessão, mesmo backoff)."""
    queries: list[str] = []

    class SpySearch:
        def __init__(self, payload: list[dict[str, Any]]) -> None:
            self._payload = payload

        def videos(self, query: str, count: int = 30, **kw: object) -> Any:
            queries.append(query)

            async def _run() -> list[dict[str, Any]]:
                return self._payload

            return _run()

    class SpyClient:
        def __init__(self, payload: list[dict[str, Any]]) -> None:
            self._payload = payload
            self.trending = StubTrending(payload)
            self.search = SpySearch(payload)

        def hashtag(self, name: str) -> StubHashtag:
            return StubHashtag(self._payload)

    c = TikTokCollector(ms_token="tok", client=SpyClient(FIXTURE))
    out = await c.discover_by_keywords(
        keywords=["candle making", "DIY"], hashtags=["#velas"], depth=1
    )
    assert out and out[0].platform == "tiktok"
    assert out[0].views == 250000
    # query = keywords+hashtags unidos
    assert len(queries) == 1
    assert "candle making" in queries[0]
    assert "#velas" in queries[0]


@pytest.mark.asyncio
async def test_missing_token_raises_auth_expired() -> None:
    c = TikTokCollector(ms_token="", client=StubClient(FIXTURE))
    with pytest.raises(AuthExpired):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_empty_response_5x_becomes_blocked() -> None:
    from TikTokApi.exceptions import EmptyResponseException  # type: ignore[reportMissingTypeStubs]

    class BoomTrending:
        async def videos(self, **kw: object) -> list[dict[str, Any]]:
            raise EmptyResponseException("", "TikTok retornou resposta vazia")

    class BoomClient:
        trending = BoomTrending()

        def hashtag(self, name: str) -> BoomTrending:
            return BoomTrending()

    c = TikTokCollector(ms_token="tok", client=BoomClient())
    with pytest.raises(CollectorError) as ei:
        await c.discover(SEG, limit=5)
    assert ei.value.code == "blocked"


@pytest.mark.asyncio
async def test_discover_consumes_gerador_async_do_tiktokapi() -> None:
    """Canário do risco §Em aberto: TikTokApi v6 devolve gerador async de `Video`."""

    class FakeVideo:
        def __init__(self, data: dict[str, Any]) -> None:
            self.as_dict = data

    class GenEndpoint:
        def videos(self, count: int = 30, **kw: object) -> Any:
            async def _gen() -> AsyncIterator[FakeVideo]:
                for item in FIXTURE:
                    yield FakeVideo(item)

            return _gen()

    class GenClient:
        trending = GenEndpoint()

        def hashtag(self, name: str) -> GenEndpoint:
            return GenEndpoint()

    c = TikTokCollector(ms_token="tok", client=GenClient())
    out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "tiktok"
    assert out[0].views == 250000
    assert out[0].url.endswith(FIXTURE[0]["id"])


def test_register_defaults_registers_tiktok() -> None:
    from content_factory.config import Settings
    from content_factory.infra.collectors import register_defaults, registry

    register_defaults(Settings(tiktok_ms_token="tok"))
    assert isinstance(registry.get("tiktok"), TikTokCollector)


@pytest.mark.asyncio
async def test_sessao_aberta_fecha_mesmo_com_erro_no_meio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sessão Playwright aberta por invocação não vaza — fecha mesmo com erro no meio."""
    from TikTokApi.exceptions import EmptyResponseException  # type: ignore[reportMissingTypeStubs]

    class BoomEndpoint:
        async def videos(self, **kw: object) -> list[dict[str, Any]]:
            raise EmptyResponseException("", "TikTok retornou resposta vazia")

    class CountingApi:
        def __init__(self) -> None:
            self.closed = 0
            self.stopped = 0
            self.trending = BoomEndpoint()

        async def close_sessions(self) -> None:
            self.closed += 1

        async def stop_playwright(self) -> None:
            self.stopped += 1

    api = CountingApi()

    async def _fake_open(self: TikTokCollector) -> Any:
        return api

    monkeypatch.setattr(TikTokCollector, "_open_session", _fake_open)
    c = TikTokCollector(ms_token="tok")  # sem client injetado → sessão própria
    with pytest.raises(CollectorError) as ei:
        await c.discover(SEG, limit=5)
    assert ei.value.code == "blocked"
    assert api.closed == 1
    assert api.stopped == 1
