"""Worker arq — jobs `collect_job`/`score_job`/`research_job` com cron diário.

Camada de composição: monta os deps uma vez no startup (registry +
repositórios + Redis) e os injeta no `ctx` do arq; os jobs chamam os MESMOS
use-cases da CLI (`app.collect`/`app.score`/`app.research_pipeline`) — zero
lógica duplicada.

Hooks (`_collect_impl`, `_score_impl`, `_research_impl`, `_load_settings`)
existem para o teste trocar por fakes sem tocar no arq/banco (fronteira = mock).
"""

import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import tzinfo
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from arq.connections import RedisSettings
from arq.cron import CronJob, cron
from arq.typing import StartupShutdown
from arq.worker import Function, func

from content_factory.app.collect import CollectDeps, collect
from content_factory.app.ports import ResearchCampaignRepository
from content_factory.app.research_pipeline import ResearchDeps, run_research_cycle
from content_factory.app.score import ScoreDeps, score
from content_factory.config import Settings, get_settings
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, Segment


def parse_cron_hours(raw: str) -> set[int]:
    """`"08,20"` → `{8, 20}`; vazio ou lixo cai no default 2x/dia."""
    try:
        hours: set[int] = {int(h) for h in raw.split(",") if h.strip()}
    except ValueError:
        hours = set()
    return hours or {8, 20}


def _load_settings() -> dict[str, Any]:
    """Config do job de coleta — env `COLLECT_*` (sem mais `COLLECT_SEGMENT`).

    Qual segmento processar sai de `active_segments()` por job — a env
    antiga virou inerte e pode sair do compose junto do fim do
    experimento de 3 workers.
    """
    platforms = [p.strip() for p in os.getenv("COLLECT_PLATFORMS", "").split(",") if p.strip()]
    return {
        "platforms": platforms,
        "limit": int(os.getenv("COLLECT_LIMIT", "10")),
    }


# hooks de teste: monkeypatch troca por fakes sem tocar no arq/banco
_collect_impl: Callable[..., Awaitable[Any]] = collect
_score_impl: Callable[..., Awaitable[Any]] = score
_research_impl: Callable[..., Awaitable[Any]] = run_research_cycle


async def run_collect_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Coleta de todos os segmentos com campanha ativa — 1 commit por segmento.

    `COLLECT_SEGMENT` não é mais lido: a lista vem de
    `ResearchCampaignRepository.active_segments()`. 1 segmento com erro
    não reverte os outros: `collect()` só captura `CollectorError`
    internamente (por plataforma) — qualquer outra exceção de um
    collector (ex.: `DDGSException` do web) é pega aqui por segmento,
    com rollback, pra não travar a sessão pros segmentos seguintes do
    mesmo tick nem perder o commit já feito dos anteriores.
    """
    cfg = _load_settings()
    deps: CollectDeps = ctx["collect_deps"]
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()
    results: dict[str, Any] = {}
    for segment in active:
        platforms: list[str] = cfg["platforms"] or segment.platforms
        try:
            out = await _collect_impl(segment.slug, platforms, cfg["limit"], deps)
            await app_deps.commit()
            results[segment.slug] = {"ok": out.ok, "partial": out.partial, **out.counts}
        except Exception as exc:
            await app_deps.rollback()
            results[segment.slug] = {"ok": False, "error": str(exc)}
    return {
        "ok": all(r["ok"] for r in results.values()) if results else True,
        "segments": results,
    }


async def run_score_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Pontua todos os segmentos com campanha ativa — 1 commit por segmento.

    `ScoreDeps` é barato de construir (sem I/O): monta 1 por segmento no
    loop reaproveitando os MESMOS `videos`/`scores` (repositórios ligados
    à sessão, não ao segmento). O `segment`/`keywords` do startup são
    placeholder e nunca chegam aqui. Exceção de 1 segmento é pega aqui,
    com rollback — mesmo princípio de isolamento de `run_collect_job`/
    `run_research_job`.
    """
    base: ScoreDeps = ctx["score_deps"]
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()
    results: dict[str, Any] = {}
    for segment in active:
        deps = ScoreDeps(
            videos=base.videos,
            scores=base.scores,
            segment=segment,
            keywords=segment.keywords,
            clock=base.clock,
        )
        try:
            out = await _score_impl(deps)
            await app_deps.commit()
            results[segment.slug] = {"ok": out.ok, "scored": out.scored}
        except Exception as exc:
            await app_deps.rollback()
            results[segment.slug] = {"ok": False, "error": str(exc)}
    return {
        "ok": all(r["ok"] for r in results.values()) if results else True,
        "segments": results,
    }


