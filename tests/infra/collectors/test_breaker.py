"""Etapa 1 Task 10: breaker por TEMPO (não por chamada) para web_suggest/trends."""

import asyncio

import pytest

from content_factory.domain.errors import CollectorError
from content_factory.infra.collectors.breaker import CooldownBreaker


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_opens_after_threshold_and_closes_after_cooldown() -> None:
    clock = _Clock()
    breaker = CooldownBreaker("p", threshold=3, cooldown_s=60, clock=clock)
    for _ in range(3):
        breaker.check()
        breaker.record_failure()
    with pytest.raises(CollectorError) as exc_info:
        breaker.check()
    assert exc_info.value.code == "circuit_open"
    clock.now += 59.9
    with pytest.raises(CollectorError):
        breaker.check()
    clock.now += 0.2
    breaker.check()  # cooldown venceu: fechado de novo


def test_checks_while_open_do_not_consume_cooldown() -> None:
    """Antes: cada chamada decrementava 1 "ciclo" — 3 segmentos por tick
    gastavam o cooldown 3x mais rápido. Por tempo, N checks não mudam nada."""
    clock = _Clock()
    breaker = CooldownBreaker("p", threshold=1, cooldown_s=60, clock=clock)
    breaker.record_failure()
    for _ in range(100):
        with pytest.raises(CollectorError):
            breaker.check()
    clock.now += 30
    with pytest.raises(CollectorError):
        breaker.check()


def test_success_resets_consecutive_failures() -> None:
    clock = _Clock()
    breaker = CooldownBreaker("p", threshold=3, cooldown_s=60, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.check()  # só 1 falha consecutiva: fechado


async def test_web_suggest_concurrent_calls_do_not_consume_cooldown() -> None:
    from content_factory.infra.collectors.web_suggest import WebSuggestProvider

    clock = _Clock()
    provider = WebSuggestProvider(
        transport=lambda _r: (_ for _ in ()).throw(AssertionError("não devia bater na rede")),
        breaker_threshold=1,
        breaker_cooldown_s=60,
        clock=clock,
    )
    provider._breaker.record_failure()  # pyright: ignore[reportPrivateUsage]
    results = await asyncio.gather(
        *(provider.expand(["vela"], depth=0) for _ in range(3)), return_exceptions=True
    )
    assert all(isinstance(r, CollectorError) and r.code == "circuit_open" for r in results)
    clock.now += 30
    with pytest.raises(CollectorError):
        await provider.expand(["vela"], depth=0)


async def test_trends_reopens_for_calls_after_cooldown() -> None:
    from content_factory.infra.collectors.trends_google import TrendsGoogleProvider

    clock = _Clock()
    provider = TrendsGoogleProvider(
        client=object(), breaker_threshold=1, breaker_cooldown_s=60, clock=clock
    )
    provider._breaker.record_failure()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(CollectorError):
        await provider.interest_over_time(["a"], "today 7-d")
    clock.now += 61
    assert await provider.interest_over_time([], "today 7-d") == []  # fechado: segue o fluxo


def test_default_cooldown_is_three_research_days() -> None:
    """Default = 3 ciclos do research (cron diário 07:00) = 72 h — o que
    "3 ciclos" significava com 1 worker por segmento (1 chamada/dia)."""
    from content_factory.infra.collectors import trends_google, web_suggest

    assert web_suggest.DEFAULT_BREAKER_COOLDOWN_S == 3 * 24 * 3600
    assert trends_google.DEFAULT_BREAKER_COOLDOWN_S == 3 * 24 * 3600
