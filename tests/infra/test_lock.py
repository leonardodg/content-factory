from content_factory.infra.lock import RedisLock
from tests.stubs import FakeRedis


async def test_lock_is_exclusive_until_released() -> None:
    client = FakeRedis()
    lock = RedisLock(client)
    assert await lock.acquire_once("collect:candles") is True
    assert await lock.acquire_once("collect:candles") is False
    await lock.release("collect:candles")
    assert await lock.acquire_once("collect:candles") is True


async def test_lock_has_expiry_so_orphan_dies() -> None:
    client = FakeRedis()
    lock = RedisLock(client, ttl_s=1800)
    await lock.acquire_once("collect:candles")
    assert client.expires["collect:candles"] == 1800
