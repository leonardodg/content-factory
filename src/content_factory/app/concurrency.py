"""Primitivas de concorrência do pipeline.

`PlatformSlots`: teto por plataforma, compartilhado entre segmentos (Etapa 0
Task 7). `bounded_gather`: TaskGroup + semáforo + ordem de entrada (Etapa 2
Task 12) — o padrão que collect, research e o worker repetiam.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager


class PlatformSlots:
    """Um semáforo por plataforma; plataforma sem teto = sem limite (no-op).

    Uma única instância é criada no startup do worker e herdada por todos
    os bundles — o teto vale para o processo inteiro, não por segmento
    (sem isso, o pico seria `WORKER_MAX_SEGMENTS x COLLECT_MAX_CONCURRENCY`
    discovers, com até 3 sessões Playwright do TikTok simultâneas).
    """

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self._semaphores = {
            platform: asyncio.Semaphore(max(1, limit)) for platform, limit in (limits or {}).items()
        }

    @asynccontextmanager
    async def slot(self, platform: str) -> AsyncGenerator[None]:
        """Reserva 1 vaga da plataforma; sem teto configurado, não bloqueia."""
        semaphore = self._semaphores.get(platform)
        if semaphore is None:
            yield
            return
        async with semaphore:
            yield


async def bounded_gather[T, R](
    items: Sequence[T], limit: int, fn: Callable[[T], Awaitable[R]]
) -> list[R]:
    """Roda `fn(item)` para cada item, no máximo `limit` por vez (mín. 1).

    Resultado na ORDEM DE ENTRADA, não na de término. Contrato: `fn` não
    deve levantar — cada chamador captura na própria fronteira e devolve um
    resultado; exceção que escapar cancela as irmãs (semântica do
    TaskGroup) e sobe como `ExceptionGroup`. Cancelamento externo propaga
    e cancela as tasks em voo.
    """
    semaphore = asyncio.Semaphore(max(1, limit))
    results: dict[int, R] = {}

    async def _run(index: int, item: T) -> None:
        async with semaphore:
            results[index] = await fn(item)

    async with asyncio.TaskGroup() as task_group:
        for index, item in enumerate(items):
            task_group.create_task(_run(index, item))
    return [results[index] for index in range(len(items))]
