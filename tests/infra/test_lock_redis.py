"""Lock contra Redis real — o script Lua nunca roda na suíte com FakeRedis."""

import asyncio
import os
import uuid

import pytest

from content_factory.domain.errors import CollectorError
from content_factory.infra.lock import RedisLock
from content_factory.infra.redis_ops import redis_client
from tests.conftest import requires_redis

pytestmark = pytest.mark.integration


def _key() -> str:
    return f"cf:test:lock:{uuid.uuid4()}"


@requires_redis
async def test_lua_release_by_owner_deletes() -> None:
    key = _key()
    url = os.environ["TEST_REDIS_URL"]
    async with redis_client(url) as client:
        lock = RedisLock(client)
        assert await lock.acquire_once(key) is True
        await lock.release(key)
        probe = RedisLock(client)
        assert await probe.acquire_once(key) is True
        await probe.release(key)


@requires_redis
async def test_stale_owner_two_bundles_sharing_client_keeps_new_owner() -> None:
    """Cenário real pós-fix: dois bundles, uma instância de lock cada, mesmo cliente.

    TTL de A expira, B readquire; o release tardio de A é no-op e o lock de
    B sobrevive — o token mora na instância, não no cliente. (Antes do fix,
    os bundles herdavam UMA instância do template e este cenário apagava o
    lock do novo dono; a mesma instância agora levanta `RuntimeError` em
    reaquisição — ver `test_lock_same_instance_reacquire_raises_until_released`.)
    """
    key = _key()
    url = os.environ["TEST_REDIS_URL"]
    async with redis_client(url) as client:
        try:
            bundle_a_lock = RedisLock(client, ttl_s=1)
            bundle_b_lock = RedisLock(client, ttl_s=1)
            assert await bundle_a_lock.acquire_once(key) is True
            await asyncio.sleep(1.3)
            assert await bundle_b_lock.acquire_once(key) is True
            await bundle_a_lock.release(key)
            probe = RedisLock(client)
            assert await probe.acquire_once(key) is False
        finally:
            await client.delete(key)


@requires_redis
async def test_platform_breaker_opens_across_segments_on_real_redis() -> None:
    """Breaker por plataforma ligado: 3 falhas de discover (segmentos diferentes,
    mesmo Redis) → o próximo acquire da plataforma levanta `blocked`."""
    from content_factory.app.collect import CollectDeps, collect
    from content_factory.domain.errors import NetworkError
    from content_factory.infra.ratelimit import RedisRateLimiter
    from tests.stubs import FakeCollector, FakeLock, FakeRepos

    platform = f"plat-{uuid.uuid4().hex[:8]}"
    url = os.environ["TEST_REDIS_URL"]
    async with redis_client(url) as client:
        limiter = RedisRateLimiter(client)
        try:
            for slug in ("a", "b", "c"):
                repos = FakeRepos()
                deps = CollectDeps(
                    collectors=[FakeCollector(platform, fail=NetworkError(platform))],
                    videos=repos.videos,
                    segments=repos.segments,
                    runs=repos.runs,
                    limiter=limiter,
                    lock=FakeLock(),
                    health=limiter,
                )
                await collect(slug, [platform], 5, deps)
            with pytest.raises(CollectorError) as exc_info:
                await limiter.acquire(platform)
            assert exc_info.value.code == "blocked"
        finally:
            await client.delete(f"cf:cb:{platform}:fails", f"cf:cb:{platform}:blocked")
            await client.delete(f"cf:rl:{platform}")
