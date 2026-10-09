import pytest

from content_factory.domain.errors import CollectorError, RateLimited
from content_factory.infra.ratelimit import (
    DEFAULT_WINDOWS,
    RedisRateLimiter,
    Window,
    backoff_delay,
)
from tests.stubs import FakeRedis


def test_default_windows_follow_spec() -> None:
    youtube = DEFAULT_WINDOWS["youtube"]
    assert youtube.period_s == 86_400  # cota diária: INCR + EXPIRE 86400
    tiktok = DEFAULT_WINDOWS["tiktok"]
    assert tiktok.limit == 6 and tiktok.period_s == 60  # 6/min
    assert DEFAULT_WINDOWS["instagram"].period_s == 60  # janela de 60 s


async def test_window_sets_expiry_then_rate_limits_over_quota() -> None:
    client = FakeRedis()
    sleeps: list[float] = []

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = RedisRateLimiter(
        client, windows={"youtube": Window(limit=2, period_s=86_400)}, sleep=no_sleep
    )
    await limiter.acquire("youtube")
    assert client.expires["cf:rl:youtube"] == 86_400
    await limiter.acquire("youtube")
    with pytest.raises(RateLimited):
        await limiter.acquire("youtube")
    assert sleeps == [], "janela de 1 dia nao pode segurar o worker"
    assert client.counters["cf:rl:youtube"] == 2  # decr devolveu ao teto


async def test_waits_for_window_reset_then_proceeds() -> None:
    client = FakeRedis()
    delays: list[float] = []

    async def ticking(seconds: float) -> None:
        delays.append(seconds)
        client.advance(seconds)

    limiter = RedisRateLimiter(
        client, windows={"demo": Window(limit=1, period_s=10)}, sleep=ticking
    )
    await limiter.acquire("demo")
    await limiter.acquire("demo")  # estoura → espera a janela zerar → passa
    assert delays and all(0 < d <= 10 for d in delays)
    assert client.counters["cf:rl:demo"] == 1


async def test_gives_up_after_max_attempts() -> None:
    client = FakeRedis()
    calls: list[float] = []

    async def no_op(seconds: float) -> None:
        calls.append(seconds)

    limiter = RedisRateLimiter(client, windows={"demo": Window(limit=1, period_s=60)}, sleep=no_op)
    await limiter.acquire("demo")
    with pytest.raises(RateLimited):
        await limiter.acquire("demo")
    assert len(calls) == 5  # backoff exponencial limitado a 5 tentativas


def test_backoff_delay_jitters_within_20_percent() -> None:
    for attempt in range(4):
        base = float(2**attempt)  # base 1 s: 1, 2, 4, 8 s
        samples = [backoff_delay(attempt) for _ in range(50)]
        assert all(base * 0.8 <= s <= base * 1.2 for s in samples)


async def test_circuit_breaker_blocks_after_three_failures() -> None:
    client = FakeRedis()
    limiter = RedisRateLimiter(client)
    for _ in range(3):
        await limiter.record_failure("tiktok")
    assert client.expires["cf:cb:tiktok:blocked"] == 900  # 15 min
    with pytest.raises(CollectorError) as excinfo:
        await limiter.acquire("tiktok")
    assert excinfo.value.code == "blocked"
    assert "cf:rl:tiktok" not in client.counters  # nao incrementa janela (sem API)


async def test_success_resets_failure_counter() -> None:
    client = FakeRedis()
    limiter = RedisRateLimiter(client)
    await limiter.record_failure("tiktok")
    await limiter.record_failure("tiktok")
    await limiter.record_success("tiktok")
    await limiter.record_failure("tiktok")
    assert client.counters["cf:cb:tiktok:fails"] == 1
    assert "cf:cb:tiktok:blocked" not in client.values  # 1ª falha pós-sucesso nao bloqueia
