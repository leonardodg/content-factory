"""Worker arq — jobs `collect_job`/`score_job`/`research_job` com cron diário.

Camada de composição: monta os deps uma vez no startup (registry +
repositórios + Redis) e os injeta no `ctx` do arq; os jobs chamam os MESMOS
use-cases da CLI (`app.collect`/`app.score`/`app.research_pipeline`) — zero
lógica duplicada.

Hooks (`_collect_impl`, `_score_impl`, `_research_impl`, `_load_settings`)
existem para o teste trocar por fakes sem tocar no arq/banco (fronteira = mock).
"""

import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, tzinfo
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from arq.connections import RedisSettings
from arq.cron import CronJob, cron
from arq.typing import StartupShutdown
from arq.worker import Function, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from content_factory.app.collect import CollectDeps, collect
from content_factory.app.concurrency import PlatformSlots, bounded_gather
from content_factory.app.ports import (
    ResearchCampaignRepository,
)
from content_factory.app.research_pipeline import ResearchDeps, run_research_cycle
from content_factory.app.score import ScoreDeps, score
from content_factory.app.segment_deps import SegmentDepsBundle, SegmentDepsFactory
from content_factory.config import Settings, get_settings
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, ResearchCampaign, Segment
from content_factory.infra.redact import RedactingFilter, redact
from content_factory.infra.redis_ops import RedisOps

logger = logging.getLogger(__name__)

LOG_HANDLER_NAME = "cf-worker"


def configure_logging() -> None:
    """Handler próprio para `content_factory.*` (Etapa 0 Task 8.2).

    O CLI do arq só configura o logger `arq`; sem isto os registros do app
    saem pelo `logging.lastResort` — sem timestamp, sem nível e sem
    redação. Formato com nível/timestamp + `RedactingFilter` (spec §7).
    Idempotente. `propagate` fica ligado: o root não tem handler no worker
    (sem duplicata) e o `caplog` dos testes continua vendo os registros.
    """
    app_logger = logging.getLogger("content_factory")
    if any(h.get_name() == LOG_HANDLER_NAME for h in app_logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(LOG_HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)


def parse_cron_hours(raw: str) -> set[int]:
    """`"08,20"` → `{8, 20}`; vazio ou lixo cai no default 2x/dia."""
    try:
        hours: set[int] = {int(h) for h in raw.split(",") if h.strip()}
    except ValueError:
        hours = set()
    return hours or {8, 20}


def _max_concurrency_from_env() -> int:
    """Teto de discover() simultâneos por collect (env `COLLECT_MAX_CONCURRENCY`).

    Default 2 (ponto de partida para benchmark, não limite seguro
    universal). Valor inválido ou < 1 cai no default/1 — semáforo com 0
    travaria a fase de rede para sempre.
    """
    try:
        value = int(os.getenv("COLLECT_MAX_CONCURRENCY", "2"))
    except ValueError:
        return 2
    return max(1, value)


def _max_segments_from_env() -> int:
    """Teto de segmentos simultâneos por job (env `WORKER_MAX_SEGMENTS`).

    Default 3 (1 por segmento do experimento). Valor inválido ou < 1 cai
    no default/1 — semáforo com 0 travaria o job para sempre. Lido por tick
    (dentro dos jobs), então trocar NÃO exige restart — ao contrário de
    `COLLECT_MAX_CONCURRENCY`, fixado no startup.
    """
    try:
        value = int(os.getenv("WORKER_MAX_SEGMENTS", "3"))
    except ValueError:
        return 3
    return max(1, value)


def _platform_max_concurrency_from_env() -> dict[str, int]:
    """Teto de discovers simultâneos por plataforma (env `PLATFORM_MAX_CONCURRENCY`).

    Formato `tiktok=1,instagram=2`; entrada inválida é ignorada. Default
    `tiktok=1` (1 sessão Playwright por vez no processo). Vale do startup:
    trocar exige restart do worker. Uma única instância de `PlatformSlots`
    é criada no startup e herdada por todos os bundles — o teto é por
    processo, não por segmento.
    """
    raw = os.getenv("PLATFORM_MAX_CONCURRENCY", "tiktok=1")
    limits: dict[str, int] = {}
    for entry in raw.split(","):
        name, sep, value = entry.partition("=")
        name = name.strip()
        if not sep or not name:
            continue
        try:
            limit = int(value.strip())
        except ValueError:
            continue
        if limit >= 1:
            limits[name] = limit
    return limits


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
        "max_concurrency": _max_concurrency_from_env(),
    }


