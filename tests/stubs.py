"""Test doubles puros para os Protocolos de `app/ports` (Task 2).

Todos os doubles implementam o Protocolo inteiro — métodos não usados
levantam `NotImplementedError` — para o pyright strict validar também a
estrutura dos testes. Sem Redis, sem banco, sem rede.
"""

from collections.abc import Sequence
from datetime import datetime
from math import ceil
from typing import ClassVar

from content_factory.app.ports import (
    Collector,
    CollectRunRepository,
    SegmentRepository,
    VideoRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Segment,
    SegmentConfig,
    UpsertStats,
    VideoSnapshot,
)


def FakeCollector(platform: str, n: int = 3, fail: CollectorError | None = None) -> Collector:
    """Devolve `n` CandidateVideos; com `fail`, levanta o erro no discover."""
    name = platform
    count = n
    error = fail

    class Impl:
        platform: ClassVar[str] = name

        async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
            if error is not None:
                raise error
            return [
                CandidateVideo(
                    platform=name,
                    platform_video_id=f"{name}-{i}",
                    url=f"https://example.invalid/v/{i}",
                    creator_handle="stub",
                    title=f"video {i}",
                )
                for i in range(count)
            ]

    return Impl()


class FakeLimiter:
    def __init__(self, fail: CollectorError | None = None) -> None:
        self._fail = fail
        self.acquired: list[str] = []

    async def acquire(self, platform: str) -> None:
        self.acquired.append(platform)
        if self._fail is not None:
            raise self._fail


class FakeLock:
    """Lock de estado único: `acquired` é o que o próximo `acquire_once` devolve.

    Depois de adquirir, só volta a ficar livre com `release` — assim o teste
    de "lock liberado" falha de verdade quando o use-case esquece o release.
    """

    def __init__(self, acquired: bool = True) -> None:
        self._free = acquired
        self.acquired: list[str] = []
        self.released: list[str] = []

    async def acquire_once(self, key: str) -> bool:
        if not self._free:
            return False
        self._free = False
        self.acquired.append(key)
        return True

    async def release(self, key: str) -> None:
        self.released.append(key)
        self._free = True


class _FakeVideos:
    def __init__(self, updates_only: bool = False) -> None:
        self._updates_only = updates_only
        self.upserts: list[tuple[int, str, int]] = []

    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats:
        self.upserts.append((segment_id, source, len(candidates)))
        if self._updates_only:  # re-execução idempotente: tudo já existe
            return UpsertStats(inserted=0, updated=len(candidates))
        return UpsertStats(inserted=len(candidates))

    async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
        # collect não pontua — snapshots vazios (o use-case score usa fakes próprios)
        return []


class _FakeSegments:
    def __init__(self, segment_id: int) -> None:
        self._id = segment_id

    async def get_by_slug(self, slug: str) -> Segment:
        return Segment(
            id=self._id,
            slug=slug,
            name=slug,
            platforms=["youtube", "tiktok"],
            keywords=[],
        )

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        raise NotImplementedError("FakeRepos.segments.upsert não é usado no collect")


class _FakeRuns:
    def __init__(self, status_counts: dict[str, int] | None = None) -> None:
        self.recorded: list[CollectRun] = []
        self._status_counts = status_counts

    async def record(self, run: CollectRun) -> int:
        self.recorded.append(run)
        return len(self.recorded)

    async def status_counts(self, segment_id: int, since: datetime) -> dict[str, int]:
        _ = segment_id, since
        if self._status_counts is None:
            raise NotImplementedError("configure via FakeRepos(..., status_counts={...})")
        return dict(self._status_counts)


class FakeRepos:
    """Tripla de repositórios em memória (videos/segments/runs)."""

    def __init__(
        self,
        segment_id: int = 1,
        *,
        updates_only: bool = False,
        status_counts: dict[str, int] | None = None,
    ) -> None:
        self.videos: VideoRepository = _FakeVideos(updates_only)
        self.segments: SegmentRepository = _FakeSegments(segment_id)
        self.runs: CollectRunRepository = _FakeRuns(status_counts)


class FakeRedis:
    """Redis in-memory na fronteira: só a superfície que a infra usa.

    Relógio próprio (`advance`) para o teste de janela nunca dormir de verdade.
    """

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.values: dict[str, str] = {}
        self.expires: dict[str, float] = {}
        self.clock: float = 0.0
        self.commands: list[str] = []

    def advance(self, seconds: float) -> None:
        self.clock += seconds
        for key in [k for k, at in self.expires.items() if at <= self.clock]:
            self.counters.pop(key, None)
            self.values.pop(key, None)
            del self.expires[key]

    async def incr(self, name: str, amount: int = 1) -> int:
        self.commands.append("incr")
        value = self.counters.get(name, 0) + amount
        self.counters[name] = value
        return value

    async def decr(self, name: str, amount: int = 1) -> int:
        self.commands.append("decr")
        value = self.counters.get(name, 0) - amount
        self.counters[name] = value
        return value

    async def expire(self, name: str, time: int) -> bool:
        self.commands.append("expire")
        if name not in self.counters:
            return False
        self.expires[name] = self.clock + time
        return True

    async def ttl(self, name: str) -> int:
        self.commands.append("ttl")
        if name not in self.counters and name not in self.values:
            return -2
        at = self.expires.get(name)
        if at is None:
            return -1
        return max(1, ceil(at - self.clock))

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        self.commands.append("set")
        if nx and (name in self.values or name in self.counters):
            return None
        self.values[name] = value
        if ex is not None:
            self.expires[name] = self.clock + ex
        elif px is not None:
            self.expires[name] = self.clock + px / 1000
        return True

    async def delete(self, *names: str) -> int:
        self.commands.append("delete")
        removed = 0
        for name in names:
            existed = name in self.values or name in self.counters
            self.values.pop(name, None)
            self.counters.pop(name, None)
            self.expires.pop(name, None)
            removed += int(existed)
        return removed
