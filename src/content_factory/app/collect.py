import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from content_factory.app.call_counters import counting
from content_factory.app.concurrency import PlatformSlots, bounded_gather
from content_factory.app.platform_health import NoPlatformHealth, report_discover
from content_factory.app.ports import (
    Collector,
    CollectRunRepository,
    DiscoveryRepository,
    Lock,
    NoOpDiscoveryRepository,
    NoOpSearchQueryRepository,
    NoOpSegmentTermRepository,
    NoOpWebHitRepository,
    PlatformHealth,
    RateLimiter,
    SearchQueryRepository,
    SegmentRepository,
    SegmentTermRepository,
    TermSearcher,
    VideoRepository,
    WebHitRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.language import wanted_language
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Discovery,
    SearchQuery,
    SearchQueryRun,
    Segment,
    SegmentConfig,
    WebSearchHit,
)
from content_factory.domain.search_plan import (
    PoolSnapshot,
    plan_queries,
    run_index_for,
    searchable_seeds,
    searches_per_collect,
)
from content_factory.domain.web_hits import discard_counts, partition_web

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ms_between(start: datetime, end: datetime) -> int:
    """Milissegundos entre duas leituras do clock (baseline Task 4)."""
    return int((end - start).total_seconds() * 1000)


@dataclass
class CollectDeps:
    collectors: list[Collector]
    videos: VideoRepository
    segments: SegmentRepository
    runs: CollectRunRepository
    limiter: RateLimiter
    lock: Lock
    # Mesmo padrão de ScoreDeps: clock injetável, default = agora em UTC —
    # chamadas existentes (worker/CLI) continuam funcionando sem mudança.
    clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow)
    # Teto de discover() simultâneos na fase de rede (Task 7). Ponto de
    # partida para benchmark, não limite seguro universal — a persistência
    # continua sempre sequencial na sessão compartilhada.
    max_concurrent_discoveries: int = 2
    # Teto de discover() simultâneos POR PLATAFORMA, compartilhado entre
    # segmentos (Etapa 0 Task 7 — ex.: tiktok=1 serializa as sessões
    # Playwright no processo). Default vazio = sem teto.
    platform_slots: PlatformSlots = field(default_factory=PlatformSlots)
    # Breaker por plataforma (spec §7): só falha do discover do lado da
    # plataforma conta — regra em `app/platform_health.report_discover`.
    health: PlatformHealth = field(default_factory=NoPlatformHealth)
    # Rastro dos resultados web (F1); default no-op mantém testes/uso avulso.
    web_hits: WebHitRepository = field(default_factory=NoOpWebHitRepository)
    # R6: plataformas com busca por termo (só YouTube hoje). Quem está aqui
    # NÃO usa `collector.discover` — a coleta vira uma busca por termo, com
    # atribuição. Chave = `platform` (a mesma do collector).
    term_searchers: dict[str, TermSearcher] = field(default_factory=dict[str, TermSearcher])
    # R6/R13: grava a atribuição (video_discoveries) e o log de buscas
    # (search_queries); defaults no-op mantêm testes/uso avulso.
    discoveries: DiscoveryRepository = field(default_factory=NoOpDiscoveryRepository)
    search_log: SearchQueryRepository = field(default_factory=NoOpSearchQueryRepository)
    # R5 (Bloco 4): pool de termos de onde saem as buscas (vagas por origem) e
    # o env da cota `SEARCHES_PER_COLLECT_<PLATAFORMA>`. Defaults: pool vazio
    # (só a seed do segmento) e cota padrão; a composição injeta os reais.
    terms: SegmentTermRepository = field(default_factory=NoOpSegmentTermRepository)
    env: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass
