"""TDD: WebSuggestProvider via Google Suggest (scraping não-oficial)."""

import json
from typing import Any

import httpx
import pytest

from content_factory.domain.errors import CollectorError, NetworkError, RateLimited
from content_factory.domain.kw_rank import SuggestHit

PLATFORM = "web_suggest"


def _suggest_response(suggestions: list[str]) -> httpx.Response:
    """Google Suggest devolve JSON: ["query", ["sug1", "sug2", ...]]."""
    return httpx.Response(200, content=json.dumps(["query", suggestions]).encode())


class _FakeTransport:
    """MockTransport que registra chamadas e devolve sugestões fixas."""

    def __init__(self, suggestions: list[str] | None = None, fail: Exception | None = None) -> None:
        self._suggestions = suggestions if suggestions is not None else ["vela soja"]
        self._fail = fail
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        self.requests.append(q)
        if self._fail is not None:
            raise self._fail
        return _suggest_response(self._suggestions)


def _provider(transport: _FakeTransport, **kw: Any) -> Any:
    from content_factory.infra.collectors.web_suggest import WebSuggestProvider

    return WebSuggestProvider(transport=transport.handler, **kw)


@pytest.mark.asyncio
async def test_expand_expoe_posicao_e_semente() -> None:
    t = _FakeTransport(suggestions=["a", "b"])
    p = _provider(t)
    hits = await p.expand(["s1"], depth=0)
    assert [(h.term, h.seed, h.position) for h in hits] == [
        ("a", "s1", 1),
        ("b", "s1", 2),
    ]
    assert all(isinstance(h, SuggestHit) for h in hits)
    # cada seed gera 1 chamada
    assert t.requests == ["s1"]


@pytest.mark.asyncio
async def test_expand_dedupe_por_semente_mas_nao_entre_elas() -> None:
    """Mesma sugestão de 2 seeds → 2 hits (sementes distintas) p/ a recorrência."""
    t = _FakeTransport(suggestions=["vela soja"])
    p = _provider(t)
    out = await p.expand(["vela", "cera"], depth=0)
    assert [(h.term, h.seed) for h in out] == [("vela soja", "vela"), ("vela soja", "cera")]


@pytest.mark.asyncio
async def test_expand_dedupe_dentro_da_semente() -> None:
    t = _FakeTransport(suggestions=["a", "a", "b"])
    p = _provider(t)
    out = await p.expand(["s1"], depth=0)
    assert [(h.term, h.position) for h in out] == [("a", 1), ("b", 3)]


@pytest.mark.asyncio
async def test_expand_network_error_maps_to_network_error() -> None:
    t = _FakeTransport(fail=httpx.ConnectError("sem rede"))
    p = _provider(t)
    with pytest.raises(NetworkError) as ei:
        await p.expand(["vela"], depth=0)
    assert ei.value.platform == PLATFORM


@pytest.mark.asyncio
async def test_expand_rate_limit_maps_to_rate_limited() -> None:
    t = _FakeTransport()

    # força 429
    def _rate_limited(request: httpx.Request) -> httpx.Response:
        t.requests.append(request.url.params.get("q", ""))
        return httpx.Response(429)

    t.handler = _rate_limited  # type: ignore[method-assign]
    p = _provider(t)
    with pytest.raises(RateLimited) as ei:
        await p.expand(["vela"], depth=0)
    assert ei.value.platform == PLATFORM


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_consecutive_failures() -> None:
    t = _FakeTransport(fail=httpx.ConnectError("x"))
    p = _provider(t, breaker_threshold=3, breaker_cooldown_s=60)

    for _ in range(3):
        with pytest.raises(NetworkError):
            await p.expand(["vela"], depth=0)

    # breaker aberto: curto-circuito sem bater na rede
    calls_before = len(t.requests)
    with pytest.raises(CollectorError) as ei:
        await p.expand(["vela"], depth=0)
    assert ei.value.code == "circuit_open"
    assert len(t.requests) == calls_before


@pytest.mark.asyncio
async def test_expand_empty_seeds_returns_empty() -> None:
    t = _FakeTransport()
    p = _provider(t)
    out = await p.expand([], depth=0)
    assert out == []
    assert t.requests == []  # não bate na rede
