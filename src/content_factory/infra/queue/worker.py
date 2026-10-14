"""Worker arq — jobs `collect_job`/`score_job`/`research_job` com cron diário.

Env, jobs e `WorkerSettings`. A composição (`SharedDeps`/`AppDeps`) mora em
`infra/queue/composition.py` (Etapa 2 Task 16): o startup lê as envs, compõe
uma vez e injeta `app_deps` no `ctx` do arq; os jobs chamam os MESMOS
use-cases da CLI (`app.collect`/`app.score`/`app.research_pipeline`) — zero
lógica duplicada.

Hooks (`_collect_impl`, `_score_impl`, `_research_impl`, `_load_settings`)
existem para o teste trocar por fakes sem tocar no arq/banco (fronteira = mock).
"""

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import tzinfo
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from arq.connections import RedisSettings
from arq.cron import CronJob, cron
from arq.typing import StartupShutdown
from arq.worker import Function, func

from content_factory.app.collect import collect
from content_factory.app.concurrency import bounded_gather
from content_factory.app.ports import (
    ResearchCampaignRepository,
)
from content_factory.app.research_pipeline import run_research_cycle
from content_factory.app.score import score
from content_factory.config import Settings, get_settings
from content_factory.domain.models import DepthConfig, Segment
from content_factory.infra.queue.composition import AppDeps, compose_app_deps
from content_factory.infra.redact import RedactingFilter, redact

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


async def create_app_deps(settings: Settings) -> AppDeps:
    """Lê as envs de concorrência (só no startup: trocar exige restart) e compõe.

    `COLLECT_MAX_CONCURRENCY` e `PLATFORM_MAX_CONCURRENCY` valem do startup,
    ao contrário de `COLLECT_PLATFORMS`/`COLLECT_LIMIT`, relidas a cada tick.
    """
    return await compose_app_deps(
        settings,
        max_concurrent_discoveries=int(_load_settings()["max_concurrency"]),
        platform_limits=_platform_max_concurrency_from_env(),
    )


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
    # Etapa 2 Task 14: sem templates no ctx — quem usasse um deps de startup
    # reintroduziria a sessão compartilhada entre jobs sobrepostos.
    ctx["app_deps"] = deps
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
