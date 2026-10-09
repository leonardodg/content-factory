import httpx
import pytest

from content_factory.app.ingest import ingest_url
from content_factory.domain.errors import DomainError


def _no_guard(url: str) -> None:
    """Guarda SSRF desligada: a fronteira HTTP já é mockada nestes testes."""
    _ = url


@pytest.mark.asyncio
async def test_ingest_extracts_title(monkeypatch: pytest.MonkeyPatch) -> None:
    import content_factory.infra.ssrf as ssrf

    monkeypatch.setattr(ssrf, "assert_public_http", _no_guard)

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Minha vela</title>"
                "<meta name='description' content='feito a mao'></head></html>"
            ),
        )

    cand = await ingest_url("https://ex.com/vela", transport=httpx.MockTransport(_route))
    assert cand.title == "Minha vela"
    assert cand.platform == "web" and cand.url == "https://ex.com/vela"


@pytest.mark.asyncio
async def test_ingest_caps_body_at_2mb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Corpo além de 2 MB é cortado — meta description lá no fim não chega."""
    import content_factory.infra.ssrf as ssrf

    monkeypatch.setattr(ssrf, "assert_public_http", _no_guard)

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><head><title>ok</title></head><body>"
                + "x" * 2_100_000
                + "<meta name='description' content='depois do limite'></body></html>"
            ),
        )

    cand = await ingest_url("https://ex.com/big", transport=httpx.MockTransport(_route))
    assert cand.title == "ok"
    assert cand.description == ""


@pytest.mark.asyncio
async def test_ingest_extracts_meta_description(monkeypatch: pytest.MonkeyPatch) -> None:
    import content_factory.infra.ssrf as ssrf

    monkeypatch.setattr(ssrf, "assert_public_http", _no_guard)

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><head>"
                "<meta content='feito a mao' name='description'>"
                "<title>Outra vela</title></head></html>"
            ),
        )

    cand = await ingest_url("https://ex.com/vela2", transport=httpx.MockTransport(_route))
    assert cand.description == "feito a mao"
    assert cand.title == "Outra vela"


@pytest.mark.asyncio
async def test_ingest_blocks_private(monkeypatch: pytest.MonkeyPatch) -> None:
    # sem monkeypatch: URL real 127.0.0.1 → DomainError ANTES de fetch
    from content_factory.app.ingest import ingest_url as go

    with pytest.raises(DomainError):
        await go("http://127.0.0.1:9/x")
