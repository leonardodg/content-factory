import hashlib

import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from content_factory.domain.errors import CollectorError, NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.models import SegmentConfig
from content_factory.domain.web_queries import PlannedQuery
from content_factory.domain.web_urls import canonical_url
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
async def test_discover_platform_video_id_is_sha1_da_url_canonica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F0: identidade = sha1(URL canônica); a URL original vai em `videos.url`."""
    monkeypatch.setattr(web_mod, "DDGS", lambda: StubDDGSLongUrl())
    out = await WebCollector().discover(SEG, limit=5)
    assert out
    assert out[0].url == StubDDGSLongUrl.LONG_URL  # URL completa preservada
    expected = hashlib.sha1(canonical_url(StubDDGSLongUrl.LONG_URL).encode()).hexdigest()
    assert out[0].platform_video_id == expected
    assert out[0].raw["canonical_url"] == canonical_url(StubDDGSLongUrl.LONG_URL)
    assert out[0].raw["web_status"] == "kept"
    assert len(out[0].platform_video_id) <= 64


class StubDDGSAds:
    """Resultado com clique de anúncio do Bing + marketplace legítimo."""

    def text(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        _ = query, max_results
        return [
            {"title": "Anúncio", "href": "https://www.bing.com/aclick?ld=1", "body": "ad"},
            {"title": "Kit de resina", "href": "https://loja.ex.com/kit?utm_source=x", "body": "x"},
        ]


@pytest.mark.asyncio
async def test_discover_keeps_ad_trail_and_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: StubDDGSAds())
    out = await WebCollector().discover(SEG, limit=5)
    assert len(out) == 2
    ad, good = out
    assert ad.raw["web_status"] == "discarded_ad"
    assert ad.raw["discard_reason"] == "ad:bing_aclick"
    assert good.raw["web_status"] == "kept"
    assert good.raw["provider"] == "unknown"
    assert good.raw["provider_result_position"] == 2
    assert good.raw["rules_version"] == "ads-v1"


class StubDDGSDuplicates:
    def text(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        _ = query, max_results
        return [
            {"title": "A", "href": "https://ex.com/p?utm_source=a", "body": "b"},
            {"title": "A de novo", "href": "https://ex.com/p?utm_source=b", "body": "b"},
        ]


@pytest.mark.asyncio
async def test_discover_marks_duplicate_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: StubDDGSDuplicates())
    out = await WebCollector().discover(SEG, limit=5)
    statuses = [c.raw["web_status"] for c in out]
    assert statuses == ["kept", "duplicate"]


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
async def test_empty_result_logs_ambiguity_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Auditoria externa 4.3: vazio do ddgs é ambíguo e fica visível no log.

    O `ddgs` devolve `None` para status != 200 sem exceção (base.py) e só então
    levanta "No results found": vazio legítimo e engine bloqueada (403/429)
    não se distinguem. A run segue `ok`/`found=0`, mas com WARNING.
    """
    import logging

    monkeypatch.setattr(web_mod, "DDGS", lambda: BoomDDGS(DDGSException("No results found.")))
    with caplog.at_level(logging.WARNING, logger=web_mod.__name__):
        assert await WebCollector().discover(SEG, limit=5) == []
    assert any("ambígua" in r.getMessage() for r in caplog.records)


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


# --- modo obrigatório (WEB_QUERIES_REQUIRED): falha explícita sem protocolo ---


class _RecordingDDGS:
    """Stub de DDGS que registra as consultas efetivamente pedidas."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def text(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        self.queries.append(query)
        return []


class _StubQueries:
    """`WebQuerySource` que devolve o que o teste mandar."""

    def __init__(self, planned: list[PlannedQuery] | None) -> None:
        self._planned = planned

    def queries_for(self, segment: SegmentConfig) -> list[PlannedQuery] | None:
        return self._planned


@pytest.mark.asyncio
async def test_without_protocol_falls_back_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _RecordingDDGS()
    monkeypatch.setattr(web_mod, "DDGS", lambda: rec)
    await WebCollector(queries=_StubQueries(None)).discover(SEG, limit=5)
    assert rec.queries == [" OR ".join(SEG.keywords[:3])]


@pytest.mark.asyncio
async def test_required_without_protocol_raises_and_skips_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _RecordingDDGS()
    monkeypatch.setattr(web_mod, "DDGS", lambda: rec)
    with pytest.raises(CollectorError) as exc_info:
        await WebCollector(queries=_StubQueries(None), required=True).discover(SEG, limit=5)
    assert exc_info.value.code == "missing_protocol"
    assert exc_info.value.platform == "web"
    assert rec.queries == []  # o legado NAO roda


@pytest.mark.asyncio
async def test_required_with_protocol_uses_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _RecordingDDGS()
    monkeypatch.setattr(web_mod, "DDGS", lambda: rec)
    planned = [PlannedQuery("q1:aprender:00", "como fazer velas", "aprender")]
    await WebCollector(queries=_StubQueries(planned), required=True).discover(SEG, limit=5)
    assert rec.queries == ["como fazer velas"]


def test_web_queries_required_defaults_off() -> None:
    from content_factory.config import Settings

    assert Settings().web_queries_required is False
