"""Worker arq — jobs `collect_job`/`score_job` com cron 2x/dia.

Camada de composição: monta os deps uma vez no startup (registry +
repositórios + Redis) e os injeta no `ctx` do arq; os jobs chamam os MESMOS
use-cases da CLI (`app.collect`/`app.score`) — zero lógica duplicada.

Hooks (`_collect_impl`, `_score_impl`, `_load_settings`) existem para o
teste trocar por fakes sem tocar no arq/banco (fronteira = mock).
"""

import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import tzinfo
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from arq.connections import RedisSettings
from arq.cron import CronJob, cron
from arq.typing import StartupShutdown
from arq.worker import Function, func

from content_factory.app.collect import CollectDeps, collect
from content_factory.app.score import ScoreDeps, score
from content_factory.config import Settings, get_settings


def parse_cron_hours(raw: str) -> set[int]:
    """`"08,20"` → `{8, 20}`; vazio ou lixo cai no default 2x/dia."""
    try:
        hours: set[int] = {int(h) for h in raw.split(",") if h.strip()}
    except ValueError:
        hours = set()
    return hours or {8, 20}


def _load_settings() -> dict[str, Any]:
    """Config do job de coleta — env `COLLECT_*` (segmento default: candles)."""
    platforms = [p.strip() for p in os.getenv("COLLECT_PLATFORMS", "").split(",") if p.strip()]
    return {
        "segment": os.getenv("COLLECT_SEGMENT", "candles"),
        "platforms": platforms,
        "limit": int(os.getenv("COLLECT_LIMIT", "10")),
    }


# hooks de teste: monkeypatch troca por fakes sem tocar no arq/banco
_collect_impl: Callable[..., Awaitable[Any]] = collect
_score_impl: Callable[..., Awaitable[Any]] = score


async def run_collect_job(ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_settings()
    deps: CollectDeps = ctx["collect_deps"]  # buildado no startup do worker
    platforms: list[str] = cfg["platforms"]
    if not platforms:  # default: plataformas declaradas do segmento (fallback da CLI)
        platforms = (await deps.segments.get_by_slug(cfg["segment"])).platforms
    out = await _collect_impl(cfg["segment"], platforms, cfg["limit"], deps)
    await ctx["app_deps"].commit()  # commit só no sucesso; falha ⇒ rollback no job_end
    return {"ok": out.ok, "partial": out.partial, **out.counts}


async def run_score_job(ctx: dict[str, Any]) -> dict[str, Any]:
    out = await _score_impl(ctx["score_deps"])
    await ctx["app_deps"].commit()
    return {"ok": out.ok, "scored": out.scored}


@dataclass
class AppDeps:
    """Deps de longa duração do worker — 1 sessão Postgres + 1 Redis p/ os 2 jobs."""

    collect: CollectDeps
    score: ScoreDeps
    commit: Callable[[], Awaitable[None]]
    rollback: Callable[[], Awaitable[None]]
    close: Callable[[], Awaitable[None]]


async def create_app_deps(settings: Settings) -> AppDeps:
    """Fábrica do worker: `register_defaults` + repositórios + lock/rate-limit.

    Segmento precisa existir no banco (`cf segment load`) — senão o startup
    falha rápido. Testes nunca passam por aqui (monkeypatch nos hooks).
    """
    from content_factory.infra.collectors import register_defaults, registry
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        CollectRunRepositoryImpl,
        ScoreRepositoryImpl,
        SegmentRepositoryImpl,
        VideoRepositoryImpl,
    )
    from content_factory.infra.lock import RedisLock
    from content_factory.infra.ratelimit import RedisRateLimiter
    from content_factory.infra.redis_ops import redis_client

    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(redis_client(settings.redis_url))
        session = await stack.enter_async_context(get_session_factory(settings)())
        register_defaults(settings)
        segment = await SegmentRepositoryImpl(session).get_by_slug(_load_settings()["segment"])
        return AppDeps(
            collect=CollectDeps(
                # todos os coletores registrados; o job filtra pela plataforma pedida
                collectors=registry.resolve(list(registry.snapshot())),
                videos=VideoRepositoryImpl(session),
                segments=SegmentRepositoryImpl(session),
                runs=CollectRunRepositoryImpl(session),
                limiter=RedisRateLimiter(client),
                lock=RedisLock(client),
            ),
            score=ScoreDeps(
                videos=VideoRepositoryImpl(session),
                scores=ScoreRepositoryImpl(session),
                segment=segment,
                keywords=segment.keywords,
            ),
            commit=session.commit,
            rollback=session.rollback,
            close=stack.aclose,
        )
    except BaseException:
        await stack.aclose()  # startup falhou: não deixa sessão/Redis órfãos
        raise


async def startup(ctx: dict[str, Any]) -> None:
    """Injeta deps no `ctx` do arq (hook `on_startup`)."""
    deps = await create_app_deps(get_settings())
    ctx["app_deps"] = deps
    ctx["collect_deps"] = deps.collect
    ctx["score_deps"] = deps.score


async def job_end(ctx: dict[str, Any]) -> None:
    """Rollback após cada job: falha não deixa transação suja p/ o próximo."""
    await ctx["app_deps"].rollback()


async def shutdown(ctx: dict[str, Any]) -> None:
    # arq chama on_shutdown no finally, mesmo com o startup falho antes de
    # injetar os deps — sem o get, o KeyError suprimiria o erro original
    deps = ctx.get("app_deps")
    if deps is not None:
        await deps.close()


class WorkerSettings:
    """`uv run arq content_factory.infra.queue.worker.WorkerSettings` (compose)."""

    functions: ClassVar[list[Function]] = [
        func(run_collect_job, name="collect_job"),
        func(run_score_job, name="score_job"),
    ]
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(
            run_collect_job,
            name="collect_job",
            hour=parse_cron_hours(os.getenv("COLLECT_CRON_HOURS", "08,20")),
            minute=0,
            unique=True,
        ),
        cron(run_score_job, name="score_job", hour={8, 21}, minute=30, unique=True),
    ]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    # sessão única compartilhada entre os 2 jobs: 1 job por vez
    # (arq default max_jobs=10 quebraria a sessão com IllegalStateChangeError)
    max_jobs: ClassVar[int] = 1
    # cron em horário local do projeto (08:00/20:00 BRT, não 05:00/17:00 UTC)
    timezone: ClassVar[tzinfo] = ZoneInfo("America/Sao_Paulo")
    on_startup: ClassVar[StartupShutdown] = startup
    on_job_end: ClassVar[StartupShutdown] = job_end
    on_shutdown: ClassVar[StartupShutdown] = shutdown
