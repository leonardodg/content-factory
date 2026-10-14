"""Rate limiter contra Redis real (revisão 2026-10-14, A1 — achado R2).

O FakeRedis reimplementa os scripts; a verdade da atomicidade está aqui.
Cada teste usa uma plataforma única e limpa as próprias chaves.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator

import pytest

from content_factory.domain.errors import CollectorError, RateLimited
from content_factory.infra.ratelimit import RedisRateLimiter, Window
from content_factory.infra.redis_ops import RedisOps, redis_client
from tests.conftest import requires_redis

pytestmark = pytest.mark.integration


async def _no_sleep(_s: float) -> None:
    await asyncio.sleep(0)


@pytest.fixture
async def redis() -> AsyncGenerator[RedisOps]:
    async with redis_client(os.environ["TEST_REDIS_URL"]) as client:
        yield client


@pytest.fixture
def platform() -> str:
    return f"plat-{uuid.uuid4().hex[:8]}"


async def _cleanup(redis: RedisOps, platform: str) -> None:
    await redis.delete(f"cf:rl:{platform}", f"cf:cb:{platform}:fails", f"cf:cb:{platform}:blocked")


# --- caracterização: comportamento que a correção PRESERVA ---


@requires_redis
async def test_window_admits_limit_then_rate_limits(redis: RedisOps, platform: str) -> None:
    limiter = RedisRateLimiter(
        redis, windows={platform: Window(limit=2, period_s=30)}, sleep=_no_sleep
    )
    try:
        await limiter.acquire(platform)
        await limiter.acquire(platform)
        with pytest.raises(RateLimited):
            await limiter.acquire(platform)
        assert 0 < await redis.ttl(f"cf:rl:{platform}") <= 30
    finally:
        await _cleanup(redis, platform)


@requires_redis
async def test_long_window_rate_limits_immediately(redis: RedisOps, platform: str) -> None:
    """Espera > 60 s (cota diária do YouTube) → RateLimited sem dormir."""
    slept: list[float] = []

    async def _record(s: float) -> None:
        slept.append(s)

    limiter = RedisRateLimiter(
        redis, windows={platform: Window(limit=1, period_s=86_400)}, sleep=_record
    )
    try:
        await limiter.acquire(platform)
        with pytest.raises(RateLimited):
            await limiter.acquire(platform)
        assert slept == []
        assert await redis.ttl(f"cf:rl:{platform}") > 86_000
    finally:
        await _cleanup(redis, platform)


@requires_redis
async def test_breaker_blocks_after_three_failures(redis: RedisOps, platform: str) -> None:
    limiter = RedisRateLimiter(redis, sleep=_no_sleep)
    try:
        for _ in range(3):
            await limiter.record_failure(platform)
        with pytest.raises(CollectorError) as exc_info:
            await limiter.acquire(platform)
        assert exc_info.value.code == "blocked"
    finally:
        await _cleanup(redis, platform)


@requires_redis
async def test_concurrent_clients_admit_exactly_the_limit(platform: str) -> None:
    """3 clientes, 20 acquires simultâneos, janela 10 → exatamente 10 passam."""
    url = os.environ["TEST_REDIS_URL"]
    async with redis_client(url) as a, redis_client(url) as b, redis_client(url) as c:
        limiters = [
            RedisRateLimiter(r, windows={platform: Window(limit=10, period_s=30)}, sleep=_no_sleep)
            for r in (a, b, c)
        ]
        try:
            results = await asyncio.gather(
                *(limiters[i % 3].acquire(platform) for i in range(20)), return_exceptions=True
            )
            assert sum(1 for r in results if r is None) == 10
            assert all(isinstance(r, RateLimited) for r in results if r is not None)
            assert 0 < await a.ttl(f"cf:rl:{platform}") <= 30
        finally:
            await _cleanup(a, platform)


# --- RED: chave sem TTL (crash entre INCR e EXPIRE) ---


@requires_redis
async def test_window_key_without_ttl_heals(redis: RedisOps, platform: str) -> None:
    """Antes: `ttl == -1` → `continue` 5x → RateLimited PARA SEMPRE."""
    key = f"cf:rl:{platform}"
    limiter = RedisRateLimiter(
        redis, windows={platform: Window(limit=5, period_s=30)}, sleep=_no_sleep
    )
    try:
        await redis.set(key, "5")  # janela cheia e sem TTL: o estado pós-crash
        with pytest.raises(RateLimited):
            await limiter.acquire(platform)
        assert 0 < await redis.ttl(key) <= 30  # vai expirar — não é mais permanente
    finally:
        await _cleanup(redis, platform)


@requires_redis
async def test_breaker_failures_key_without_ttl_heals(redis: RedisOps, platform: str) -> None:
    key = f"cf:cb:{platform}:fails"
    limiter = RedisRateLimiter(redis, sleep=_no_sleep)
    try:
        await redis.set(key, "1")  # contador órfão sem TTL
        await limiter.record_failure(platform)
        assert await redis.ttl(key) > 0
    finally:
        await _cleanup(redis, platform)
