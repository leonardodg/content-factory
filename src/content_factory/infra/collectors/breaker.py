"""Circuit breaker por TEMPO para providers não-oficiais (Etapa 1 Task 10).

Antes o cooldown era "N ciclos" decrementado a cada chamada: com o worker
único (3 segmentos por tick sobre o MESMO provider singleton) ele durava
1/3 do previsto, e falhas de segmentos diferentes se somavam. Agora:
`threshold` falhas consecutivas abrem o breaker até `clock() + cooldown_s`;
`check()` não muta estado, então chamadas concorrentes não gastam cooldown.

Decisão (Task 10): o estado fica EM MEMÓRIA, não no Redis — com o worker
único há 1 processo e 1 instância por provider. Se voltar a haver mais de
um processo chamando o mesmo provider, mover para chaves `cf:cb:*` no Redis
compartilhado (mesma família do breaker de `infra/ratelimit.py`).
"""

import time
from collections.abc import Callable

from content_factory.domain.errors import CollectorError


class CooldownBreaker:
    def __init__(
        self,
        platform: str,
        *,
        threshold: int,
        cooldown_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._platform = platform
        self._threshold = max(1, threshold)
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._consecutive_failures = 0
        self._open_until: float | None = None

    def check(self) -> None:
        """Levanta `circuit_open` enquanto o cooldown não venceu (sem mutar estado)."""
        if self._open_until is not None and self._clock() < self._open_until:
            raise CollectorError("circuit_open", self._platform, "provider em cooldown")

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._open_until = self._clock() + self._cooldown_s
            self._consecutive_failures = 0

    def record_success(self) -> None:
        self._consecutive_failures = 0
