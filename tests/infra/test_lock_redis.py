"""Lock contra Redis real — o script Lua nunca roda na suíte com FakeRedis."""

import asyncio
import os
import uuid

import pytest

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
async def test_lua_stale_owner_distinct_instances_keeps_new_owner() -> None:
    key = _key()
    url = os.environ["TEST_REDIS_URL"]
    async with redis_client(url) as client:
        try:
            owner_a = RedisLock(client, ttl_s=1)
            owner_b = RedisLock(client)
            assert await owner_a.acquire_once(key) is True
            await asyncio.sleep(1.3)
            assert await owner_b.acquire_once(key) is True
            await owner_a.release(key)
            probe = RedisLock(client)
            assert await probe.acquire_once(key) is False
        finally:
            cleaner = RedisLock(client)
            await cleaner.release(key)
            await client.delete(key)


@requires_redis
async def test_stale_owner_same_instance_keeps_new_owner() -> None:
    """Mesma instância (topologia do worker): release tardio não apaga o novo dono."""
    key = _key()
    url = os.environ["TEST_REDIS_URL"]
    async with redis_client(url) as client:
        try:
            lock = RedisLock(client, ttl_s=1)
            assert await lock.acquire_once(key) is True
            await asyncio.sleep(1.3)
            assert await lock.acquire_once(key) is True
            await lock.release(key)
            probe = RedisLock(client)
            assert await probe.acquire_once(key) is False
        finally:
            await client.delete(key)
