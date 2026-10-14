import hashlib

import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from content_factory.domain.errors import NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors import web as web_mod
from content_factory.infra.collectors.web import WebCollector

SEG = SegmentConfig(slug="candles", name="Velas", platforms=["web"], keywords=["vela artesanal"])


class StubDDGS:
    def text(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        return [
            {
                "title": "Tutorial de velas",
                "href": "https://ex.com/vela",
                "body": "aprenda a fazer velas de soja",
            }
        ]


@pytest.mark.asyncio
async def test_discover_maps_ddgs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: StubDDGS())
    out = await WebCollector().discover(SEG, limit=5)
    assert out and out[0].platform == "web"
    assert out[0].title == "Tutorial de velas"
    assert out[0].url == "https://ex.com/vela"
    assert out[0].views is None


class StubDDGSLongUrl:
    """URL real de busca (>64 chars) — contrato do banco: platform_video_id ≤ 64."""

    LONG_URL = (
        "https://www.candlescience.com/learning/beginner-candle-making-with-soy-wax/"
        "?utm_source=e2e&utm_campaign=playtest-task-18"
    )

    def text(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        _ = query, max_results
        return [
            {
                "title": "Beginner candle making",
                "href": self.LONG_URL,
                "body": "soy wax guide",
            }
        ]


@pytest.mark.asyncio
async def test_discover_platform_video_id_is_sha1_do_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug E2E T18: URL cheia estourava VARCHAR(64) e abortava o collect inteiro."""
    monkeypatch.setattr(web_mod, "DDGS", lambda: StubDDGSLongUrl())
    out = await WebCollector().discover(SEG, limit=5)
    assert out
    assert out[0].url == StubDDGSLongUrl.LONG_URL  # URL completa preservada
    assert out[0].platform_video_id == hashlib.sha1(StubDDGSLongUrl.LONG_URL.encode()).hexdigest()
    assert len(out[0].platform_video_id) <= 64


class BoomDDGS:
    """DDGS que levanta na chamada de rede (construção ok, como o real)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def text(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        _ = query, max_results
        raise self._exc


@pytest.mark.asyncio
async def test_discover_no_results_found_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Busca vazia não é erro: produção 2026-10-13 abortou o segmento inteiro."""
    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(DDGSException("No results found")))
    assert await WebCollector().discover(SEG, limit=5) == []


@pytest.mark.asyncio
async def test_discover_ratelimit_maps_to_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(RatelimitException("202 Ratelimit")))
    with pytest.raises(RateLimited) as exc_info:
        await WebCollector().discover(SEG, limit=5)
    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.platform == "web"


@pytest.mark.asyncio
async def test_discover_timeout_maps_to_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(TimeoutException("timeout")))
    with pytest.raises(NetworkError) as exc_info:
        await WebCollector().discover(SEG, limit=5)
    assert exc_info.value.code == "network"
    assert exc_info.value.platform == "web"


@pytest.mark.asyncio
async def test_discover_other_ddgs_error_maps_to_upstream_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(DDGSException("kaput")))
    with pytest.raises(UpstreamChanged) as exc_info:
        await WebCollector().discover(SEG, limit=5)
    assert exc_info.value.code == "upstream_changed"
    assert exc_info.value.platform == "web"


def test_register_defaults_registers_web() -> None:
    from content_factory.config import Settings
    from content_factory.infra.collectors import register_defaults, registry

    register_defaults(Settings())
    assert isinstance(registry.get("web"), WebCollector)


@pytest.mark.asyncio
async def test_discover_wrapped_ratelimit_maps_to_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review: `DDGS.text` reembrulha o erro da engine em `DDGSException(err)`."""
    wrapped = DDGSException(RatelimitException("https://duckduckgo.com 202 Ratelimit"))
    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(wrapped))
    with pytest.raises(RateLimited):
        await WebCollector().discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_discover_wrapped_timeout_maps_to_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapped = DDGSException(TimeoutException("engine timeout"))
    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(wrapped))
    with pytest.raises(NetworkError):
        await WebCollector().discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_discover_wrapped_http_429_maps_to_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erro de engine não-ddgs (ex.: HTTP 429 do cliente) chega só como texto."""
    wrapped = DDGSException(RuntimeError("HTTP status 429 Too Many Requests"))
    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(wrapped))
    with pytest.raises(RateLimited):
        await WebCollector().discover(SEG, limit=5)
