"""Raiz de composição do worker (Etapa 2 Task 16, extraída de `worker.py`).

`SharedDeps` (task-safe, vive o processo), `AppDeps` (fábrica de bundles por
segmento, 1 UoW cada) e `compose_app_deps`. Não lê env: o worker lê as
variáveis no startup e passa os valores.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from content_factory.app.collect import CollectDeps
from content_factory.app.concurrency import PlatformSlots
from content_factory.app.ports import (
    Collector,
    KeywordExpander,
    PlatformHealth,
    RateLimiter,
    ResearchCampaignRepository,
    SocialDiscoverer,
    TrendsProvider,
)
from content_factory.app.research_pipeline import ResearchDeps
from content_factory.app.score import ScoreDeps
from content_factory.app.segment_deps import SegmentDepsBundle, SegmentDepsFactory
from content_factory.config import Settings
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, ResearchCampaign, Segment
from content_factory.infra.redis_ops import RedisOps


def _campaign_repo(session: AsyncSession) -> ResearchCampaignRepository:
    # import tardio: o teste troca `repositories.ResearchCampaignRepositoryImpl`
    from content_factory.infra.db.repositories import ResearchCampaignRepositoryImpl

    return ResearchCampaignRepositoryImpl(session)


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
            return await _campaign_repo(session).active_segments()

    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
        async with self.factory() as session:
            return await _campaign_repo(session).active_for_segment(segment_id)

    async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign:
        async with self.factory() as session:
            result = await _campaign_repo(session).start(segment_id, ends_at)
            await session.commit()
            return result

    async def stop(self, segment_id: int) -> None:
        async with self.factory() as session:
            await _campaign_repo(session).stop(segment_id)
            await session.commit()


@dataclass(frozen=True)
class SharedDeps:
    """O que vive o processo inteiro e é task-safe (Etapa 2 Task 14).

    Nada aqui liga sessão: coletores/providers são singletons do registry,
    o limiter e o `lock_client` falam com o Redis compartilhado (cota e
    breaker globais, Task 3), e `platform_slots` é UMA instância para o
    processo (teto por plataforma entre segmentos, Etapa 0 Task 7). O
    `RedisLock` NÃO mora aqui: o token de posse é por instância, então cada
    bundle cria o seu sobre `lock_client` (Etapa 0 Task 2).
    """

    collectors: list[Collector]
    socials: dict[str, SocialDiscoverer]
    web: KeywordExpander
    trends: TrendsProvider
    limiter: RateLimiter
    # breaker por plataforma no Redis — o MESMO RedisRateLimiter (chaves cf:cb:*)
    health: PlatformHealth
    lock_client: RedisOps
    platform_slots: PlatformSlots
    # COLLECT_MAX_CONCURRENCY, lido 1x no startup pelo worker: trocar exige restart
    max_concurrent_discoveries: int


@dataclass
class AppDeps(SegmentDepsFactory):
    """Deps de longa duração do worker — engine + factory compartilhados, sessão por UoW.

    Etapa 2 Tasks 14/15: sem sessão de startup e sem deps "template" — os
    `CollectDeps`/`ScoreDeps`/`ResearchDeps` nascem inteiros em
    `create_segment_deps`, a partir de `shared` + a sessão da UoW. `campaigns`
    abre 1 sessão fresca por chamada (`_FreshSessionCampaigns`), então jobs
    sobrepostos (`max_jobs=3`) nunca disputam sessão. O engine é
    compartilhado (task-safe) e fechado em `close`; a SESSÃO nunca é
    compartilhada (AsyncSession não é task-safe).
    """

    shared: SharedDeps
    campaigns: ResearchCampaignRepository
    session_factory: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    close: Callable[[], Awaitable[None]]

    @asynccontextmanager
    async def create_segment_deps(self, segment: Segment) -> AsyncGenerator[SegmentDepsBundle]:
        """1 segmento = 1 UoW = 1 transação, sob `bounded_gather` (Task 11).

        Repositórios e `RedisLock` nascem aqui, por UoW; o resto vem de
        `shared`. Exceção dentro do escopo reverte SÓ a sessão do segmento
        (`session_scope`); o caller commita via `bundle.uow`.
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

        shared = self.shared
        async with session_scope(self.session_factory) as session:
            videos = VideoRepositoryImpl(session)
            segments = SegmentRepositoryImpl(session)
            runs = CollectRunRepositoryImpl(session)
            yield SegmentDepsBundle(
                uow=SessionUnitOfWork(session),
                collect=CollectDeps(
                    # todos os coletores registrados; o job filtra pela plataforma pedida
                    collectors=shared.collectors,
                    videos=videos,
                    segments=segments,
                    runs=runs,
                    limiter=shared.limiter,
                    lock=RedisLock(shared.lock_client),
                    max_concurrent_discoveries=shared.max_concurrent_discoveries,
                    platform_slots=shared.platform_slots,
                    health=shared.health,
                ),
                score=ScoreDeps(
                    videos=videos,
                    scores=ScoreRepositoryImpl(session),
                    segment=segment,
                    keywords=segment.keywords,
                ),
                research=ResearchDeps(
                    web=shared.web,
                    trends=shared.trends,
                    socials=shared.socials,
                    signals=KeywordSignalRepositoryImpl(session),
                    trend_observations=TrendObservationRepositoryImpl(session),
                    segments=segments,
                    videos=videos,
                    runs=runs,
                    lock=RedisLock(shared.lock_client),
                    limiter=shared.limiter,
                    guard=FanoutGuard(segment.research or DepthConfig()),
                    platform_slots=shared.platform_slots,
                    health=shared.health,
                ),
            )