async def _gather_segments(
    active: list[Segment],
    max_segments: int,
    run_one: Callable[[Segment], Awaitable[dict[str, Any]]],
    job_name: str,
) -> dict[str, Any]:
    """`bounded_gather` por job: cada segmento com sua UoW/sessão; devolve o envelope.

    A exceção é capturada DENTRO de cada task e nunca escapa (o mesmo
    princípio da Task 7 para plataformas, repetido para segmentos): o
    TaskGroup só cancela as irmãs quando uma task levanta — aqui nenhuma
    levanta, então `results` tem TODOS os slugs de `active` e nenhum
    segmento é silenciosamente abandonado. Só CancelledError externo
    (shutdown do arq) propaga — aí o job inteiro aborta, como esperado.
    AsyncSession nunca é compartilhada entre tasks: cada `run_one` abre seu
    próprio bundle (sua própria sessão) via `create_segment_deps`.
    Segmento que falha gera 1 registro ERROR com traceback (`exc_info`) e
    o envelope leva `error_type` — o arq marca o job como concluído mesmo
    com `ok: false`, então o sinal de falha é o log + o envelope, não o
    status do job. A mensagem do envelope passa por `redact()` (o erro vai
    para o log do arq).
    """

    async def _one(segment: Segment) -> dict[str, Any]:
        try:
            return await run_one(segment)
        except Exception as exc:
            logger.exception("segmento %s falhou em %s", segment.slug, job_name)
            return {"ok": False, "error": redact(str(exc)), "error_type": type(exc).__name__}

    outcomes = await bounded_gather(active, max_segments, _one)
    results = {segment.slug: outcome for segment, outcome in zip(active, outcomes, strict=True)}
    return {
        "ok": all(r["ok"] for r in results.values()) if results else True,
        "segments": results,
    }


# hooks de teste: monkeypatch troca por fakes sem tocar no arq/banco
_collect_impl: Callable[..., Awaitable[Any]] = collect
_score_impl: Callable[..., Awaitable[Any]] = score
_research_impl: Callable[..., Awaitable[Any]] = run_research_cycle


async def run_collect_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Coleta de todos os segmentos com campanha ativa — 1 UoW/commit por segmento.

    `COLLECT_SEGMENT` não é mais lido: a lista vem de
    `ResearchCampaignRepository.active_segments()`. Os segmentos rodam
    CONCORRENTES sob TaskGroup + semáforo (`WORKER_MAX_SEGMENTS`, default
    3), cada um na SUA sessão (`create_segment_deps`): 1 segmento com erro
    não reverte os outros — `collect()` só captura `CollectorError`
    internamente (por plataforma); qualquer outra exceção de um collector
    (ex.: `DDGSException` do web) é pega DENTRO da task do segmento (o
    escopo da UoW já reverteu), pra não cancelar as irmãs nem perder o
    commit já feito das anteriores.
    """
    cfg = _load_settings()
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()

    async def _one(segment: Segment) -> dict[str, Any]:
        platforms: list[str] = cfg["platforms"] or segment.platforms
        async with app_deps.create_segment_deps(segment) as bundle:
            out = await _collect_impl(segment.slug, platforms, cfg["limit"], bundle.collect)
            await bundle.uow.commit()
        return {"ok": out.ok, "partial": out.partial, **out.counts}

    return await _gather_segments(active, _max_segments_from_env(), _one, "collect")


async def run_score_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Pontua todos os segmentos com campanha ativa — 1 UoW/commit por segmento.

    Os segmentos rodam CONCORRENTES sob TaskGroup + semáforo
    (`WORKER_MAX_SEGMENTS`). O bundle por segmento já traz `ScoreDeps`
    re-resolvido (segment/keywords do segmento, repositórios da UoW); o
    `score` do startup é placeholder e nunca chega aqui. Exceção de 1
    segmento é pega DENTRO da task — mesmo isolamento de `run_collect_job`/
    `run_research_job`, agora sob concorrência.
    """
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()

    async def _one(segment: Segment) -> dict[str, Any]:
        async with app_deps.create_segment_deps(segment) as bundle:
            out = await _score_impl(bundle.score)
            await bundle.uow.commit()
        return {"ok": out.ok, "scored": out.scored}

    return await _gather_segments(active, _max_segments_from_env(), _one, "score")


