import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from content_factory.app.collect import CollectDeps, CollectOutcome, collect
from content_factory.app.ports import (
    CollectRunRepository,
    OpsReportReader,
    ResearchCampaignRepository,
    SegmentRepository,
)
from content_factory.app.results import collect_result, score_result
from content_factory.app.score import ScoreDeps, ScoreOutcome, score
from content_factory.cli.output import dumps, envelope_err, envelope_ok, exit_code_for
from content_factory.domain.errors import CollectorError, DomainError, SegmentNotFound
from content_factory.domain.models import CandidateVideo, ResearchCampaign, Segment
from content_factory.infra.logs import configure_logging
from content_factory.infra.redact import redact

app = typer.Typer(no_args_is_help=True)


def _emit(command: str, result: dict[str, Any]) -> NoReturn:
    """C3: envelope = `command` + o resultado do caso de uso (o mesmo do worker).

    Exit: 0 ok, 2 ok parcial, 1 falha (`error.code` obrigatório).
    """
    print(dumps({"ok": result["ok"], "command": command, **result}))
    raise typer.Exit(exit_code_for(ok=bool(result["ok"]), partial=bool(result.get("partial"))))


segment_app = typer.Typer()
app.add_typer(segment_app, name="segment")
research_app = typer.Typer(no_args_is_help=True)
app.add_typer(research_app, name="research")


def _usage_command(exc: typer.TyperException) -> str:
    ctx: Any = getattr(exc, "ctx", None)
    command_path: Any = getattr(ctx, "command_path", None)
    if isinstance(command_path, str):
        parts = command_path.split()
        if len(parts) > 1:
            return ".".join(parts[1:])
    return "cf"


def _usage_message(exc: typer.TyperException) -> str:
    return exc.format_message() or "uso inválido (execute cf --help)"


@segment_app.command("load")
def segment_load(path: Path) -> None:
    """Valida YAML de segmento e grava no banco."""
    try:
        import asyncio

        from content_factory.app.segment import load_segment_file, upsert_segment
        from content_factory.config import get_settings
        from content_factory.infra.db.base import get_session_factory
        from content_factory.infra.db.repositories import SegmentRepositoryImpl

        cfg = load_segment_file(path)  # validação primeiro — sem DB na frente
        factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())

        async def _save() -> int:
            async with factory() as s:
                seg = await upsert_segment(SegmentRepositoryImpl(s), path)
                await s.commit()
                return seg.id

        seg_id = asyncio.run(_save())
    except DomainError as exc:
        print(dumps(envelope_err("segment.load", "invalid_segment", str(exc))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("segment.load", "internal_error", str(exc))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("segment.load", slug=cfg.slug, id=seg_id)))
    raise typer.Exit(exit_code_for(ok=True))


