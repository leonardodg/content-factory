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
