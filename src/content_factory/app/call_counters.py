"""Contadores POR CHAMADA de coletor (Etapa 1 Task 9; revisão 2026-10-14 B2).

Os coletores são singletons no registry e o worker roda segmentos
concorrentes: contar no atributo da instância soma o que outros segmentos
fizeram. O contador vive num `ContextVar`: cada task do `TaskGroup` roda numa
cópia do contexto, então `counting()` aberto dentro de uma task só vê os
`record_*()` daquela task.

- `gate_hits`: fallback do gate #10 do Instagram;
- `quota_used`: unidades de cota de API (YouTube: search = 100, list = 1),
  gravadas em `collect_runs.quota_used`.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class CallCounters:
    gate_hits: int = 0
    quota_used: int = 0


_current: ContextVar[CallCounters | None] = ContextVar("cf_call_counters", default=None)


def record_gate_hit(n: int = 1) -> None:
    """Soma `n` gate hits ao contador ativo; sem `counting()` aberto é no-op."""
    counters = _current.get()
    if counters is not None:
        counters.gate_hits += n


def record_quota(units: int) -> None:
    """Soma unidades de cota ao contador ativo; sem `counting()` aberto é no-op."""
    counters = _current.get()
    if counters is not None:
        counters.quota_used += units


@contextmanager
def counting() -> Generator[CallCounters]:
    """Abre contadores novos para o escopo (aninhado não vaza para o externo)."""
    counters = CallCounters()
    token = _current.set(counters)
    try:
        yield counters
    finally:
        _current.reset(token)
