"""TDD: TrendsGoogleProvider via pytrends com circuit breaker."""

from typing import Any

import pandas as pd  # type: ignore[reportMissingTypeStubs]
import pytest

from content_factory.domain.errors import CollectorError, NetworkError, RateLimited
from content_factory.infra.collectors.trends_google import TrendsGoogleProvider

PLATFORM = "google_trends"


def _fake_dataframe() -> pd.DataFrame:
    df = pd.DataFrame(
        {"candle making": [50, 60], "vela": [30, 40], "isPartial": [False, False]},
        index=pd.to_datetime(["2026-10-01", "2026-10-08"], utc=True),  # type: ignore[reportUnknownMemberType]
    )
    df.index.name = "date"
    return df


class _FakePytrends:
    """Stub do TrendReq — devolve DataFrame fixo."""

    def __init__(self, df: pd.DataFrame | None = None, fail: Exception | None = None) -> None:
        self._df = df if df is not None else _fake_dataframe()
        self._fail = fail
        self.build_payload_calls: list[dict[str, Any]] = []

    def build_payload(
        self, kw_list: list[str], timeframe: str = "", geo: str = "", **kw: Any
    ) -> None:
        self.build_payload_calls.append({"kw_list": kw_list, "timeframe": timeframe, "geo": geo})

    def interest_over_time(self) -> pd.DataFrame:
        if self._fail is not None:
            raise self._fail
        return self._df


def _provider(client: Any, **kw: Any) -> TrendsGoogleProvider:
    return TrendsGoogleProvider(geo="BR", client=client, **kw)


@pytest.mark.asyncio
async def test_interest_over_time_maps_dataframe_to_trend_points() -> None:
    client = _FakePytrends()
    p = _provider(client)
    out = await p.interest_over_time(["candle making", "vela"], "today 7-d")
    assert len(out) == 4  # 2 keywords x 2 periodos
    assert out[0].keyword == "candle making"
    assert out[0].interest == 50.0
    assert out[0].period_granularity == "week"
    # payload chamado com keywords + geo
    assert client.build_payload_calls[0]["kw_list"] == ["candle making", "vela"]
    assert client.build_payload_calls[0]["geo"] == "BR"


@pytest.mark.asyncio
async def test_network_error_maps_to_network_error() -> None:
    import requests

    client = _FakePytrends(fail=requests.ConnectionError("sem rede"))
    p = _provider(client)
    with pytest.raises(NetworkError) as ei:
        await p.interest_over_time(["a"], "today 7-d")
    assert ei.value.platform == PLATFORM


@pytest.mark.asyncio
async def test_rate_limit_maps_to_rate_limited() -> None:
    import requests

    client = _FakePytrends(fail=requests.HTTPError("429 Too Many Requests"))
    # força o status code 429 no response
    resp = type("R", (), {"status_code": 429})()
    client._fail = requests.HTTPError(response=resp)  # type: ignore[attr-defined]
    p = _provider(client)
    with pytest.raises(RateLimited) as ei:
        await p.interest_over_time(["a"], "today 7-d")
    assert ei.value.platform == PLATFORM


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_consecutive_failures() -> None:
    """M falhas seguidas → provider desativado por N ciclos (sem bater na rede)."""
    import requests

    failing = _FakePytrends(fail=requests.ConnectionError("x"))
    p = _provider(failing, breaker_threshold=3, breaker_cooldown_cycles=2)

    # 3 falhas seguidas → breaker abre
    for _ in range(3):
        with pytest.raises(NetworkError):
            await p.interest_over_time(["a"], "today 7-d")

    # breaker aberto: curto-circuito SEM chamar o cliente
    calls_before = len(failing.build_payload_calls)
    with pytest.raises(CollectorError) as ei:  # breaker aberto → circuit_open
        await p.interest_over_time(["a"], "today 7-d")
    assert ei.value.code == "circuit_open"
    assert len(failing.build_payload_calls) == calls_before  # não bateu na rede


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_success() -> None:
    """Sucesso zera o contador de falhas consecutivas."""
    flaky = _FakePytrends(fail=ConnectionError("x"))
    p = _provider(flaky, breaker_threshold=3)

    # 2 falhas seguidas (abaixo do threshold)
    for _ in range(2):
        with pytest.raises(NetworkError):
            await p.interest_over_time(["candle making"], "today 7-d")

    # sucesso → contador reseta
    flaky._fail = None  # type: ignore[attr-defined]
    out = await p.interest_over_time(["candle making"], "today 7-d")
    assert out  # DataFrame tem "candle making"

    # mais 2 falhas: breaker NÃO abre (foi resetado)
    flaky._fail = ConnectionError("x")  # type: ignore[attr-defined]
    for _ in range(2):
        with pytest.raises(NetworkError):
            await p.interest_over_time(["candle making"], "today 7-d")

    # 3ª falha agora abre (contou do zero)
    with pytest.raises(NetworkError):
        await p.interest_over_time(["candle making"], "today 7-d")

    with pytest.raises(CollectorError) as ei:
        await p.interest_over_time(["candle making"], "today 7-d")
    assert ei.value.code == "circuit_open"
