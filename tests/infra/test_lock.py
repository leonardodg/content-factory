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


async def test_release_from_stale_owner_does_not_delete_new_owner() -> None:
    """TTL de A expira, B readquire; release de A não pode apagar o lock de B."""
    client = FakeRedis()
    owner_a = RedisLock(client, ttl_s=10)
    owner_b = RedisLock(client, ttl_s=1800)
    assert await owner_a.acquire_once("collect:velas") is True
    client.advance(11)  # TTL de A expira — lock órfão morre
    assert await owner_b.acquire_once("collect:velas") is True
    await owner_a.release("collect:velas")  # dono antigo: no-op
    probe = RedisLock(client)
    assert await probe.acquire_once("collect:velas") is False  # ainda é de B


async def test_release_by_owner_deletes() -> None:
    client = FakeRedis()
    lock = RedisLock(client)
    assert await lock.acquire_once("collect:velas") is True
    await lock.release("collect:velas")
    assert await lock.acquire_once("collect:velas") is True


async def test_release_without_ownership_is_noop() -> None:
    client = FakeRedis()
    holder = RedisLock(client)
    stranger = RedisLock(client)
    assert await holder.acquire_once("collect:velas") is True
    await stranger.release("collect:velas")  # nunca adquiriu: sem erro, sem efeito
    assert "eval" not in client.commands
    assert "delete" not in client.commands
    probe = RedisLock(client)
    assert await probe.acquire_once("collect:velas") is False  # holder ainda é dono