async def compose_app_deps(
    settings: Settings,
    *,
    max_concurrent_discoveries: int,
    platform_limits: dict[str, int],
) -> AppDeps:
    """Fábrica do worker: `register_defaults` + engine + Redis compartilhado.

    Não abre sessão nem resolve segmento: cada job lê os segmentos ativos
    (`ResearchCampaignRepository.active_segments`) e cada segmento ganha sua
    UoW em `create_segment_deps`. Engine e factory vêm de
    `create_engine_and_factory` (Etapa 2 Task 15 — o engine é fechado em
    `close`). Não lê env: os tetos de concorrência chegam como argumento
    (o worker os lê no startup — `worker.create_app_deps`).

    Composição (Fase D→B): `web` = WebSuggestProvider (Google Suggest,
    ADR-0005); `trends` = TrendsGoogleProvider; `socials` = coletores que
    satisfazem SocialDiscoverer estruturalmente.
    """
    from content_factory.infra.collectors import register_defaults, registry
    from content_factory.infra.collectors.trends_google import TrendsGoogleProvider
    from content_factory.infra.collectors.web_suggest import WebSuggestProvider
    from content_factory.infra.db.base import create_engine_and_factory
    from content_factory.infra.ratelimit import RedisRateLimiter
    from content_factory.infra.redis_ops import redis_client

    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(redis_client(settings.redis_url))
        # Cota de API e breaker são globais por chave de API, não por fila
        # (Task 3): com /1, /2, /3 cada worker teria sua própria janela
        # cf:rl:youtube e seu próprio breaker — a cota real seria consumida
        # em até 3x. Locks de segmento vão no mesmo cliente compartilhado,
        # porque lock por fila não protege nada entre workers. O validator de
        # Settings já resolve o default (rate_limit_redis_url = redis_url).
        if settings.rate_limit_redis_url == settings.redis_url:
            shared_client = client
        else:
            shared_client = await stack.enter_async_context(
                redis_client(settings.rate_limit_redis_url)
            )
        engine, factory = create_engine_and_factory(settings)
        # 1º callback = último a rodar: dispose acontece mesmo se o startup
        # falhar adiante ou se o close de um cliente Redis levantar
        stack.push_async_callback(engine.dispose)

        async def _close() -> None:
            await stack.aclose()

        register_defaults(settings)
        # socials: só collectors que implementam discover_by_keywords (Fase C+)
        socials: dict[str, SocialDiscoverer] = {}
        for name in registry.snapshot():
            collector = registry.resolve([name])[0]
            if hasattr(collector, "discover_by_keywords"):
                socials[name] = collector  # type: ignore[assignment]
        limiter = RedisRateLimiter(shared_client)
        return AppDeps(
            shared=SharedDeps(
                collectors=registry.resolve(list(registry.snapshot())),
                socials=socials,
                web=WebSuggestProvider(),
                trends=TrendsGoogleProvider(),
                limiter=limiter,
                health=limiter,
                lock_client=shared_client,
                platform_slots=PlatformSlots(platform_limits),
                max_concurrent_discoveries=max_concurrent_discoveries,
            ),
            campaigns=_FreshSessionCampaigns(factory),
            session_factory=factory,
            engine=engine,
            close=_close,
        )
    except BaseException:
        await stack.aclose()  # startup falhou: não deixa Redis nem pool órfãos
        raise
