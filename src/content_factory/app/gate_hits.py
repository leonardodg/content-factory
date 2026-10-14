"""Contagem de gate hits POR CHAMADA (Etapa 1 Task 9).

Os coletores são singletons no registry e o worker roda segmentos
concorrentes: o delta before/after de `collector.gate_hits` somava os hits
de outros segmentos. Aqui o contador vive num `ContextVar`: cada task do
`TaskGroup` roda numa cópia do contexto, então `counting()` aberto dentro
de uma task só vê os `record_gate_hit()` daquela task.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class GateHitCounter:
    total: int = 0


_current: ContextVar[GateHitCounter | None] = ContextVar("cf_gate_hits", default=None)


def record_gate_hit(n: int = 1) -> None:
    """Soma `n` ao contador ativo; sem `counting()` aberto é no-op."""
    counter = _current.get()
    if counter is not None:
        counter.total += n


@contextmanager
def counting() -> Generator[GateHitCounter]:
    """Abre um contador novo para o escopo (aninhado não vaza para o externo)."""
    counter = GateHitCounter()
    token = _current.set(counter)
    try:
        yield counter
    finally:
        _current.reset(token)
