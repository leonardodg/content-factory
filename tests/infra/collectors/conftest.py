from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parents[2] / "fixtures"


def _route(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/search" in url:
        return httpx.Response(200, content=(FIXTURES / "youtube_search.json").read_bytes())
    if "/videos" in url:
        return httpx.Response(200, content=(FIXTURES / "youtube_videos.json").read_bytes())
    if "/channels" in url:
        return httpx.Response(200, content=(FIXTURES / "youtube_channels.json").read_bytes())
    return httpx.Response(404, json={})


@pytest.fixture
def httpx_mock_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_route)
