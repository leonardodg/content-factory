import httpx
import pytest

from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.youtube import YoutubeCollector

SEG = SegmentConfig(
    slug="candles", name="Velas", platforms=["youtube"], keywords=["candle making", "vela de soja"]
)


@pytest.mark.asyncio
async def test_discover_maps_fixture(httpx_mock_transport: httpx.MockTransport) -> None:
    c = YoutubeCollector(api_key="k", transport=httpx_mock_transport)
    out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "youtube"
    assert out[0].views is not None and out[0].url.startswith("https://")
    assert out[0].duration_s == 492  # PT8M12S
    assert out[0].creator_followers == 54000
    assert c.quota_used == 102  # 100 search + 1 videos + 1 channels


@pytest.mark.asyncio
async def test_discover_by_keywords_builds_query_and_returns_videos() -> None:
    """discover_by_keywords monta query a partir de keywords+hashtags e usa o MESMO fluxo."""
    captured: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        from pathlib import Path

        fixtures = Path(__file__).parents[2] / "fixtures"
        url = str(request.url)
        if "/search" in url:
            captured.append(request.url.params.get("q", ""))
            return httpx.Response(200, content=(fixtures / "youtube_search.json").read_bytes())
        if "/videos" in url:
            return httpx.Response(200, content=(fixtures / "youtube_videos.json").read_bytes())
        if "/channels" in url:
            return httpx.Response(200, content=(fixtures / "youtube_channels.json").read_bytes())
        return httpx.Response(404, json={})

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_route))
    out = await c.discover_by_keywords(
        keywords=["candle making", "DIY candle"], hashtags=["#velas", "#soy"], depth=1
    )
    assert out and out[0].platform == "youtube"
    assert out[0].duration_s == 492
    assert c.quota_used == 102
    # query = keywords+hashtags unidos (máx 5 termos)
    assert len(captured) == 1
    assert "candle making" in captured[0]
    assert "DIY candle" in captured[0]
    assert "#velas" in captured[0]


@pytest.mark.asyncio
async def test_quota_403_becomes_rate_limited() -> None:
    from content_factory.domain.errors import RateLimited

    def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "quota"}})

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_boom))
    with pytest.raises(RateLimited):
        await c.discover(SEG, limit=5)


def test_register_defaults_registers_youtube() -> None:
    from content_factory.config import Settings
    from content_factory.infra.collectors import register_defaults, registry

    register_defaults(Settings(youtube_api_key="k"))
    assert isinstance(registry.get("youtube"), YoutubeCollector)
