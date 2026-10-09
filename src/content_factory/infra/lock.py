"""Lock distribuído de execução única (Redis `SET NX PX` + `DEL`)."""

from content_factory.infra.redis_ops import RedisOps

# lock órfão (worker morto no meio da coleta) não sobrevive 30 min
DEFAULT_TTL_S = 1800


class RedisLock:
    def __init__(self, client: RedisOps, *, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._client = client
        self._ttl_s = ttl_s

    async def acquire_once(self, key: str) -> bool:
        return bool(await self._client.set(key, "1", nx=True, px=self._ttl_s * 1000))

    async def release(self, key: str) -> None:
        # SEM release o lock eterno bloqueia a próxima execução — o TTL cobre
        # o caso de processo morrer antes daqui
        await self._client.delete(key)
