"""Rate-limit por plataforma + circuit breaker no Redis (spec §7).

Janelas: YouTube cota diária (`INCR` + `EXPIRE 86400`), TikTok 6/min,
Instagram janela de 60 s. Janela estourada espera com backoff ±jitter
(máx. 5 tentativas) e, se a espera passaria de 60 s, `RateLimited` na hora.
Breaker: 3 falhas consecutivas → `blocked` por 15 min; `acquire` devolve
`CollectorError("blocked")` sem incrementar janela (sem bater na API).

Atomicidade (revisão 2026-10-14, A1): contar e pôr TTL são UM script Lua
(`INCR` + `PEXPIRE` se a chave é nova OU está sem TTL). Com `INCR` e
`EXPIRE` separados, um crash entre os dois deixava a chave sem TTL — e o
`acquire` passava a levantar `RateLimited` para sempre. A regra "sem TTL →
põe TTL" também cura chaves antigas já órfãs. O `DECR` da espera só age se
a chave ainda existe (senão criaria `-1` sem TTL).
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import cast

from content_factory.domain.errors import CollectorError, RateLimited
from content_factory.infra.redis_ops import RedisOps

Sleeper = Callable[[float], Awaitable[None]]

# KEYS[1] = contador, ARGV[1] = TTL em ms → {count, pttl}
INCR_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
local pttl = redis.call('PTTL', KEYS[1])
if count == 1 or pttl < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  pttl = tonumber(ARGV[1])
end
return {count, pttl}
"""

# KEYS[1] = contador → novo valor, ou -1 se a chave já expirou (não recria)
DECR_IF_EXISTS = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return redis.call('DECR', KEYS[1])
end
return -1
"""


@dataclass(frozen=True)
class Window:
    limit: int
    period_s: int


DEFAULT_WINDOWS: dict[str, Window] = {
    # ~100 descobertas/dia ≈ cota padrão de 10k unidades (search custa 100)
    "youtube": Window(limit=100, period_s=86_400),
    "tiktok": Window(limit=6, period_s=60),
    "instagram": Window(limit=20, period_s=60),
}
FALLBACK_WINDOW = Window(limit=10, period_s=60)

_BREAKER_THRESHOLD = 3
_BREAKER_TTL_S = 900  # 15 min
_MAX_ATTEMPTS = 5
_MAX_WAIT_S = 60.0


def backoff_delay(attempt: int) -> float:
    """Backoff exponencial (base 1 s) com jitter ±20%."""
    # jitter de retry não é uso criptográfico (B311 aceito aqui)
    return float(2**attempt) * random.uniform(0.8, 1.2)  # nosec B311


class RedisRateLimiter:
    def __init__(
        self,
        client: RedisOps,
        *,
        windows: Mapping[str, Window] | None = None,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._client = client
        self._windows: Mapping[str, Window] = DEFAULT_WINDOWS if windows is None else windows
        self._sleep = sleep

    async def acquire(self, platform: str) -> None:
        await self._assert_not_blocked(platform)
        window = self._windows.get(platform, FALLBACK_WINDOW)
        key = f"cf:rl:{platform}"
        for attempt in range(_MAX_ATTEMPTS):
            count, pttl = await self._incr_with_ttl(key, window.period_s)
            if count <= window.limit:
                return
            # não infla a janela enquanto espera (atômico: não recria a chave)
            await self._client.eval(DECR_IF_EXISTS, 1, key)
            remaining = pttl / 1000
            if remaining > _MAX_WAIT_S:
                raise RateLimited(platform)  # cota diária não segura o worker
            await self._sleep(min(backoff_delay(attempt), remaining))
        raise RateLimited(platform)

    async def _incr_with_ttl(self, key: str, ttl_s: int) -> tuple[int, int]:
        """INCR + TTL garantido, atômico (script Lua) → (contagem, pttl em ms)."""
        result = await self._client.eval(INCR_WITH_TTL, 1, key, str(ttl_s * 1000))
        count, pttl = cast(list[int], result)
        return int(count), int(pttl)

    async def record_failure(self, platform: str) -> None:
        """3 falhas consecutivas → circuit aberto por 15 min (spec §7)."""
        failures, _ = await self._incr_with_ttl(self._breaker_failures(platform), _BREAKER_TTL_S)
        if failures >= _BREAKER_THRESHOLD:
            await self._client.set(self._breaker_block(platform), "1", ex=_BREAKER_TTL_S)

    async def record_success(self, platform: str) -> None:
        await self._client.delete(self._breaker_failures(platform))

    async def _assert_not_blocked(self, platform: str) -> None:
        remaining = await self._client.ttl(self._breaker_block(platform))
        if remaining == -1 or remaining > 0:  # -2 = ausente
            raise CollectorError("blocked", platform)

    @staticmethod
    def _breaker_failures(platform: str) -> str:
        return f"cf:cb:{platform}:fails"

    @staticmethod
    def _breaker_block(platform: str) -> str:
        return f"cf:cb:{platform}:blocked"