async def run_research_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Ciclo diário do pipeline de pesquisa — para TODOS os segmentos ativos.

    O arq tem cron recorrente sem fim; a campanha (`research_campaigns`)
    expressa "rodar por N semanas" — sem campanha ativa, `active_segments()`
    devolve lista vazia e o loop não roda nenhuma vez. A chave antiga
    `"skipped"` saiu do envelope: vazio é `segments: {}`.

    `run_research_cycle` só captura exceção por rede social (loop interno);
    uma falha de `web`/`trends` (ex.: `UpstreamChanged` fora do try/except)
    sobe direto — pega aqui por segmento, com rollback, pra não abortar o
    tick inteiro dos demais segmentos.
    """
    base: ResearchDeps = ctx["research_deps"]  # guard aqui é só placeholder — ver Task 5
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()
    results: dict[str, Any] = {}
    for segment in active:
        deps = replace(base, guard=FanoutGuard(segment.research or DepthConfig()))
        try:
            out = await _research_impl(segment.slug, DepthConfig(), deps)
            await app_deps.commit()
            results[segment.slug] = {"ok": out.ok, "partial": out.partial, **out.counts}
        except Exception as exc:
            await app_deps.rollback()
            results[segment.slug] = {"ok": False, "error": str(exc)}
    return {
        "ok": all(r["ok"] for r in results.values()) if results else True,
        "segments": results,
    }


@dataclass
class AppDeps:
    """Deps de longa duração do worker — 1 sessão Postgres + 1 Redis p/ os jobs."""

    collect: CollectDeps
    score: ScoreDeps
    research: ResearchDeps
    campaigns: Any  # ResearchCampaignRepository — tipado loose p/ não circular
    commit: Callable[[], Awaitable[None]]
    rollback: Callable[[], Awaitable[None]]
    close: Callable[[], Awaitable[None]]


async def create_app_deps(settings: Settings) -> AppDeps:
    """Fábrica do worker: `register_defaults` + repositórios + lock/rate-limit.

    Fase multi-segmento: NÃO resolve mais 1 segmento fixo aqui — cada job
    resolve os segmentos ativos por conta própria
    (`ResearchCampaignRepository.active_segments`) e reconstrói
    `ScoreDeps.segment`/`.keywords`/`ResearchDeps.guard` por segmento.
    Os placeholders abaixo são nunca lidos fora do loop dos jobs.
    Startup não depende mais de `cf segment load` prévio.

    Decisão de composição (Fase D→B): `web` = WebSuggestProvider real
    (Google Suggest, ADR-0005); `trends` = TrendsGoogleProvider real;
    `socials` = collectors reais que satisfazem
    SocialDiscoverer estruturalmente (Instagram desde a Fase C); `signals` =
    repositório real (tabela existe desde a Fase A).
    """
    from content_factory.app.ports import SocialDiscoverer
    from content_factory.infra.collectors import register_defaults, registry
    from content_factory.infra.collectors.trends_google import TrendsGoogleProvider
    from content_factory.infra.collectors.web_suggest import WebSuggestProvider
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        CollectRunRepositoryImpl,
        KeywordSignalRepositoryImpl,
        ResearchCampaignRepositoryImpl,
        ScoreRepositoryImpl,
        SegmentRepositoryImpl,
        TrendObservationRepositoryImpl,
        VideoRepositoryImpl,
    )
    from content_factory.infra.lock import RedisLock
    from content_factory.infra.ratelimit import RedisRateLimiter
    from content_factory.infra.redis_ops import redis_client

    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(redis_client(settings.redis_url))
        # Cota de API e breaker são globais por chave de API, não por fila
        # (Task 3): com /1, /2, /3 cada worker teria sua própria janela
        # cf:rl:youtube e seu próprio breaker — a cota real seria consumida
        # em até 3x. Operacionalmente rate_limit_redis_url aponta para /0.
        # Locks de segmento vão no mesmo cliente compartilhado, porque lock
        # por fila não protege nada entre workers.
        shared_url = settings.rate_limit_redis_url or settings.redis_url
        if shared_url == settings.redis_url:
            shared = client
        else:
            shared = await stack.enter_async_context(redis_client(shared_url))
        session = await stack.enter_async_context(get_session_factory(settings)())
        register_defaults(settings)
        # socials: só collectors que implementam discover_by_keywords (Fase C+)
        socials: dict[str, SocialDiscoverer] = {}
        for name in registry.snapshot():
            collector = registry.resolve([name])[0]
            if hasattr(collector, "discover_by_keywords"):
                socials[name] = collector  # type: ignore[assignment]
        return AppDeps(
            collect=CollectDeps(
                # todos os coletores registrados; o job filtra pela plataforma pedida
                collectors=registry.resolve(list(registry.snapshot())),
                videos=VideoRepositoryImpl(session),
                segments=SegmentRepositoryImpl(session),
                runs=CollectRunRepositoryImpl(session),
                limiter=RedisRateLimiter(shared),
                lock=RedisLock(shared),
            ),
            score=ScoreDeps(
                videos=VideoRepositoryImpl(session),
                scores=ScoreRepositoryImpl(session),
                # placeholder — run_score_job substitui por segmento
                segment=Segment(id=0, slug="_placeholder", name="_", platforms=[], keywords=[]),
                keywords=[],
            ),
            research=ResearchDeps(
                web=WebSuggestProvider(),
                trends=TrendsGoogleProvider(),
                socials=socials,
                signals=KeywordSignalRepositoryImpl(session),
                trend_observations=TrendObservationRepositoryImpl(session),
                segments=SegmentRepositoryImpl(session),
                videos=VideoRepositoryImpl(session),
                runs=CollectRunRepositoryImpl(session),
                limiter=RedisRateLimiter(shared),
                lock=RedisLock(shared),
                guard=FanoutGuard(DepthConfig()),  # placeholder — run_research_job substitui
            ),
            campaigns=ResearchCampaignRepositoryImpl(session),
            commit=session.commit,
            rollback=session.rollback,
            close=stack.aclose,
        )
    except BaseException:
        await stack.aclose()  # startup falhou: não deixa sessão/Redis órfãos
        raise


async def startup(ctx: dict[str, Any]) -> None:
    """Injeta deps no `ctx` do arq (hook `on_startup`)."""
    segment = os.getenv("COLLECT_SEGMENT")
    if segment:
        raise RuntimeError(
            "worker agora é multi-segmento e COLLECT_SEGMENT está definido "
            f"({segment!r}): o compose do experimento de 3 workers "
            "(docker-compose.experimento-7dias.yml) deve ser substituído pelo "
            "compose de worker único (docker-compose.multi-segmento.yml, só após 2026-10-19)"
        )
    deps = await create_app_deps(get_settings())
    ctx["app_deps"] = deps
    ctx["collect_deps"] = deps.collect
    ctx["score_deps"] = deps.score
    ctx["research_deps"] = deps.research
    ctx["campaigns_deps"] = deps.campaigns


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
        func(run_research_job, name="research_job"),
    ]
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(run_research_job, name="research_job", hour={7}, minute=0, unique=True),
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
    # sessão única compartilhada entre os jobs: 1 job por vez
    # (arq default max_jobs=10 quebraria a sessão com IllegalStateChangeError)
    max_jobs: ClassVar[int] = 1
    # cron em horário local do projeto (07:00 research, 08:00/20:00 collect BRT)
    timezone: ClassVar[tzinfo] = ZoneInfo("America/Sao_Paulo")
    on_startup: ClassVar[StartupShutdown] = startup
    on_job_end: ClassVar[StartupShutdown] = job_end
    on_shutdown: ClassVar[StartupShutdown] = shutdown
