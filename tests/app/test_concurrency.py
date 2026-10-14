import asyncio

import pytest

from content_factory.app.concurrency import PlatformSlots


async def test_platform_slots_cap_concurrent_entries() -> None:
    """Etapa 0 Task 7: teto por plataforma limita entradas simultâneas."""
    slots = PlatformSlots({"youtube": 1})
    state = {"current": 0, "peak": 0}

    async def _work() -> None:
        async with slots.slot("youtube"):
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.02)
            state["current"] -= 1

    await asyncio.gather(_work(), _work(), _work())
    assert state["peak"] == 1


async def test_platform_without_limit_is_not_blocked() -> None:
    """Plataforma sem teto configurado = sem semáforo (sobrepõe livre)."""
    slots = PlatformSlots({"youtube": 1})
    state = {"current": 0, "peak": 0}

    async def _work() -> None:
        async with slots.slot("tiktok"):
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.02)
            state["current"] -= 1

    await asyncio.gather(_work(), _work(), _work())
    assert state["peak"] == 3


async def test_empty_slots_never_block() -> None:
    """Default vazio: nenhuma plataforma tem teto."""
    slots = PlatformSlots()
    state = {"current": 0, "peak": 0}

    async def _work() -> None:
        async with slots.slot("youtube"):
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.02)
            state["current"] -= 1

    await asyncio.gather(_work(), _work())
    assert state["peak"] == 2


async def test_bounded_gather_preserves_input_order() -> None:
    """Etapa 2 Task 12: resultado na ordem de entrada, não na de término."""
    from content_factory.app.concurrency import bounded_gather

    async def _fn(delay: float) -> float:
        await asyncio.sleep(delay)
        return delay

    assert await bounded_gather([0.03, 0.0, 0.01], 3, _fn) == [0.03, 0.0, 0.01]


async def test_bounded_gather_caps_concurrency() -> None:
    from content_factory.app.concurrency import bounded_gather

    state = {"current": 0, "peak": 0}

    async def _fn(_item: int) -> None:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0.01)
        state["current"] -= 1

    await bounded_gather(list(range(6)), 2, _fn)
    assert state["peak"] == 2


async def test_bounded_gather_limit_below_one_runs_serially() -> None:
    from content_factory.app.concurrency import bounded_gather

    state = {"current": 0, "peak": 0}

    async def _fn(_item: int) -> None:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0)
        state["current"] -= 1

    await bounded_gather([1, 2, 3], 0, _fn)
    assert state["peak"] == 1


async def test_bounded_gather_empty_input() -> None:
    from content_factory.app.concurrency import bounded_gather

    async def _fn(item: int) -> int:
        return item

    assert await bounded_gather([], 3, _fn) == []


async def test_bounded_gather_external_cancel_cancels_all() -> None:
    """Cancelamento do pai propaga e cancela as tasks em voo (TaskGroup)."""
    from content_factory.app.concurrency import bounded_gather

    cancelled: list[int] = []

    async def _fn(item: int) -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(item)
            raise

    task = asyncio.create_task(bounded_gather([1, 2], 2, _fn))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(cancelled) == [1, 2]
    assert task.cancelled()
