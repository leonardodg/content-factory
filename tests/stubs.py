"""Test doubles puros para os Protocolos de `app/ports` (Task 2).

Todos os doubles implementam o Protocolo inteiro — métodos não usados
levantam `NotImplementedError` — para o pyright strict validar também a
estrutura dos testes. Sem Redis, sem banco, sem rede.
"""

import asyncio
from collections.abc import Sequence
from datetime import datetime
from math import ceil
from typing import Any, ClassVar

from content_factory.app.ports import (
    Collector,
    CollectRunRepository,
    SegmentRepository,
    TrendObservationRepository,
    VideoRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Segment,
    SegmentConfig,
    TrendPoint,
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
    def __init__(self, updates_only: bool = False, skipped: int = 0) -> None:
        self._updates_only = updates_only
        self._skipped = skipped
        self.upserts: list[tuple[int, str, int]] = []

    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats:
        self.upserts.append((segment_id, source, len(candidates)))
        if self._updates_only:  # re-execução idempotente: tudo já existe
            return UpsertStats(
                inserted=0, updated=len(candidates) - self._skipped, skipped=self._skipped
            )
        return UpsertStats(inserted=len(candidates) - self._skipped, skipped=self._skipped)

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
    def __init__(
        self,
        status_counts: dict[str, int] | None = None,
        timing_stats: dict[str, dict[str, float | int | None]] | None = None,
    ) -> None:
        self.recorded: list[CollectRun] = []
        self._status_counts = status_counts
        self._timing_stats = timing_stats

    async def record(self, run: CollectRun) -> int:
        self.recorded.append(run)
        return len(self.recorded)

    async def status_counts(self, segment_id: int, since: datetime) -> dict[str, int]:
        _ = segment_id, since
        if self._status_counts is None:
            raise NotImplementedError("configure via FakeRepos(..., status_counts={...})")
        return dict(self._status_counts)

    async def timing_stats(
        self, segment_id: int, since: datetime
    ) -> dict[str, dict[str, float | int | None]]:
        _ = segment_id, since
        if self._timing_stats is None:
            raise NotImplementedError("configure via FakeRepos(..., timing_stats={...})")
        return {k: dict(v) for k, v in self._timing_stats.items()}


class FakeRepos:
    """Tripla de repositórios em memória (videos/segments/runs)."""

    def __init__(
        self,
        segment_id: int = 1,
        *,
        updates_only: bool = False,
        skipped: int = 0,
        status_counts: dict[str, int] | None = None,
        timing_stats: dict[str, dict[str, float | int | None]] | None = None,
    ) -> None:
        self.videos: VideoRepository = _FakeVideos(updates_only, skipped)
        self.segments: SegmentRepository = _FakeSegments(segment_id)
        self.runs: CollectRunRepository = _FakeRuns(status_counts, timing_stats)


# aliases públicas p/ testes que precisam fazer isinstance/asserts
FakeVideos = _FakeVideos
FakeRuns = _FakeRuns


class ConcurrencyTracker:
    """Contador compartilhado de concorrência (Task 7).

    Vários collectors instrumentados apontam para a mesma instância —
    `peak` é o máximo de `discover()` simultâneos observados.
    """

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0
        self.entered: list[str] = []
        self.exited: list[str] = []


def TrackedCollector(
    platform: str,
    tracker: ConcurrencyTracker,
    n: int = 2,
    delay: float = 0.0,
    fail: CollectorError | None = None,
) -> Collector:
    """Collector instrumentado: mede sobreposição real de discover().

    Mesmo idioma de `FakeCollector` (classe com `platform: ClassVar` presa
    por closure, para satisfazer o Protocol). `delay` simula I/O de rede;
    `fail` levanta no lugar de devolver vídeos. Entradas/saídas vão para o
    `tracker` compartilhado — `tracker.entered == []` prova "sem bater na API".
    """
    name = platform
    count = n
    wait = delay
    error = fail

    class Impl:
        platform: ClassVar[str] = name

        async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
            tracker.current += 1
            tracker.peak = max(tracker.peak, tracker.current)
            tracker.entered.append(name)
            try:
                if wait:
                    await asyncio.sleep(wait)
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
            finally:
                tracker.current -= 1
                tracker.exited.append(name)

    return Impl()


class FakeTrendObservations(TrendObservationRepository):
    """Registra pontos gravados e devolve histórico em memória."""

    def __init__(self) -> None:
        self.recorded: list[TrendPoint] = []

    async def record_many(self, segment_id: int, points: Sequence[TrendPoint]) -> int:
        self.recorded.extend(points)
        return len(points)

    async def history_for_keyword(
        self, segment_id: int, keyword: str, limit: int
    ) -> list[TrendPoint]:
        matched = [p for p in self.recorded if p.keyword == keyword]
        matched.sort(key=lambda p: p.period_start, reverse=True)
        return matched[:limit]


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

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        """Executa os scripts Lua da infra de forma atômica no dict em memória.

        Rate limiter (A1): `INCR_WITH_TTL` e `DECR_IF_EXISTS` reimplementados
        aqui; a verdade da atomicidade está em `test_ratelimit_redis.py`.
        Demais scripts: o CAS de release do lock (abaixo).
        """
        from content_factory.infra.ratelimit import DECR_IF_EXISTS, INCR_WITH_TTL

        if script == INCR_WITH_TTL:
            self.commands.append("eval:incr_with_ttl")
            key, ttl_ms = keys_and_args[0], int(keys_and_args[1])
            count = self.counters.get(key, 0) + 1
            self.counters[key] = count
            if count == 1 or key not in self.expires:
                self.expires[key] = self.clock + ttl_ms / 1000
            return [count, ceil((self.expires[key] - self.clock) * 1000)]
        if script == DECR_IF_EXISTS:
            self.commands.append("eval:decr_if_exists")
            key = keys_and_args[0]
            if key not in self.counters:
                return -1
            self.counters[key] -= 1
            return self.counters[key]
        return await self._eval_lock_cas(script, numkeys, *keys_and_args)

    async def _eval_lock_cas(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        """Executa o CAS de release de forma atômica no dict em memória.

        Espelha o Lua de `infra/lock` (`KEYS[1]` apagada só se o valor for
        `ARGV[1]`); o `script` é registrado mas não interpretado — o único
        chamador é `RedisLock.release`.
        """
        self.commands.append("eval")
        _ = script
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        if not keys or not args:
            return 0
        if self.values.get(keys[0]) == args[0]:
            self.values.pop(keys[0], None)
            self.expires.pop(keys[0], None)
            return 1
        return 0


class FakeHealth:
    """`PlatformHealth` em memória: registra falhas/sucessos por plataforma."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.successes: list[str] = []

    async def record_failure(self, platform: str) -> None:
        self.failures.append(platform)

    async def record_success(self, platform: str) -> None:
        self.successes.append(platform)