async def _segment_platforms(segment_slug: str) -> list[str]:
    """Fallback do `--platform` vazio: as plataformas declaradas no segmento."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import SegmentRepositoryImpl

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        return seg.platforms


@asynccontextmanager
async def _collect_deps(platforms: list[str]) -> AsyncGenerator[CollectDeps]:
    """Deps reais do `collect`: registry de collectors + Postgres + Redis."""
    from content_factory.config import get_settings
    from content_factory.infra.collectors import register_defaults, registry
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        CollectRunRepositoryImpl,
        SegmentRepositoryImpl,
        VideoRepositoryImpl,
    )
    from content_factory.infra.lock import RedisLock
    from content_factory.infra.ratelimit import RedisRateLimiter
    from content_factory.infra.redis_ops import redis_client

    settings = get_settings()
    register_defaults(settings)  # composition root: worker já fazia o mesmo no startup
    factory: async_sessionmaker[AsyncSession] = get_session_factory(settings)
    async with redis_client(settings.redis_url) as client, factory() as session:
        yield CollectDeps(
            collectors=registry.resolve(platforms),
            videos=VideoRepositoryImpl(session),
            segments=SegmentRepositoryImpl(session),
            runs=CollectRunRepositoryImpl(session),
            limiter=RedisRateLimiter(client),
            lock=RedisLock(client),
        )
        await session.commit()


async def _acollect(segment_slug: str, platforms: list[str], limit: int) -> CollectOutcome:
    if not platforms:
        platforms = await _segment_platforms(segment_slug)
    async with _collect_deps(platforms) as deps:
        return await collect(segment_slug, platforms, limit, deps)


@app.command("collect")
def collect_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    platform: str | None = typer.Option(
        None,
        "--platform",
        help="plataformas separadas por vírgula (default: as do segmento)",
    ),
    limit: int = typer.Option(10, "--limit", min=1, max=50, help="máximo por plataforma"),
) -> None:
    """Coleta candidatos → upsert idempotente + auditoria em collect_runs."""
    platforms = [p.strip() for p in platform.split(",") if p.strip()] if platform else []
    try:
        outcome = asyncio.run(_acollect(segment, platforms, limit))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("collect", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except CollectorError as exc:
        print(dumps(envelope_err("collect", exc.code, redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:  # ex.: plataforma fora do registry = erro de uso
        print(dumps(envelope_err("collect", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("collect", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc

    _emit("collect", collect_result(outcome))


async def _aingest(url: str, segment: str | None) -> CandidateVideo:
    # composition root: injeta a guarda de infra (app/ não conhece infra);
    # import sob demanda (padrão segment_load) — seam monkeypatchável nos testes
    from content_factory.app.ingest import ingest_url
    from content_factory.infra.ssrf import assert_public_http

    cand = await ingest_url(url, guard=assert_public_http)
    if segment is not None:
        await _ingest_save(segment, cand)
    return cand


async def _ingest_save(segment_slug: str, cand: CandidateVideo) -> None:
    """Mesmo wire do Task 6: segmento por slug → upsert_candidates (idempotente)."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import SegmentRepositoryImpl, VideoRepositoryImpl

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        await VideoRepositoryImpl(session).upsert_candidates(seg.id, cand.platform, [cand])
        await session.commit()


