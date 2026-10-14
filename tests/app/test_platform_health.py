"""Breaker por plataforma ligado (plano 2026-10-14-content-factory-lacunas-mapa-do-ciclo)."""

from typing import ClassVar

import pytest
from tests.stubs import FakeCollector, FakeHealth, FakeLimiter, FakeLock, FakeRepos

from content_factory.app.collect import CollectDeps, collect
from content_factory.domain.errors import (
    AuthExpired,
    CollectorError,
    InvalidPayload,
    NetworkError,
    RateLimited,
    UpstreamChanged,
)
from content_factory.domain.models import CandidateVideo, SegmentConfig


def _deps(collector: object, health: FakeHealth, limiter: FakeLimiter | None = None) -> CollectDeps:
    repos = FakeRepos()
    return CollectDeps(
        collectors=[collector],  # type: ignore[list-item]
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=limiter or FakeLimiter(),
        lock=FakeLock(),
        health=health,
    )


@pytest.mark.parametrize(
    "error",
    [
        RateLimited("youtube"),
        NetworkError("youtube"),
        UpstreamChanged("youtube"),
        CollectorError("blocked", "youtube"),
    ],
)
async def test_platform_side_discover_failure_is_recorded(error: CollectorError) -> None:
    health = FakeHealth()
    await collect("candles", ["youtube"], 5, _deps(FakeCollector("youtube", fail=error), health))
    assert health.failures == ["youtube"]
    assert health.successes == []


async def test_discover_success_resets_breaker() -> None:
    health = FakeHealth()
    await collect("candles", ["youtube"], 5, _deps(FakeCollector("youtube", n=1), health))
    assert health.successes == ["youtube"]
    assert health.failures == []


@pytest.mark.parametrize(
    "error", [AuthExpired("youtube"), InvalidPayload("youtube")], ids=["auth", "payload"]
)
async def test_non_platform_failures_are_not_recorded(error: CollectorError) -> None:
    """Config ausente / dado ruim não são a plataforma caindo: não abrem breaker."""
    health = FakeHealth()
    await collect("candles", ["youtube"], 5, _deps(FakeCollector("youtube", fail=error), health))
    assert health.failures == [] and health.successes == []


class _Bug:
    platform: ClassVar[str] = "youtube"

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        raise KeyError("bug nosso")


async def test_internal_error_is_not_recorded() -> None:
    health = FakeHealth()
    await collect("candles", ["youtube"], 5, _deps(_Bug(), health))
    assert health.failures == []


async def test_acquire_failure_is_not_recorded() -> None:
    """Janela cheia é o NOSSO limiter; `blocked` no acquire é o breaker já aberto."""
    health = FakeHealth()
    limiter = FakeLimiter(fail=CollectorError("blocked", "youtube"))
    await collect("candles", ["youtube"], 5, _deps(FakeCollector("youtube", n=1), health, limiter))
    assert health.failures == [] and health.successes == []


def test_default_health_is_noop() -> None:
    repos = FakeRepos()
    deps = CollectDeps(
        collectors=[],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    assert deps.health is not None  # null object: chamadas existentes seguem válidas


class _BrokenHealth:
    async def record_failure(self, platform: str) -> None:
        raise ConnectionError("redis caiu")

    async def record_success(self, platform: str) -> None:
        raise ConnectionError("redis caiu")


async def test_health_backend_failure_never_breaks_collect() -> None:
    """Registrar saúde é acessório: Redis fora não pode cancelar as irmãs."""
    repos = FakeRepos()
    deps = CollectDeps(
        collectors=[
            FakeCollector("youtube", n=1),
            FakeCollector("tiktok", fail=RateLimited("tiktok")),
        ],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
        health=_BrokenHealth(),
    )
    out = await collect("candles", ["youtube", "tiktok"], 5, deps)
    assert out.counts["inserted"] == 1
    assert out.counts["failed"] == 1
