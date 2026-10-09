"""Superfície mínima de Redis usada pela infra de coleta.

Protocol estreito de propósito: a implementação real (`redis.asyncio`) e os
doubles de teste (`tests.stubs.FakeRedis`) satisfazem a mesma forma — mock
só na fronteira (spec §10).

Métodos são `def ... -> Awaitable[T]` (não `async def`) porque o stub do
redis-py declara `-> ResponseT = Awaitable[Any] | Any`; `async def` aqui
quebraria o structural typing em pyright strict.
"""

from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from typing import Protocol


class RedisOps(Protocol):
    def incr(self, name: str, amount: int = 1) -> Awaitable[int]: ...

    def decr(self, name: str, amount: int = 1) -> Awaitable[int]: ...

    def expire(self, name: str, time: int) -> Awaitable[bool]: ...

    def ttl(self, name: str) -> Awaitable[int]: ...

    def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> Awaitable[bool | None]: ...

    def delete(self, *names: str) -> Awaitable[int]: ...


@asynccontextmanager
async def redis_client(url: str) -> AsyncGenerator[RedisOps]:
    """Fronteira única que conhece o cliente real — cria e fecha o pool."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(url)  # pyright: ignore[reportUnknownMemberType]
    try:
        yield client
    finally:
        await client.aclose()
