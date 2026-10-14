"""Engine compartilhado + sessão por unidade de trabalho (Task 10).

Um engine por processo, uma factory reutilizável, uma sessão por segmento.
`get_session_factory` continua existindo como wrapper fino porque
`tests/infra/queue/test_worker.py` o usa como seam de teste (monkeypatch).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from content_factory.app.ports import UnitOfWork
from content_factory.config import Settings


def create_engine_and_factory(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Engine único + factory reutilizável — chamar 1x no startup.

    Chamá-la por segmento vazaria pools (1 engine = 1 pool por chamada);
    o certo é 1 engine compartilhado (task-safe) com 1 sessão por UoW
    (AsyncSession NUNCA é task-safe).
    """
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


def get_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Wrapper fino sobre `create_engine_and_factory` (seam de teste do worker)."""
    _, factory = create_engine_and_factory(settings)
    return factory


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession]:
    """Uma sessão por unidade de trabalho: rollback em exceção, close sempre.

    O commit é do caller (1 por segmento, no job) — o escopo só garante que
    falha não deixa transação suja para a próxima UoW. Funciona com a factory
    real (`factory()` é um CM de AsyncSession) e com os seams de teste.
    """
    async with factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


class SessionUnitOfWork(UnitOfWork):
    """UoW concreta sobre 1 AsyncSession (dona do commit explícito).

    Satisfaz `app.ports.UnitOfWork` por herança explícita (mesmo estilo dos
    `*RepositoryImpl`) — o pyright valida a conformidade. Sem context
    manager (Etapa 2 Task 13): o rollback em exceção é de `session_scope`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
