from pathlib import Path

import httpx
import pytest

from content_factory.domain.errors import AuthExpired, NetworkError, UpstreamChanged
from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.instagram import InstagramCollector

# fixtures moram em tests/fixtures (parents[2] de tests/infra/collectors)
FIXDIR = Path(__file__).parents[2] / "fixtures"
SEG = SegmentConfig(
    slug="candles", name="Velas", platforms=["instagram"], keywords=["vela artesanal"]
)


def _route(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    headers = {"X-App-Usage": '{"call_count":10,"total_cputime":5,"total_time":5}'}
    if "hashtags" in url:
        return httpx.Response(
            200, headers=headers, content=(FIXDIR / "ig_hashtag_search.json").read_bytes()
        )
    if "top_media" in url or "recent_media" in url:
        return httpx.Response(
            200, headers=headers, content=(FIXDIR / "ig_hashtag_top.json").read_bytes()
        )
    return httpx.Response(404, json={})


@pytest.mark.asyncio
async def test_discover_maps_fixture() -> None:
    c = InstagramCollector(access_token="t", ig_user_id="1", transport=httpx.MockTransport(_route))
    out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "instagram"
    assert out[0].views is None  # limitação documentada (ADR-0003)
    assert out[0].likes == 5400 and out[0].comments == 130
    assert out[0].url.startswith("https://www.instagram.com/p/")
    assert c.app_usage == 10


@pytest.mark.asyncio
async def test_401_becomes_auth_expired() -> None:
    def _unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid token"}})

    c = InstagramCollector(
        access_token="bad", ig_user_id="1", transport=httpx.MockTransport(_unauthorized)
    )
    with pytest.raises(AuthExpired):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_400_becomes_auth_expired() -> None:
    def _bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad token"}})

    c = InstagramCollector(access_token="t", ig_user_id="1", transport=httpx.MockTransport(_bad))
    with pytest.raises(AuthExpired):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_500_becomes_upstream_changed() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    c = InstagramCollector(access_token="t", ig_user_id="1", transport=httpx.MockTransport(_boom))
    with pytest.raises(UpstreamChanged):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_non_json_becomes_upstream_changed() -> None:
    """Corpo não-JSON nunca vaza como ValueError cru (use-case só classifica CollectorError)."""

    def _html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>erro de proxy</html>")

    c = InstagramCollector(access_token="t", ig_user_id="1", transport=httpx.MockTransport(_html))
    with pytest.raises(UpstreamChanged):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_empty_search_returns_no_candidates() -> None:
    def _empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    c = InstagramCollector(access_token="t", ig_user_id="1", transport=httpx.MockTransport(_empty))
    out = await c.discover(SEG, limit=5)
    assert out == []


@pytest.mark.asyncio
async def test_network_error_redacts_access_token() -> None:
    """Token em query string nunca aparece em mensagem de erro (spec §7)."""

    def _raise(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"falha ao conectar em {request.url}", request=request)

    # placeholder (PLACEHOLDER do guard) — o valor não importa, a prova é o redact
    c = InstagramCollector(
        access_token="YOUR_TOKEN_HERE", ig_user_id="1", transport=httpx.MockTransport(_raise)
    )
    with pytest.raises(NetworkError) as ei:
        await c.discover(SEG, limit=5)
    assert ei.value.code == "network"
    assert "YOUR_TOKEN_HERE" not in str(ei.value)


def test_register_defaults_registers_instagram() -> None:
    from content_factory.config import Settings
    from content_factory.infra.collectors import register_defaults, registry

    register_defaults(Settings(instagram_access_token="t", ig_user_id="1"))
    assert isinstance(registry.get("instagram"), InstagramCollector)
