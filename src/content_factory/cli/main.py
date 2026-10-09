import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from content_factory.app.collect import CollectDeps, CollectOutcome, collect
from content_factory.app.ports import CollectRunRepository, SegmentRepository
from content_factory.app.score import ScoreDeps, ScoreOutcome, score
from content_factory.cli.output import dumps, envelope_err, envelope_ok, exit_code_for
from content_factory.domain.errors import CollectorError, DomainError, SegmentNotFound
from content_factory.domain.models import CandidateVideo
from content_factory.infra.redact import redact

app = typer.Typer(no_args_is_help=True)
segment_app = typer.Typer()
app.add_typer(segment_app, name="segment")


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

    if outcome.partial:
        print(dumps(envelope_ok("collect", partial=True, **outcome.counts)))
        raise typer.Exit(exit_code_for(ok=True, partial=True))
    if outcome.ok:
        print(dumps(envelope_ok("collect", **outcome.counts)))
        raise typer.Exit(exit_code_for(ok=True))
    if "ja_em_execucao" in outcome.counts:
        code = "ja_em_execucao"
        message = f"coleta já em execução para o segmento {segment}"
    else:
        code = next((r.error_code for r in outcome.runs if r.error_code), "internal_error")
        message = f"coleta falhou em {int(outcome.counts.get('failed', 0))} plataforma(s)"
    print(dumps(envelope_err("collect", code, message)))
    raise typer.Exit(exit_code_for(ok=False))


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

    if outcome.partial:
        print(dumps(envelope_ok("score", partial=True, scored=outcome.scored)))
        raise typer.Exit(exit_code_for(ok=True, partial=True))
    if outcome.ok:
        print(dumps(envelope_ok("score", scored=outcome.scored)))
        raise typer.Exit(exit_code_for(ok=True))
    msg = f"falha ao gravar scores de {segment}"
    print(dumps(envelope_err("score", "internal_error", redact(msg))))
    raise typer.Exit(exit_code_for(ok=False))


@app.command("ping")
def ping() -> None:
    """Healthcheck do CLI (sem banco)."""
    print(dumps(envelope_ok("ping")))
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


def main() -> None:
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