@app.command("ingest")
def ingest(
    url: str = typer.Option(..., "--url", help="URL a baixar e enriquecer"),
    segment: str | None = typer.Option(
        None, "--segment", help="slug do segmento — se dado, faz upsert do candidato"
    ),
) -> None:
    """Baixa uma URL, extrai título/descrição e grava (se --segment)."""
    try:
        cand = asyncio.run(_aingest(url, segment))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("ingest", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:  # guarda SSRF (invalid_url) e erros de domínio
        print(dumps(envelope_err("ingest", "invalid_url", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("ingest", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("ingest", url=cand.url, title=cand.title)))
    raise typer.Exit(exit_code_for(ok=True))


async def _ascore(segment_slug: str) -> ScoreOutcome:
    """Mesmo wire do Task 6: segmento por slug → snapshots → upsert de video_scores."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        ScoreRepositoryImpl,
        SegmentRepositoryImpl,
        VideoRepositoryImpl,
    )

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        outcome = await score(
            ScoreDeps(
                videos=VideoRepositoryImpl(session),
                scores=ScoreRepositoryImpl(session),
                segment=seg,
                keywords=seg.keywords,
            )
        )
        await session.commit()
        return outcome


@app.command("score")
def score_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
) -> None:
    """Pontua os vídeos do segmento (métrica mais recente) em video_scores."""
    try:
        outcome = asyncio.run(_ascore(segment))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("score", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("score", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("score", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc

    # R5/C3: só contagem/categoria — mesmo resultado do score_job do worker
    _emit("score", score_result(outcome))


@app.command("ping")
def ping() -> None:
    """Healthcheck do CLI (sem banco)."""
    print(dumps(envelope_ok("ping")))
    raise typer.Exit(exit_code_for(ok=True))


# --- score-sample / score-correlation (Fase H, Task 8) ---


async def _ascore_sample(segment_slug: str, limit: int, out: Path) -> int:
    from content_factory.app.score_sample import score_sample, write_score_csv
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        ScoreSampleRepositoryImpl,
        SegmentRepositoryImpl,
    )

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        repo = ScoreSampleRepositoryImpl(session)
        rows = await score_sample(segment_id=seg.id, limit=limit, repo=repo)
    write_score_csv(rows, out)
    return len(rows)


@app.command("score-sample")
def score_sample_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    limit: int = typer.Option(50, "--limit", min=1, max=500, help="máximo de vídeos"),
    out: str = typer.Option(..., "--out", help="caminho do CSV a gravar"),
) -> None:
    """Exporta o ranking atual em CSV para anotação manual (avaliacao_manual)."""
    try:
        n = asyncio.run(_ascore_sample(segment, limit, Path(out)))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("score-sample", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("score-sample", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("score-sample", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("score-sample", rows=n, out=out)))
    raise typer.Exit(exit_code_for(ok=True))


@app.command("score-correlation")
def score_correlation_cmd(
    csv_path: str = typer.Option(..., "--csv", help="CSV já anotado (avaliacao_manual)"),
) -> None:
    """Spearman entre o rank do sistema (total) e a avaliação manual."""
    from content_factory.app.score_sample import score_correlation

    try:
        out = score_correlation(Path(csv_path))
    except FileNotFoundError as exc:
        print(dumps(envelope_err("score-correlation", "file_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("score-correlation", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    if out.ok and out.n is not None and out.spearman is not None:
        print(dumps(envelope_ok("score-correlation", n=out.n, spearman=out.spearman)))
        raise typer.Exit(exit_code_for(ok=True))
    code = out.error_code or "internal_error"
    print(dumps(envelope_err("score-correlation", code, out.message or "")))
    raise typer.Exit(exit_code_for(ok=False))


@asynccontextmanager
async def _status_deps() -> AsyncGenerator[tuple[SegmentRepository, CollectRunRepository]]:
    """Deps do `status`: só Postgres (sem Redis/registry — é leitura)."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        CollectRunRepositoryImpl,
        SegmentRepositoryImpl,
    )

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        yield SegmentRepositoryImpl(session), CollectRunRepositoryImpl(session)


async def _astatus(segment_slug: str) -> dict[str, int]:
    """Últimos 7 dias de `collect_runs` do segmento, agrupados por status."""
    async with _status_deps() as (segments, runs):
        seg = await segments.get_by_slug(segment_slug)
        since = datetime.now(UTC) - timedelta(days=7)
        return await runs.status_counts(seg.id, since)


@app.command("status")
def status_cmd(segment: str = typer.Option(..., "--segment", help="slug do segmento")) -> None:
    """Resumo das coletas do segmento nos últimos 7 dias (collect_runs)."""
    try:
        counts = asyncio.run(_astatus(segment))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("status", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("status", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("status", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("status", runs=counts)))
    raise typer.Exit(exit_code_for(ok=True))


@asynccontextmanager
async def _timings_deps() -> AsyncGenerator[tuple[SegmentRepository, CollectRunRepository]]:
    """Deps do `collect-timings`: só Postgres (sem Redis/registry — é leitura)."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        CollectRunRepositoryImpl,
        SegmentRepositoryImpl,
    )

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        yield SegmentRepositoryImpl(session), CollectRunRepositoryImpl(session)


async def _acollect_timings(segment_slug: str) -> dict[str, dict[str, float | int | None]]:
    """Agregado de discover_ms/persist_ms por plataforma nos últimos 7 dias."""
    async with _timings_deps() as (segments, runs):
        seg = await segments.get_by_slug(segment_slug)
        since = datetime.now(UTC) - timedelta(days=7)
        return await runs.timing_stats(seg.id, since)


@app.command("collect-timings")
def collect_timings_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
) -> None:
    """Baseline de duração por plataforma (collect_runs.discover_ms/persist_ms)."""
    try:
        timings = asyncio.run(_acollect_timings(segment))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("collect-timings", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("collect-timings", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("collect-timings", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("collect-timings", segment=segment, timings=timings)))
    raise typer.Exit(exit_code_for(ok=True))


@asynccontextmanager
async def _ops_deps() -> AsyncGenerator[
    tuple[SegmentRepository, CollectRunRepository, OpsReportReader]
]:
    """Deps do `ops-report` (C2): só Postgres, leitura."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        CollectRunRepositoryImpl,
        OpsReportReaderImpl,
        SegmentRepositoryImpl,
    )

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        yield (
            SegmentRepositoryImpl(session),
            CollectRunRepositoryImpl(session),
            OpsReportReaderImpl(session),
        )


async def _aops_report(segment_slug: str, hours: int) -> dict[str, Any]:
    from content_factory.app.ops_report import build_ops_report

    until = datetime.now(UTC)
    since = until - timedelta(hours=hours)
    async with _ops_deps() as (segments, runs, reader):
        seg = await segments.get_by_slug(segment_slug)
        return build_ops_report(
            await runs.runs_since(seg.id, since),
            await reader.metric_coverage(seg.id),
            await reader.scored_since(seg.id, since),
            since=since,
            until=until,
        )


@app.command("ops-report")
def ops_report_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    hours: int = typer.Option(24, "--hours", help="janela em horas (24 = relatório diário)"),
) -> None:
    """Relatório operacional (C2): runs por fonte, vazios, bloqueios, ticks e cobertura."""
    try:
        if hours < 1:
            raise DomainError(f"--hours precisa ser >= 1 (recebido {hours})")
        report = asyncio.run(_aops_report(segment, hours))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("ops-report", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("ops-report", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("ops-report", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("ops-report", segment=segment, hours=hours, report=report)))
    raise typer.Exit(exit_code_for(ok=True))


# --- research campaign commands (Fase D) ---


def _segment_id(slug: str) -> int:
    """Resolve slug → id para os comandos research (reusa o padrão existente)."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import SegmentRepositoryImpl

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())

    async def _get() -> int:
        async with factory() as session:
            return (await SegmentRepositoryImpl(session).get_by_slug(slug)).id

    return asyncio.run(_get())


def _research_campaigns() -> ResearchCampaignRepository:
    """Composition root: repositório real de campanhas (seam monkeypatchável)."""
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import ResearchCampaignRepositoryImpl

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())

    class _Repo:
        async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign:
            async with factory() as session:
                result = await ResearchCampaignRepositoryImpl(session).start(segment_id, ends_at)
                await session.commit()
                return result

        async def stop(self, segment_id: int) -> None:
            async with factory() as session:
                await ResearchCampaignRepositoryImpl(session).stop(segment_id)
                await session.commit()

        async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
            async with factory() as session:
                return await ResearchCampaignRepositoryImpl(session).active_for_segment(segment_id)

        async def active_segments(self) -> list[Segment]:
            async with factory() as session:
                return await ResearchCampaignRepositoryImpl(session).active_segments()

    return _Repo()


@research_app.command("start")
def research_start(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    weeks: int = typer.Option(4, "--weeks", min=1, max=52, help="duração da campanha em semanas"),
) -> None:
    """Inicia uma campanha de pesquisa (o worker só roda com campanha ativa)."""
    try:
        seg_id = _segment_id(segment)
        ends_at = datetime.now(UTC) + timedelta(weeks=weeks)
        campaigns = _research_campaigns()
        result = asyncio.run(campaigns.start(seg_id, ends_at))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("research.start", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("research.start", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("research.start", ends_at=result.ends_at.isoformat())))
    raise typer.Exit(exit_code_for(ok=True))


@research_app.command("stop")
def research_stop(segment: str = typer.Option(..., "--segment", help="slug do segmento")) -> None:
    """Para a campanha de pesquisa do segmento (ativas → inativas)."""
    try:
        seg_id = _segment_id(segment)
        campaigns = _research_campaigns()
        asyncio.run(campaigns.stop(seg_id))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("research.stop", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("research.stop", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("research.stop", segment=segment)))
    raise typer.Exit(exit_code_for(ok=True))


@research_app.command("status")
def research_status(segment: str = typer.Option(..., "--segment", help="slug do segmento")) -> None:
    """Mostra se há campanha ativa para o segmento."""
    try:
        seg_id = _segment_id(segment)
        campaigns = _research_campaigns()
        campaign = asyncio.run(campaigns.active_for_segment(seg_id))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("research.status", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("research.status", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    if campaign is not None:
        print(
            dumps(envelope_ok("research.status", active=True, ends_at=campaign.ends_at.isoformat()))
        )
    else:
        print(dumps(envelope_ok("research.status", active=False)))
    raise typer.Exit(exit_code_for(ok=True))


def main() -> None:
    # o app loga exceção fora do contrato (collect/research): sem handler
    # próprio o traceback sairia em claro pelo lastResort (spec §7)
    configure_logging(logging.WARNING)
    try:
        code = app(standalone_mode=False)
    except typer.TyperException as exc:
        print(dumps(envelope_err(_usage_command(exc), "usage_error", _usage_message(exc))))
        raise SystemExit(exit_code_for(ok=False)) from exc
    except typer.Abort:
        print(dumps(envelope_err("cf", "aborted", "operação abortada")))
        raise SystemExit(exit_code_for(ok=False)) from None
    if isinstance(code, int):
        raise SystemExit(code)


if __name__ == "__main__":
    main()
