import asyncio

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
