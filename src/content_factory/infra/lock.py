"""Lock distribuído de execução única (`SET NX PX` + `EVAL` com CAS).

Cada `acquire_once` gera um token de posse (`secrets.token_hex`) gravado como
valor do lock; `release` só apaga quando o valor ainda é o token — via script
Lua atômico. Sem isso, se o TTL do dono A expirar e B readquirir o mesmo lock,
o `release` tardio de A apagaria o lock de B e dois processos coletariam o
mesmo segmento. `GET` + `DEL` separados não são atômicos — por isso o Lua.
"""

import secrets

from content_factory.infra.redis_ops import RedisOps

# lock órfão (worker morto no meio da coleta) não sobrevive 30 min
DEFAULT_TTL_S = 1800

# apaga KEYS[1] só se o valor for ARGV[1] (o token do dono); devolve 1/0
_RELEASE_IF_OWNER = (
    'if redis.call("GET", KEYS[1]) == ARGV[1] then '
    'return redis.call("DEL", KEYS[1]) else return 0 end'
)


class RedisLock:
    def __init__(self, client: RedisOps, *, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._client = client
        self._ttl_s = ttl_s
        self._tokens: dict[str, str] = {}

    async def acquire_once(self, key: str) -> bool:
        token = secrets.token_hex(16)
        acquired = bool(await self._client.set(key, token, nx=True, px=self._ttl_s * 1000))
        if acquired:
            self._tokens[key] = token
        return acquired

    async def release(self, key: str) -> None:
        # SEM release o lock eterno bloqueia a próxima execução — o TTL cobre
        # o caso de processo morrer antes daqui
        token = self._tokens.pop(key, None)
        if token is None:
            return  # sem posse (nunca adquiriu ou TTL expirou p/ outro dono): no-op
        await self._client.eval(_RELEASE_IF_OWNER, 1, key, token)
