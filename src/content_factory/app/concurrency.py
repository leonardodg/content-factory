"""Teto de concorrência por plataforma, compartilhado entre segmentos (Etapa 0 Task 7)."""

import asyncio
from collections.abc import AsyncGenerator
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
