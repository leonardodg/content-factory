"""Test doubles puros para os Protocolos de `app/ports` (Task 2).

Todos os doubles implementam o Protocolo inteiro — métodos não usados
levantam `NotImplementedError` — para o pyright strict validar também a
estrutura dos testes. Sem Redis, sem banco, sem rede.
"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import ceil
from typing import Any, ClassVar, Literal

from content_factory.app.call_counters import record_quota
from content_factory.app.ports import (
    Collector,
    CollectRunRepository,
    DiscoveryRepository,
    KeywordSignalRepository,
    RemediationRepository,
    SearchQueryRepository,
    SegmentRepository,
    SegmentTermRepository,
    TermSearcher,
    TrendObservationRepository,
    VideoRepository,
    WebScoreRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Discovery,
    KeywordSignal,
    RefreshResult,
    RemediationTarget,
    SearchQuery,
    SearchQueryRun,
    Segment,
    SegmentConfig,
    SegmentTerm,
    TermTrendDay,
    TrendPoint,
    UpsertStats,
    VideoMetrics,
    VideoSnapshot,
    WebPageScoreRow,
    WebSearchHit,
)
from content_factory.domain.web_relevance import WebPageInput


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


def FakeTermSearcher(
    hits: dict[str, list[str]],
    *,
    quota_per_call: int = 0,
    fail_on: str | None = None,
    error: Exception | None = None,
) -> TermSearcher:
    """`TermSearcher` determinístico: `{termo: [ids de vídeo]}` (R6).

    `search_term` devolve `Discovery` com posição 1-based. `quota_per_call`
    é somado via `record_quota` (o contador ativo é o `counting()` de cada
    busca). `fail_on` levanta `error` naquele termo (interrompe as buscas
    seguintes, como o contrato real).
    """
    mapping = dict(hits)
    quota = quota_per_call
    fail_term = fail_on
    fail_error = error

    class Impl:
        platform: ClassVar[str] = "youtube"

        async def search_term(
            self, segment: SegmentConfig, query: SearchQuery, limit: int
        ) -> list[Discovery]:
            _ = segment, limit
            if quota:
                record_quota(quota)
            if fail_term is not None and query.term == fail_term:
                raise fail_error or CollectorError("failed", "youtube")
            return [
                Discovery(
                    candidate=CandidateVideo(
                        platform="youtube",
                        platform_video_id=vid,
                        url=f"https://youtu.be/{vid}",
                        creator_handle="stub",
                        title=vid,
                    ),
                    term=query.term,
                    origin=query.origin,
                    query_id=query.query_id,
                    result_position=position,
                    run_kind="collect",
                )
                for position, vid in enumerate(mapping.get(query.term, []), start=1)
            ]

    return Impl()


class FakeDiscoveries(DiscoveryRepository):
    """Grava as descobertas em memória e devolve quantas entraram (R6)."""

    def __init__(self) -> None:
        self.saved: list[Discovery] = []

    async def record(self, segment_id: int, discoveries: Sequence[Discovery]) -> int:
        _ = segment_id
        self.saved.extend(discoveries)
        return len(discoveries)


class FakeSearchLog(SearchQueryRepository):
    """Grava o log de buscas em memória e devolve quantas entraram (R13)."""

    def __init__(self) -> None:
        self.saved: list[SearchQueryRun] = []

    async def record_many(self, segment_id: int, runs: Sequence[SearchQueryRun]) -> int:
        _ = segment_id
        self.saved.extend(runs)
        return len(runs)


class FakeLimiter:
    def __init__(self, fail: CollectorError | None = None) -> None:
        self._fail = fail
        self.acquired: list[str] = []

    async def acquire(self, platform: str) -> None:
        self.acquired.append(platform)
        if self._fail is not None:
            raise self._fail


class FakeRefresher:
    """`RefreshableCollector` determinístico (R7).

    `found="all"` devolve todos os alvos; uma lista devolve só esses ids (os
    demais viram `missing_ids`). `quota_per_batch` é somado via `record_quota`
    por lote de 50 (mesmo custo do `videos.list` real). `error` levanta no
    lugar de devolver candidatos.
    """

    platform: ClassVar[str] = "youtube"

    def __init__(
        self,
        found: list[str] | str = "all",
        *,
        error: CollectorError | None = None,
        error_on_call: int | None = None,
        quota_per_batch: int = 0,
    ) -> None:
        self._found = found
        self.error = error
        self._error_on_call = error_on_call
        self._quota_per_batch = quota_per_batch
        self.calls = 0
        self.asked: list[RemediationTarget] = []
        self.batches: list[list[str]] = []

    async def refresh(self, targets: Sequence[RemediationTarget]) -> RefreshResult:
        self.calls += 1
        self.asked.extend(targets)
        self.batches.append([t.platform_video_id for t in targets])
        if self._quota_per_batch:
            for _ in range(0, len(targets), 50):
                record_quota(self._quota_per_batch)
        if self.error is not None and (
            self._error_on_call is None or self.calls == self._error_on_call
        ):
            raise self.error
        ids = [t.platform_video_id for t in targets]
        keep = ids if self._found == "all" else [i for i in ids if i in self._found]
        return RefreshResult(
            candidates=[
                CandidateVideo(
                    platform=self.platform,
                    platform_video_id=vid,
                    url=f"https://youtu.be/{vid}",
                    creator_handle="stub",
                    creator_followers=500,
                    title=vid,
                    views=100,
                    likes=5,
                )
                for vid in keep
            ],
            missing_ids=[i for i in ids if i not in keep],
        )


class FakeRemediationRepo(RemediationRepository):
    """`RemediationRepository` em memória (R7): alvos fixos, métricas e removidos.

    `video_id` dos alvos = posição na lista (1-based), para o teste amarrar
    o id de vídeo ao alvo (v1 → 1).
    """

    def __init__(
        self,
        targets: Sequence[str],
        platform: str = "youtube",
        languages: Mapping[str, str | None] | None = None,
    ) -> None:
        self._targets = list(targets)
        self._platform = platform
        self._languages = dict(languages or {})
        self.asked: list[tuple[int, int, int, int, list[str]]] = []
        self.metrics: list[tuple[RemediationTarget, VideoMetrics, int | None]] = []
        self.removed: list[int] = []

    async def remediation_targets(
        self,
        segment_id: int,
        since_days: int,
        limit: int,
        min_gap_hours: int,
        platforms: Sequence[str],
    ) -> list[RemediationTarget]:
        self.asked.append((segment_id, since_days, limit, min_gap_hours, list(platforms)))
        return [
            RemediationTarget(
                video_id=i + 1,
                platform=self._platform,
                platform_video_id=v,
                language=self._languages.get(v),
            )
            for i, v in enumerate(self._targets)
        ]

    async def record_metrics(
        self, target: RemediationTarget, metrics: VideoMetrics, followers_at_capture: int | None
    ) -> None:
        self.metrics.append((target, metrics, followers_at_capture))

    async def mark_removed(self, video_ids: Sequence[int]) -> int:
        self.removed.extend(video_ids)
        return len(video_ids)


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
        self.upserted: list[CandidateVideo] = []  # candidatos que chegaram ao upsert

    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats:
        self.upserts.append((segment_id, source, len(candidates)))
        self.upserted.extend(candidates)
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

    @property
    def saved(self) -> list[CollectRun]:
        """Alias de `recorded` (nomenclatura do plano do Bloco 3, R7)."""
        return self.recorded

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

    async def runs_since(self, segment_id: int, since: datetime) -> list[CollectRun]:
        return [r for r in self.recorded if r.segment_id == segment_id and r.started_at > since]


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


class FakeWebScores(WebScoreRepository):
    """`WebScoreRepository` em memória: páginas/hits fixos + scores gravados.

    `pages_for_segment` devolve as páginas configuradas (a filtragem por
    segmento é do repo real); `append_scores` só acumula para assert.
    """

    def __init__(
        self,
        pages: Sequence[WebPageInput] | None = None,
        hits: Sequence[WebSearchHit] | None = None,
    ) -> None:
        self._pages = list(pages or [])
        self._hits = list(hits or [])
        self.appended: list[WebPageScoreRow] = []
        self.until_asked: list[datetime | None] = []

    async def pages_for_segment(
        self, segment_id: int, keywords: list[str], *, until: datetime | None = None
    ) -> list[WebPageInput]:
        _ = segment_id, keywords
        self.until_asked.append(until)
        return list(self._pages)

    async def append_scores(self, rows: Sequence[WebPageScoreRow]) -> int:
        self.appended.extend(rows)
        return len(rows)

    async def hits_for_segment(
        self, segment_id: int, *, until: datetime | None = None
    ) -> list[WebSearchHit]:
        _ = segment_id
        self.until_asked.append(until)
        return list(self._hits)


class FakeKeywordSignals(KeywordSignalRepository):
    """Sinais gravados + dias distintos do Suggest (R2) em memória."""

    def __init__(self) -> None:
        self.saved: list[KeywordSignal] = []
        self.days: dict[tuple[str, date], int] = {}

    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int:
        self.saved.extend(signals)
        return len(signals)

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]:
        _ = segment_id, source, limit
        return []

    async def record_days(
        self, segment_id: int, keywords: Sequence[str], source: str, day: date
    ) -> int:
        _ = segment_id, source
        for kw in keywords:
            self.days[(kw, day)] = 1
        return len(keywords)

    async def days_seen(
        self, segment_id: int, source: str, days: int, today: date
    ) -> dict[str, int]:
        _ = segment_id, source
        counts: dict[str, int] = {}
        for kw, day in self.days:
            if 0 <= (today - day).days < days:
                counts[kw] = counts.get(kw, 0) + 1
        return counts


class FakeSegmentTerms(SegmentTermRepository):
    """Pool de termos (R4) em memória: acumula para assert e responde
    `top_by_origin` com a MESMA ordem do SQL (valor desc, nulos por último,
    desempate por termo; só `active`; o último upsert do termo vence)."""

    def __init__(self) -> None:
        self.saved: list[SegmentTerm] = []
        self.status_calls: list[tuple[int, str, list[str], str]] = []
        self.trend_scores: dict[str, float | None] = {}

    async def upsert_many(self, segment_id: int, terms: Sequence[SegmentTerm]) -> int:
        self.saved.extend(terms)
        return len(terms)

    async def top_by_origin(
        self,
        segment_id: int,
        origin: str,
        limit: int,
        order_by: Literal["kw_rank", "trend_score", "engagement_score"] = "kw_rank",
    ) -> list[SegmentTerm]:
        _ = segment_id
        latest = {t.term: t for t in self.saved if t.origin == origin}
        active = [t for t in latest.values() if t.status == "active"]

        def value(t: SegmentTerm) -> float | None:
            return getattr(t, order_by)

        with_value = sorted(
            (t for t in active if value(t) is not None), key=lambda t: (-(value(t) or 0.0), t.term)
        )
        without = sorted((t for t in active if value(t) is None), key=lambda t: t.term)
        return [*with_value, *without][:limit]

    async def counts_by_origin(self, segment_id: int) -> dict[str, int]:
        _ = segment_id
        return {}

    async def all_for_segment(
        self, segment_id: int, status: str | None = None
    ) -> list[SegmentTerm]:
        _ = segment_id, status
        return []

    async def set_status(
        self, segment_id: int, origin: str, keep: Sequence[str], reason: str
    ) -> int:
        self.status_calls.append((segment_id, origin, list(keep), reason))
        return 0

    async def set_trend_scores(self, segment_id: int, scores: Mapping[str, float | None]) -> int:
        _ = segment_id
        self.trend_scores.update(scores)
        self.saved = [
            t.model_copy(update={"trend_score": scores[t.term]})
            if t.origin == "suggest" and t.term in scores
            else t
            for t in self.saved
        ]
        return len(scores)


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


class FakeTermTrendDays:
    """`TermTrendRepository` em memória (R3): acumula para assert."""

    def __init__(self) -> None:
        self.saved: list[TermTrendDay] = []

    async def record_many(self, segment_id: int, rows: Sequence[TermTrendDay]) -> int:
        _ = segment_id
        self.saved.extend(rows)
        return len(rows)