class DiscoverResult:
    """Resultado da fase de rede para uma plataforma (Task 6).

    Fronteira da concorrência (Etapa 0 Task 4): CollectorError E qualquer
    outra Exception viram `error` aqui — nenhuma exceção esperada escapa
    para cancelar as irmãs (paridade com o research).     Só cancelamento
    (BaseException) propaga. Exceção inesperada vira
    `CollectorError("internal_error", ...)` com o original em `__cause__`.
    discover_ms é medido nesta fase e transportado; started_at abre esta
    fase (antes do acquire) e é transportado para a run; finished_at e
    persist_ms são medidos na fase de persistência de collect().
    """

    platform: str
    found: list[CandidateVideo] | None
    error: CollectorError | None
    gate_hits: int  # desta chamada (Etapa 1 Task 9)
    quota_used: int  # unidades de cota de API desta chamada (revisão B2)
    discover_ms: int | None
    started_at: datetime
    # R6/R13: atribuição e log das buscas desta plataforma (vazio no caminho
    # legado `discover()` e na web, que têm proveniência própria).
    discoveries: list[Discovery] = field(default_factory=list[Discovery])
    query_runs: list[SearchQueryRun] = field(default_factory=list[SearchQueryRun])


@dataclass
class CollectOutcome:
    ok: bool
    partial: bool
    counts: dict[str, int | bool]  # bool só no caso {"ja_em_execucao": True}
    runs: list[CollectRun] = field(default_factory=list[CollectRun])


def _query_run(
    query: SearchQuery,
    platform: str,
    results: int,
    quota_used: int,
    started_at: datetime,
    finished_at: datetime,
    error_code: str | None,
) -> SearchQueryRun:
    """Uma linha do log de buscas; `kept` é preenchido depois do dedupe."""
    return SearchQueryRun(
        platform=platform,
        query_id=query.query_id,
        term=query.term,
        origin=query.origin,
        results=results,
        kept=0,
        quota_used=quota_used,
        error_code=error_code,
        started_at=started_at,
        finished_at=finished_at,
    )


def _unattributed(candidate: CandidateVideo) -> Discovery:
    """Descoberta do caminho legado (`discover()`), sem termo que a atribua."""
    return Discovery(
        candidate=candidate,
        term="",
        origin="unattributed",
        query_id=None,
        result_position=None,
        run_kind="collect",
    )