async def run_research_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Ciclo diário do pipeline de pesquisa — para TODOS os segmentos ativos.

    O arq tem cron recorrente sem fim; a campanha (`research_campaigns`)
    expressa "rodar por N semanas" — sem campanha ativa, `active_segments()`
    devolve lista vazia e o loop não roda nenhuma vez. A chave antiga
    `"skipped"` saiu do envelope: vazio é `segments: {}`.

    O bundle por segmento já traz `ResearchDeps` com o guard do segmento;
    `run_research_cycle` só captura exceção por rede social (loop interno) —
    uma falha de `web`/`trends` (ex.: `UpstreamChanged` fora do try/except)
    sobe direto e é pega DENTRO da task do segmento, pra não cancelar as
    irmãs concorrentes. Os segmentos rodam CONCORRENTES sob TaskGroup +
    semáforo (`WORKER_MAX_SEGMENTS`).
    """
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()

    async def _one(segment: Segment) -> dict[str, Any]:
        async with app_deps.create_segment_deps(segment) as bundle:
            out = await _research_impl(segment.slug, DepthConfig(), bundle.research)
            await bundle.uow.commit()
        return {"ok": out.ok, "partial": out.partial, **out.counts}

    return await _gather_segments(active, _max_segments_from_env(), _one, "research")


@dataclass
class _FreshSessionCampaigns:
    """`ResearchCampaignRepository` sobre a factory: 1 sessão fresca por chamada.

    Task 11: com `max_jobs > 1` os jobs podem se sobrepor (collect das 8h +
    score das 8h30 no mesmo worker) — um repo ligado à sessão de startup
    quebraria com `IllegalStateChangeError` no uso concorrente. Cada chamada
    abre sua própria sessão de leitura, então `active_segments()` é seguro
    sob TaskGroup E entre jobs. `start`/`stop` comitam na própria sessão
    (a CLI faz o mesmo com a factory direta).
    """

    factory: async_sessionmaker[AsyncSession]

    async def active_segments(self) -> list[Segment]:
        async with self.factory() as session:
            from content_factory.infra.db.repositories import ResearchCampaignRepositoryImpl

            return await ResearchCampaignRepositoryImpl(session).active_segments()

    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
        async with self.factory() as session:
            from content_factory.infra.db.repositories import ResearchCampaignRepositoryImpl

            return await ResearchCampaignRepositoryImpl(session).active_for_segment(segment_id)

    async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign:
        async with self.factory() as session:
            from content_factory.infra.db.repositories import ResearchCampaignRepositoryImpl

            result = await ResearchCampaignRepositoryImpl(session).start(segment_id, ends_at)
            await session.commit()
            return result

    async def stop(self, segment_id: int) -> None:
        async with self.factory() as session:
            from content_factory.infra.db.repositories import ResearchCampaignRepositoryImpl

            await ResearchCampaignRepositoryImpl(session).stop(segment_id)
            await session.commit()


@dataclass
class AppDeps(SegmentDepsFactory):
    """Deps de longa duração do worker — engine + factory compartilhados, sessão por UoW.

    Task 10/11: `collect`/`score`/`research` são TEMPLATES montados no
    startup sobre 1 sessão — os jobs NUNCA os usam como sessão; cada segmento
    constrói seu bundle via `create_segment_deps` (repositórios nascem por
    UoW; collectors/limiter/providers/clock/max_concurrency são herdados dos
    templates e continuam compartilhados). `campaigns` abre 1 sessão fresca
    por chamada (`_FreshSessionCampaigns`), então jobs sobrepostos nunca
    disputam sessão. O engine e os clientes Redis continuam compartilhados
    (são task-safe); a SESSÃO NUNCA é (AsyncSession não é task-safe).
    Etapa 0 Task 2: o `lock` dos templates é placeholder — cada bundle ganha
    seu próprio `RedisLock` sobre o `lock_client` compartilhado (a instância
    de lock, com seu mapa de tokens, NÃO é task-safe entre segmentos).
    Etapa 0 Task 7: `platform_slots` É compartilhado (a instância única do
    startup é herdada por todos os bundles via replace) — o teto por
    plataforma é por processo de propósito.
    `commit`/`rollback` de startup e `job_end`/`on_job_end` foram REMOVIDOS
    na Task 11: cada UoW já reverte em exceção via `session_scope`, e não há
    mais sessão global a limpar após o job.
    """

    collect: CollectDeps
    score: ScoreDeps
    research: ResearchDeps
    campaigns: Any  # ResearchCampaignRepository — tipado loose p/ não circular
    close: Callable[[], Awaitable[None]]
    engine: AsyncEngine | None  # None só com o seam fake de teste (sem .kw["bind"])
    session_factory: async_sessionmaker[AsyncSession]
    lock_client: RedisOps  # cliente compartilhado; cada bundle cria seu RedisLock

    @asynccontextmanager
    async def create_segment_deps(self, segment: Segment) -> AsyncGenerator[SegmentDepsBundle]:
        """1 segmento = 1 UoW = 1 transação, sob TaskGroup + semáforo (Task 11).

        Repositórios nascem aqui, por UoW; o resto (collectors, limiter,
        providers, max_concurrency) é herdado dos templates de startup e
        continua compartilhado. O `lock` NÃO é herdado: cada bundle ganha seu
        próprio `RedisLock` sobre o `lock_client` compartilhado (Etapa 0
        Task 2 — o token mora na instância). Exceção dentro do escopo reverte
        SÓ a sessão do segmento (`session_scope`); o caller commita via
        `bundle.uow`.
        """
        from content_factory.infra.db.base import SessionUnitOfWork, session_scope
        from content_factory.infra.db.repositories import (
            CollectRunRepositoryImpl,
            KeywordSignalRepositoryImpl,
            ScoreRepositoryImpl,
            SegmentRepositoryImpl,
            TrendObservationRepositoryImpl,
            VideoRepositoryImpl,
        )
        from content_factory.infra.lock import RedisLock

        async with session_scope(self.session_factory) as session:
            yield SegmentDepsBundle(
                uow=SessionUnitOfWork(session),
                collect=replace(
                    self.collect,
                    videos=VideoRepositoryImpl(session),
                    segments=SegmentRepositoryImpl(session),
                    runs=CollectRunRepositoryImpl(session),
                    lock=RedisLock(self.lock_client),
                ),
                score=replace(
                    self.score,
                    videos=VideoRepositoryImpl(session),
                    scores=ScoreRepositoryImpl(session),
                    segment=segment,
                    keywords=segment.keywords,
                ),
                research=replace(
                    self.research,
                    lock=RedisLock(self.lock_client),
                    signals=KeywordSignalRepositoryImpl(session),
                    trend_observations=TrendObservationRepositoryImpl(session),
                    segments=SegmentRepositoryImpl(session),
                    videos=VideoRepositoryImpl(session),
                    runs=CollectRunRepositoryImpl(session),
                    guard=FanoutGuard(segment.research or DepthConfig()),
                ),
            )


async def create_app_deps(settings: Settings) -> AppDeps:
    """Fábrica do worker: `register_defaults` + repositórios + lock/rate-limit.

    Fase multi-segmento: NÃO resolve mais 1 segmento fixo aqui — cada job
    resolve os segmentos ativos por conta própria
    (`ResearchCampaignRepository.active_segments`) e reconstrói
    `ScoreDeps.segment`/`.keywords`/`ResearchDeps.guard` por segmento.
    Os placeholders abaixo são nunca lidos fora do loop dos jobs.
    Startup não depende mais de `cf segment load` prévio.

    Concorrência de descoberta (Task 7): `max_concurrent_discoveries` é
    fixado AQUI, no startup, via `_load_settings()` — `CollectDeps` é
    montado uma vez e reutilizado por todos os jobs via
    `ctx["collect_deps"]`, então trocar `COLLECT_MAX_CONCURRENCY` exige
    restart do worker (ao contrário de `COLLECT_PLATFORMS`/`COLLECT_LIMIT`,
    relidos por `run_collect_job` a cada tick).

    Task 10: a factory de `get_session_factory` é guardada em `AppDeps` como
    fonte das sessões por segmento (`create_segment_deps`); o engine
    (`factory.kw["bind"]`) é compartilhado e fechado no shutdown (`dispose`,
    além da sessão/Redis). O seam de teste que monkeypatcha
    `db_base.get_session_factory` por uma função continua funcionando: sem
    `.kw["bind"]`, `engine` é None e o dispose é pulado.

    Task 11: `campaigns` abre 1 sessão fresca por chamada
    (`_FreshSessionCampaigns`) — com `max_jobs > 1` os jobs se sobrepõem e
    um repo ligado à sessão de startup quebraria; `commit`/`rollback` de
    startup e `job_end` saíram (cada UoW já reverte via `session_scope`).

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
        factory = get_session_factory(settings)
        session = await stack.enter_async_context(factory())
        # Task 10: engine compartilhado para as UoWs por segmento. O seam de
        # teste troca a factory por uma função (sem `.kw`) — nesse caso o
        # engine é None e o dispose no shutdown é pulado. O isinstance é
        # load-bearing em runtime (o tipo estático não vê o seam).
        if isinstance(factory, async_sessionmaker):  # pyright: ignore[reportUnnecessaryIsInstance]
            bind: Any = factory.kw.get("bind")
        else:
            bind = None
        engine: AsyncEngine | None = bind if isinstance(bind, AsyncEngine) else None

        async def _close() -> None:
            await stack.aclose()
            if engine is not None:
                await engine.dispose()

        register_defaults(settings)
        # socials: só collectors que implementam discover_by_keywords (Fase C+)
        socials: dict[str, SocialDiscoverer] = {}
        for name in registry.snapshot():
            collector = registry.resolve([name])[0]
            if hasattr(collector, "discover_by_keywords"):
                socials[name] = collector  # type: ignore[assignment]
        # Etapa 0 Task 7: UMA instância para o processo inteiro — os bundles
        # a herdam via replace (o objeto é mantido), então o teto por
        # plataforma vale entre segmentos, não por segmento.
        platform_slots = PlatformSlots(_platform_max_concurrency_from_env())
        return AppDeps(
            collect=CollectDeps(
                # todos os coletores registrados; o job filtra pela plataforma pedida
                collectors=registry.resolve(list(registry.snapshot())),
                videos=VideoRepositoryImpl(session),
                segments=SegmentRepositoryImpl(session),
                runs=CollectRunRepositoryImpl(session),
                limiter=RedisRateLimiter(shared),
                lock=RedisLock(shared),
                max_concurrent_discoveries=int(_load_settings()["max_concurrency"]),
                platform_slots=platform_slots,
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
                platform_slots=platform_slots,
            ),
            campaigns=_FreshSessionCampaigns(factory),
            close=_close,
            engine=engine,
            session_factory=factory,
            lock_client=shared,
        )
    except BaseException:
        await stack.aclose()  # startup falhou: não deixa sessão/Redis órfãos
        raise


async def startup(ctx: dict[str, Any]) -> None:
    """Injeta deps no `ctx` do arq (hook `on_startup`)."""
    configure_logging()
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
    # Sessões por UoW (1 por segmento, Task 10/11): jobs podem se sobrepor —
    # engine e clientes Redis são task-safe, AsyncSession nunca é
    # compartilhada. 3 = 1 por tipo de job (collect/score/research). O 1
    # anterior existia SÓ porque a sessão era compartilhada (quebraria com
    # IllegalStateChangeError sob o default max_jobs=10 do arq).
    max_jobs: ClassVar[int] = 3
    # cron em horário local do projeto (07:00 research, 08:00/20:00 collect BRT)
    timezone: ClassVar[tzinfo] = ZoneInfo("America/Sao_Paulo")
    on_startup: ClassVar[StartupShutdown] = startup
    # SEM on_job_end (Task 11, decisão REMOVER): o job_end antigo fazia
    # rollback na sessão global — com sessão por UoW isso perdeu o objeto
    # (cada UoW já reverte em exceção via session_scope).
    on_shutdown: ClassVar[StartupShutdown] = shutdown
