from pathlib import Path

import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from content_factory.cli.output import dumps, envelope_err, envelope_ok, exit_code_for
from content_factory.domain.errors import DomainError

app = typer.Typer(no_args_is_help=True)
segment_app = typer.Typer()
app.add_typer(segment_app, name="segment")


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
        print(dumps(envelope_ok("segment.load", slug=cfg.slug, id=seg_id)))
        raise typer.Exit(exit_code_for(ok=True))
    except DomainError as exc:
        print(dumps(envelope_err("segment.load", "invalid_segment", str(exc))))
        raise typer.Exit(exit_code_for(ok=False)) from exc


@app.command("ping")
def ping() -> None:
    """Healthcheck do CLI (sem banco)."""
    print(dumps(envelope_ok("ping")))
    raise typer.Exit(exit_code_for(ok=True))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
