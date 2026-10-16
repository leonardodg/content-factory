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
from content_factory.app.web_eval import WebEvalReport
from content_factory.app.web_rank import WebRankOutcome
from content_factory.app.web_sample import WebSampleRun
from content_factory.cli.output import dumps, envelope_err, envelope_ok, exit_code_for
from content_factory.domain.errors import CollectorError, DomainError, SegmentNotFound
from content_factory.domain.models import CandidateVideo, ResearchCampaign, Segment
from content_factory.domain.web_eval import Interval
from content_factory.domain.web_relevance import WebScore
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
        WebHitRepositoryImpl,
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
            web_hits=WebHitRepositoryImpl(session),
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


async def _ascore_sample(
    segment_slug: str, limit: int, out: Path, platform: str | None = None
) -> int:
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
        rows = await score_sample(segment_id=seg.id, limit=limit, repo=repo, platform=platform)
    write_score_csv(rows, out)
    return len(rows)


@app.command("score-sample")
def score_sample_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    limit: int = typer.Option(50, "--limit", min=1, max=500, help="máximo de vídeos"),
    out: str = typer.Option(..., "--out", help="caminho do CSV a gravar"),
    platform: str | None = typer.Option(
        None,
        "--platform",
        help="só uma plataforma (youtube, instagram…): scores de plataformas diferentes "
        "não são comparáveis",
    ),
) -> None:
    """Exporta o ranking atual em CSV para anotação manual (avaliacao_manual)."""
    try:
        n = asyncio.run(_ascore_sample(segment, limit, Path(out), platform))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("score-sample", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("score-sample", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("score-sample", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(dumps(envelope_ok("score-sample", rows=n, out=out, platform=platform)))
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


# --- web: proveniência, protocolo, score e avaliação (R #45) ---
# Plano: docs/ai-plans/2026-10-16-web-relevancia-potencial.md (F1-F4 na CLI).
# O score web tem tabela própria (`web_page_scores`) e NUNCA toca o
# `viral_score` dos vídeos.


async def _apropose_web_queries(
    segment_slug: str, directory: Path, vetoes: dict[str, str]
) -> tuple[str, Path, dict[str, int]]:
    """Lê os signals do Suggest do segmento e grava o protocolo `q1`."""
    from content_factory.app.web_queries_plan import build_protocol, write_protocol
    from content_factory.config import get_settings
    from content_factory.infra.collectors.web_queries import protocol_path
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        KeywordSignalRepositoryImpl,
        SegmentRepositoryImpl,
    )

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        signals = await KeywordSignalRepositoryImpl(session).top_for_segment(seg.id, None, 500)
    protocol = build_protocol(
        seg.slug, [s.keyword for s in signals], list(seg.keywords), vetoes=vetoes
    )
    path = protocol_path(directory, seg.slug)
    write_protocol(path, protocol)
    return seg.slug, path, dict(protocol.missing)


@app.command("web-queries")
def web_queries_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    propose: bool = typer.Option(
        False, "--propose", help="gera e grava o protocolo q1 (revisão do usuário)"
    ),
    veto: list[str] | None = typer.Option(  # noqa: B008 (typer exige a chamada no default)
        None, "--veto", help="frase do protocolo a vetar (repetível); exige --reason"
    ),
    reason: str = typer.Option("", "--reason", help="motivo do veto (registrado no protocolo)"),
    directory: str | None = typer.Option(
        None, "--dir", help="diretório do protocolo (default: segments/web-queries)"
    ),
) -> None:
    """Monta o protocolo fixo de consultas web `q1` do segmento (F1)."""
    if not propose:
        print(dumps(envelope_err("web-queries", "usage_error", "informe --propose")))
        raise typer.Exit(exit_code_for(ok=False))
    vetoes = {phrase: reason or "vetado pelo usuário" for phrase in (veto or [])}
    try:
        from content_factory.infra.collectors.web_queries import DEFAULT_QUERIES_DIR

        target = Path(directory) if directory else DEFAULT_QUERIES_DIR
        slug, path, missing = asyncio.run(_apropose_web_queries(segment, target, vetoes))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("web-queries", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("web-queries", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("web-queries", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    print(
        dumps(
            envelope_ok(
                "web-queries", segment=slug, path=str(path), missing=missing, vetoes=len(vetoes)
            )
        )
    )
    raise typer.Exit(exit_code_for(ok=True))


async def _aweb_rank(segment_slug: str, k: int, max_per_domain: int | None) -> WebRankOutcome:
    from content_factory.app.web_rank import web_rank
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        SegmentRepositoryImpl,
        WebScoreRepositoryImpl,
    )

    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        outcome = await web_rank(
            seg,
            WebScoreRepositoryImpl(session),
            now=datetime.now(UTC),
            k=k,
            max_per_domain=max_per_domain,
        )
        await session.commit()
        return outcome


def _brief(score: WebScore) -> dict[str, Any]:
    return {"video_id": score.video_id, "domain": score.domain, "score": score.score}


def _explained(score: WebScore) -> dict[str, Any]:
    return {**_brief(score), "components": score.components, "explanation": score.explanation}


@app.command("web-rank")
def web_rank_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    compare: bool = typer.Option(
        False, "--compare", help="inclui o ranking da linha de base congelada base-fase-h"
    ),
    explain: bool = typer.Option(
        False, "--explain", help="inclui componentes e explicação por página do topo"
    ),
    k: int = typer.Option(10, "--k", min=1, max=100, help="tamanho do topo"),
    max_per_domain: int | None = typer.Option(
        None, "--max-per-domain", min=1, help="limita páginas por domínio na lista final"
    ),
) -> None:
    """Score `web-v1` + linha de base, só na CLI (grava `web_page_scores`)."""
    try:
        outcome = asyncio.run(_aweb_rank(segment, k, max_per_domain))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("web-rank", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("web-rank", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("web-rank", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc

    render = _explained if explain else _brief
    body: dict[str, Any] = {
        "segment": outcome.segment,
        "scored": outcome.scored,
        "score_version": outcome.score_version,
        "k": k,
        "top_v1": [render(s) for s in outcome.top_v1],
        "domains_v1": len({s.domain for s in outcome.top_v1}),
    }
    if compare:
        body["top_base"] = [_brief(s) for s in outcome.top_base]
        body["domains_base"] = len({s.domain for s in outcome.top_base})
    print(dumps(envelope_ok("web-rank", **body)))
    raise typer.Exit(exit_code_for(ok=True))


async def _aweb_sample(
    segment_slug: str, seed: int, out: Path, allow_incomplete: bool
) -> WebSampleRun:
    from content_factory.app.web_sample import (
        queries_without_hits,
        web_sample,
        write_web_sample_csv,
    )
    from content_factory.config import get_settings
    from content_factory.infra.collectors.web_queries import (
        DEFAULT_QUERIES_DIR,
        load_protocol,
        protocol_path,
    )
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        SegmentRepositoryImpl,
        WebScoreRepositoryImpl,
    )

    as_of = datetime.now(UTC)
    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        repo = WebScoreRepositoryImpl(session)
        path = protocol_path(DEFAULT_QUERIES_DIR, seg.slug)
        missing: list[str] = []
        if path.exists():
            hits = await repo.hits_for_segment(seg.id, until=as_of)
            missing = queries_without_hits(load_protocol(path).queries, hits)
        if missing and not allow_incomplete:
            return WebSampleRun(rows=0, as_of=as_of, missing_queries=missing)
        items = await web_sample(seg, repo, seed=seed)
    write_web_sample_csv(items, out)
    return WebSampleRun(rows=len(items), as_of=as_of, missing_queries=missing)


@app.command("web-sample")
def web_sample_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    out: str = typer.Option(..., "--out", help="caminho do CSV cego a gravar"),
    seed: int = typer.Option(
        20261019, "--seed", help="seed do sorteio (gravada/auditável; default fixo)"
    ),
    allow_incomplete: bool = typer.Option(
        False,
        "--allow-incomplete",
        help="gera mesmo com consulta do protocolo q1 ainda sem coleta (registrar o motivo)",
    ),
) -> None:
    """Exporta a amostra cega (topo + aleatório + descartados) para anotação (F2).

    Recusa (`incomplete_cycle`) enquanto alguma consulta do protocolo q1 não
    tiver coleta. O `as_of` do envelope é o `--as-of` do `cf web-eval`.
    """
    try:
        run = asyncio.run(_aweb_sample(segment, seed, Path(out), allow_incomplete))
    except SegmentNotFound as exc:
        print(dumps(envelope_err("web-sample", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("web-sample", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("web-sample", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    if run.missing_queries and not allow_incomplete:
        message = "consultas do protocolo sem coleta: " + ", ".join(run.missing_queries)
        print(dumps(envelope_err("web-sample", "incomplete_cycle", message)))
        raise typer.Exit(exit_code_for(ok=False))
    print(
        dumps(
            envelope_ok(
                "web-sample",
                segment=segment,
                rows=run.rows,
                out=out,
                seed=seed,
                as_of=run.as_of.isoformat(),
                missing_queries=run.missing_queries,
            )
        )
    )
    raise typer.Exit(exit_code_for(ok=True))


async def _aweb_eval(segment_slug: str, csv_path: Path, as_of: datetime) -> WebEvalReport:
    from content_factory.app.web_eval import read_annotations, web_evaluate
    from content_factory.config import get_settings
    from content_factory.infra.db.base import get_session_factory
    from content_factory.infra.db.repositories import (
        SegmentRepositoryImpl,
        WebScoreRepositoryImpl,
    )

    annotations = read_annotations(csv_path)
    factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())
    async with factory() as session:
        seg = await SegmentRepositoryImpl(session).get_by_slug(segment_slug)
        return await web_evaluate(seg, WebScoreRepositoryImpl(session), annotations, as_of=as_of)


def _interval(interval: Interval) -> dict[str, Any]:
    return {"point": interval.point, "low": interval.low, "high": interval.high, "n": interval.n}


@app.command("web-eval")
def web_eval_cmd(
    segment: str = typer.Option(..., "--segment", help="slug do segmento"),
    csv_path: str = typer.Option(..., "--csv", help="CSV já anotado (web-sample)"),
    as_of: str = typer.Option(
        ...,
        "--as-of",
        help="data de corte da amostra com fuso (o `as_of` do envelope do web-sample)",
    ),
    explain: bool = typer.Option(False, "--explain", help="inclui os dois rankings de URL"),
) -> None:
    """Avaliação pareada base-fase-h x web-v1 com IC e veredito (F4)."""
    try:
        cutoff = datetime.fromisoformat(as_of)
    except ValueError as exc:
        print(dumps(envelope_err("web-eval", "usage_error", f"--as-of inválido: {as_of}")))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    if cutoff.tzinfo is None:
        print(dumps(envelope_err("web-eval", "usage_error", "--as-of precisa de fuso (+00:00)")))
        raise typer.Exit(exit_code_for(ok=False))
    try:
        report = asyncio.run(_aweb_eval(segment, Path(csv_path), cutoff))
    except FileNotFoundError as exc:
        print(dumps(envelope_err("web-eval", "file_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except SegmentNotFound as exc:
        print(dumps(envelope_err("web-eval", "segment_not_found", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except DomainError as exc:
        print(dumps(envelope_err("web-eval", "usage_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc
    except Exception as exc:
        print(dumps(envelope_err("web-eval", "internal_error", redact(str(exc)))))
        raise typer.Exit(exit_code_for(ok=False)) from exc

    body: dict[str, Any] = {
        "segment": report.segment,
        "kept_annotated": report.kept_annotated,
        "p5": _interval(report.p5),
        "p10": _interval(report.p10),
        "gain_p10": _interval(report.gain_p10),
        "coverage": report.coverage,
        "verdict": report.verdict,
        "correlation": report.correlation,
        "useful_top10": report.useful_top10,
    }
    if explain:
        body["ranking_v1"] = report.ranking_v1
        body["ranking_base"] = report.ranking_base
    print(dumps(envelope_ok("web-eval", **body)))
    raise typer.Exit(exit_code_for(ok=True))


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
            coverage_window=await reader.metric_coverage(seg.id, since=since),
            web_discards=await reader.discard_stats(seg.id, since),
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