def _dedup_and_mark_kept(
    discoveries: list[Discovery], runs: list[SearchQueryRun]
) -> list[CandidateVideo]:
    """Deduplica candidatos (primeiro vence) e marca o `kept` de cada busca.

    O mesmo vídeo pode vir de termos diferentes: o `upsert` recebe cada um
    uma vez, mas `video_discoveries` guarda a atribuição a TODOS os termos
    que o trouxeram. `kept` de cada busca = quantos candidatos dela foram os
    primeiros no dedupe desta coleta.
    """
    seen: set[tuple[str, str]] = set()
    deduped: list[CandidateVideo] = []
    kept: dict[str, int] = {}
    for d in discoveries:
        key = (d.candidate.platform, d.candidate.platform_video_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(d.candidate)
        if d.query_id is not None:
            kept[d.query_id] = kept.get(d.query_id, 0) + 1
    for run in runs:
        run.kept = kept.get(run.query_id, 0)
    return deduped


async def _search_queries(
    searcher: TermSearcher,
    segment: SegmentConfig,
    queries: list[SearchQuery],
    limit: int,
    deps: CollectDeps,
    discoveries: list[Discovery],
    runs: list[SearchQueryRun],
) -> tuple[int, CollectorError | None]:
    """Executa as buscas EM SÉRIE; cada uma com `counting()` próprio.

    Erro de contrato (cota, rede) interrompe as buscas seguintes da mesma
    plataforma — 429/403 de cota não melhora na próxima chamada — e o que já
    veio fica acumulado em `discoveries`/`runs` (passados pelo chamador, para
    não se perder nem numa exceção fora do contrato). Devolve (cota total,
    erro).
    """
    quota = 0
    for query in queries:
        started = deps.clock()
        with counting() as counters:
            try:
                await deps.limiter.acquire(searcher.platform)
                found = await searcher.search_term(segment, query, limit)
            except Exception as exc:
                # Fora do contrato (bug no parser etc.) vira `internal_error`
                # AQUI, com a busca no log e o que já veio preservado para o
                # dedupe — antes escapava para `_discover_one` e os candidatos
                # iam ao upsert SEM dedupe (revisão QA do bloco 2).
                if isinstance(exc, CollectorError):
                    err = exc
                else:
                    logger.exception(
                        "busca fora do contrato: %s/%s", segment.slug, searcher.platform
                    )
                    err = CollectorError("internal_error", searcher.platform, str(exc))
                    err.__cause__ = exc
                quota += counters.quota_used
                runs.append(
                    _query_run(
                        query,
                        searcher.platform,
                        0,
                        counters.quota_used,
                        started,
                        deps.clock(),
                        err.code,
                    )
                )
                return quota, err
        quota += counters.quota_used
        discoveries += found
        runs.append(
            _query_run(
                query,
                searcher.platform,
                len(found),
                counters.quota_used,
                started,
                deps.clock(),
                None,
            )
        )
    return quota, None


async def _pool_snapshot(segment: Segment, platform: str, deps: CollectDeps) -> PoolSnapshot:
    """Retrato do pool para as vagas (R5): cada lista na ordem do seu critério.

    Pool sem seeds (1ª coleta antes do 1º research) usa as keywords do
    segmento. As seeds do pool não têm `kw_rank`: ordem alfabética, estável
    (pré-registrada).
    """
    terms = deps.terms
    seeds = [t.term for t in await terms.top_by_origin(segment.id, "seed", 200)]
    by_trend = await terms.top_by_origin(segment.id, "suggest", 50, order_by="trend_score")
    hashtags = await terms.top_by_origin(
        segment.id, f"hashtag:{platform}", 50, order_by="engagement_score"
    )
    return PoolSnapshot(
        # v2: keyword em outra língua num segmento pt-BR sempre voltava vazia
        seeds=searchable_seeds(tuple(seeds or segment.keywords), wanted_language(segment.search)),
        suggest_by_rank=tuple(t.term for t in await terms.top_by_origin(segment.id, "suggest", 50)),
        suggest_by_trend=tuple(t.term for t in by_trend if t.trend_score is not None),
        hashtags=tuple(t.term for t in hashtags),
    )


async def _discover_one(
    collector: Collector,
    segment: SegmentConfig,
    limit: int,
    deps: CollectDeps,
    *,
    queries: Sequence[SearchQuery] = (),
) -> DiscoverResult:
    """Fase de rede para uma plataforma: limiter.acquire + discover/busca.

    Devolve DiscoverResult em vez de levantar — a fase de persistência
    decide status/counts/runs. Exceção fora do contrato vira
    `internal_error` (defesa em profundidade); só cancelamento propaga.
    gate_hits é contado POR CHAMADA (`counting()`, Etapa 1 Task 9): o
    collector é singleton e segmentos concorrentes o compartilham, então o
    delta do atributo somaria hits de outros segmentos.

    R6: plataforma em `deps.term_searchers` faz uma busca por termo (com
    atribuição); as demais seguem o `discover()` legado (descobertas
    `unattributed`; a web não gera atribuição — tem a proveniência própria
    em `web_search_hits`).
    """
    platform = collector.platform
    found: list[CandidateVideo] | None = None
    error: CollectorError | None = None
    discover_start: datetime | None = None
    discoveries: list[Discovery] = []
    query_runs: list[SearchQueryRun] = []
    quota = 0
    searcher = deps.term_searchers.get(platform)
    # Teto por plataforma (Etapa 0 Task 7): a vaga cobre acquire + discover.
    # started_at é lido DENTRO da vaga (mas antes do acquire) para a duração
    # não contar espera na fila. Comparar com o baseline por discover_ms +
    # persist_ms: finished_at sai após a rede de TODAS as irmãs (review).
    async with deps.platform_slots.slot(platform):
        started_at = deps.clock()
        with counting() as counters:
            try:
                await deps.limiter.acquire(platform)
                discover_start = deps.clock()
                if searcher is not None:
                    quota, error = await _search_queries(
                        searcher,
                        segment,
                        list(queries),
                        limit,
                        deps,
                        discoveries,
                        query_runs,
                    )
                    found = _dedup_and_mark_kept(discoveries, query_runs)
                else:
                    found = await collector.discover(segment, limit)
                    if platform != "web":  # web tem proveniência própria (F1)
                        discoveries = [_unattributed(c) for c in found]
            except CollectorError as exc:
                error = exc
                if discover_start is not None:  # erro do discover, não do acquire
                    await report_discover(deps.health, platform, exc.code)
            except Exception as exc:
                # run só guarda error_code: a causa vai para o log (Task 8.1)
                logger.exception("discover fora do contrato: %s/%s", segment.slug, platform)
                error = CollectorError("internal_error", platform, str(exc))
                error.__cause__ = exc
            else:
                # breaker: sucesso só se NENHUMA busca falhou (R6)
                await report_discover(deps.health, platform, error.code if error else None)
        # discover_ms só existe se o acquire passou (falha no limiter = None)
        discover_ms = None if discover_start is None else _ms_between(discover_start, deps.clock())
        return DiscoverResult(
            platform=platform,
            found=found,  # só atribuído no sucesso: erro ⇒ None
            error=error,
            gate_hits=counters.gate_hits,
            # a busca conta a cota em `counting()` próprio (aninhado), então
            # o total vem da soma das buscas, não do contador externo
            quota_used=quota if searcher is not None else counters.quota_used,
            discover_ms=discover_ms,
            started_at=started_at,
            discoveries=discoveries,
            query_runs=query_runs,
        )


async def collect(
    segment_slug: str, platforms: list[str], limit: int, deps: CollectDeps
) -> CollectOutcome:
    if not await deps.lock.acquire_once(f"collect:{segment_slug}"):
        return CollectOutcome(ok=False, partial=False, counts={"ja_em_execucao": True})
    try:
        segment = await deps.segments.get_by_slug(segment_slug)
        counts: dict[str, int | bool] = {
            "found": 0,
            "inserted": 0,
            "updated": 0,
            "failed": 0,
            # candidatos revertidos pelo savepoint: semântica separada de
            # "failed" (plataformas com erro) para a CLI não mentir
            "skipped": 0,
            # R6/R13: atribuição e log de buscas
            "discoveries": 0,
            "searches": 0,
            "searches_empty": 0,
            "unattributed": 0,
        }
        runs: list[CollectRun] = []
        errors = 0
        # Fase de rede (Task 7 + Etapa 0 Task 4 + Etapa 2 Task 12):
        # bounded_gather = TaskGroup + semáforo, resultado na ordem de
        # deps.collectors (não na de término). _discover_one captura TUDO na
        # própria fronteira — nenhuma exceção esperada escapa para cancelar
        # as irmãs; só cancelamento externo aborta o grupo.
        wanted = [c for c in deps.collectors if c.platform in platforms]
        # R5: as buscas de cada plataforma com `TermSearcher` saem das vagas do
        # pool, montadas ANTES da fase de rede (leitura na mesma sessão, em
        # série — a fase concorrente não toca o banco).
        run_index = run_index_for(deps.clock())
        queries_by_platform: dict[str, list[SearchQuery]] = {}
        for c in wanted:
            if c.platform in deps.term_searchers:
                queries_by_platform[c.platform] = plan_queries(
                    await _pool_snapshot(segment, c.platform, deps),
                    c.platform,
                    run_index,
                    searches_per_collect(c.platform, deps.env),
                )
        counts["searches_planned"] = sum(len(q) for q in queries_by_platform.values())
        discoveries = await bounded_gather(
            wanted,
            deps.max_concurrent_discoveries,
            lambda collector: _discover_one(
                collector,
                segment,
                limit,
                deps,
                queries=queries_by_platform.get(collector.platform, ()),
            ),
        )
        # Fase de persistência (sempre sequencial: mesma sessão Postgres).
        for result in discoveries:
            run = CollectRun(
                collector=result.platform, segment_id=segment.id, started_at=result.started_at
            )
            run.discover_ms = result.discover_ms
            run.quota_used = result.quota_used
            # Sem try/finally (Etapa 1, /code-review): erro fora do contrato
            # (infra — a sessão fica inutilizável) PROPAGA original; gravar a
            # run num `finally` levantaria PendingRollbackError e mascararia
            # a causa. A UoW do segmento reverte tudo, então não há run a
            # gravar nesse caminho.
            #
            # R6: há o que persistir quando a rede trouxe candidatos (found)
            # ou quando a fase de busca gerou log/atribuição — mesmo com erro
            # parcial de busca, o que já veio é gravado e a run vira failed.
            has_data = result.found is not None or bool(result.query_runs)
            if result.error is not None and not has_data:
                exc = result.error
                run.status = "blocked" if exc.code == "blocked" else "failed"
                run.error_code = exc.code
                run.failed = 1  # Fase H: explícito no campo numérico
                errors += 1
                counts["failed"] = int(counts["failed"]) + 1
            else:
                # invariante: sem erro, found nunca é None (a fronteira de
                # _discover_one garante); com erro parcial, cai no que veio
                candidates = (
                    result.found
                    if result.found is not None
                    else [d.candidate for d in result.discoveries]
                )
                hits: list[WebSearchHit] = []
                if result.platform == "web":
                    candidates, hits = partition_web(candidates)
                try:
                    persist_start = deps.clock()
                    stats = await deps.videos.upsert_candidates(
                        segment.id, result.platform, candidates
                    )
                    if hits:
                        await deps.web_hits.record(segment.id, hits)
                    written = await deps.discoveries.record(segment.id, result.discoveries)
                    await deps.search_log.record_many(segment.id, result.query_runs)
                except CollectorError as exc:
                    run.status = "blocked" if exc.code == "blocked" else "failed"
                    run.error_code = exc.code
                    run.failed = 1
                    errors += 1
                    counts["failed"] = int(counts["failed"]) + 1
                else:
                    run.persist_ms = _ms_between(persist_start, deps.clock())
                    discarded = (
                        len(result.found) - len(candidates) if result.found is not None else 0
                    )
                    run.found = len(candidates)
                    run.inserted, run.updated = stats.inserted, stats.updated
                    # descartados pelo filtro somam em `skipped` (auditável)
                    run.skipped = stats.skipped + discarded
                    counts["found"] = int(counts["found"]) + len(candidates)
                    counts["inserted"] = int(counts["inserted"]) + stats.inserted
                    counts["updated"] = int(counts["updated"]) + stats.updated
                    counts["skipped"] = int(counts["skipped"]) + stats.skipped + discarded
                    counts["discoveries"] = int(counts["discoveries"]) + written
                    counts["searches"] = int(counts["searches"]) + len(result.query_runs)
                    # vazia = respondeu sem resultado; busca que falhou não conta
                    counts["searches_empty"] = int(counts["searches_empty"]) + sum(
                        1 for r in result.query_runs if r.results == 0 and r.error_code is None
                    )
                    counts["unattributed"] = int(counts["unattributed"]) + sum(
                        1 for d in result.discoveries if d.origin == "unattributed"
                    )
                    if discarded:
                        counts["web_discarded"] = int(counts.get("web_discarded", 0)) + discarded
                        for reason, n in discard_counts(hits).items():
                            counts[f"web_discard:{reason}"] = (
                                int(counts.get(f"web_discard:{reason}", 0)) + n
                            )
                    # erro parcial de busca: dados persistidos + run failed
                    if result.error is not None:
                        exc = result.error
                        run.status = "blocked" if exc.code == "blocked" else "failed"
                        run.error_code = exc.code
                        run.failed = 1
                        errors += 1
                        counts["failed"] = int(counts["failed"]) + 1
                    else:
                        run.status = "ok"
            if result.gate_hits:
                counts[f"{result.platform}_gate_hits"] = result.gate_hits
            run.finished_at = deps.clock()
            await deps.runs.record(run)
            runs.append(run)
        ok = errors == 0
        return CollectOutcome(
            ok=ok,
            partial=(not ok and (int(counts["inserted"]) + int(counts["updated"]) > 0)),
            counts=counts,
            runs=runs,
        )
    finally:
        # SEM release o lock eterno bloqueia a próxima execução — bug clássico
        await deps.lock.release(f"collect:{segment_slug}")
