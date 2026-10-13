from types import TracebackType
from typing import Any, Self

import pytest
from sqlalchemy.engine import make_url

from content_factory.config import Settings
from content_factory.infra.db.base import (
    create_engine_and_factory,
    get_session_factory,
    session_scope,
)


def test_get_session_factory_binds_settings_url() -> None:
    settings = Settings(database_url="postgresql+psycopg://user:pass@localhost:5432/db")
    factory = get_session_factory(settings)
    engine = factory.kw["bind"]
    assert engine is not None
    assert engine.url == make_url(settings.database_url)
    assert factory.kw.get("expire_on_commit") is False


async def test_create_engine_and_factory_shares_single_engine() -> None:
    """Task 10: 1 engine por factory — 2 sessões distintas, mesmo bind.

    Sem I/O: o engine é lazy (nenhuma conexão é aberta aqui).
    """
    settings = Settings(database_url="postgresql+psycopg://user:pass@localhost:5432/db")
    engine, factory = create_engine_and_factory(settings)
    try:
        assert factory.kw["bind"] is engine
        assert factory.kw.get("expire_on_commit") is False
        s1 = factory()
        s2 = factory()
        try:
            assert s1 is not s2  # AsyncSession NUNCA é compartilhada entre UoWs
        finally:
            await s1.close()
            await s2.close()
    finally:
        await engine.dispose()


class _RecordingSession:
    """Sessão fake com async-CM próprio (sem I/O): registra commit/rollback/close."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()  # espelha AsyncSession.__aexit__ (sempre fecha)

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")

    async def close(self) -> None:
        self.events.append("close")


def _recording_factory(seen: list[_RecordingSession]) -> Any:
    """Factory fake com a mesma dualidade da real: `factory()` é um CM de sessão."""

    def _make() -> _RecordingSession:
        session = _RecordingSession()
        seen.append(session)
        return session

    return _make


async def test_session_scope_rolls_back_and_closes_on_error() -> None:
    """Exceção na UoW → rollback + close; o erro propaga (o job registra no envelope)."""
    seen: list[_RecordingSession] = []
    with pytest.raises(RuntimeError, match="boom"):
        async with session_scope(_recording_factory(seen)):
            raise RuntimeError("boom")
    assert [s.events for s in seen] == [["rollback", "close"]]


async def test_session_scope_clean_exit_closes_without_rollback() -> None:
    """Saída limpa → sem rollback; o commit é do caller (1 por segmento, no job)."""
    seen: list[_RecordingSession] = []
    async with session_scope(_recording_factory(seen)) as session:
        await session.commit()
    assert [s.events for s in seen] == [["commit", "close"]]
