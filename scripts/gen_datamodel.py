"""Gera docs/data-model/README.md a partir do metadata do Alembic/modelos.

Nunca edite o output na mão — gere de novo:

    TEST_DATABASE_URL=... uv run python scripts/gen_datamodel.py

Gate antes de gerar: `alembic check` (modelo ≡ migrations ≡ banco).
O engine criado em TEST_DATABASE_URL valida a conexão; o conteúdo vem do
`Base.metadata` (garantido ≡ banco pelo gate).
"""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Column, create_engine, text
from sqlalchemy.sql.schema import Table

from content_factory.infra.db.orm import Base

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data-model" / "README.md"
COMANDO = "uv run python scripts/gen_datamodel.py"


def require_test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        sys.exit(
            "TEST_DATABASE_URL ausente — exporte a URL do banco de teste "
            "(ex.: postgresql+psycopg://contentfactory:localdev@localhost:5432/contentfactory)"
        )
    return url


def alembic_check(test_url: str) -> None:
    """Reprova se modelo ≡ migrations divergir — gate é gate."""
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": test_url},
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit(
            "\nalembic check falhou — aplique/corrija migrations "
            "('uv run alembic upgrade head' e revise o diff) antes de gerar docs"
        )


def validate_engine(test_url: str) -> None:
    engine = create_engine(test_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        sys.exit(f"TEST_DATABASE_URL inacessível: {exc}")
    finally:
        engine.dispose()


def _is_composite_pk(table: Table, col: Column[object]) -> bool:
    return bool(col.primary_key) and len(table.primary_key.columns) > 1


def _notes(table: Table, col: Column[object]) -> str:
    notes: list[str] = []
    fks = list(col.foreign_keys)
    if fks:
        targets = ", ".join(fk.target_fullname for fk in fks)
        suffix = " (FK diferida — use_alter)" if any(fk.use_alter for fk in fks) else ""
        notes.append(f"**FK → {targets}**{suffix}")
    if col.primary_key:
        notes.append("PK")
    if col.unique:
        notes.append("UNIQUE")
    if not col.primary_key:
        notes.append("nullable" if col.nullable else "obrigatório")
    if col.server_default is not None:
        notes.append(f"default: `{col.server_default}`")
    if col.index:
        notes.append("indexado")
    return " · ".join(notes) if notes else "—"


def _fk_declarada(col: Column[object]) -> bool:
    return bool(col.foreign_keys)


def render() -> str:
    hoje = datetime.now(UTC).strftime("%Y-%m-%d")
    linhas = [
        "# Data model — content-factory",
        "",
        '!!! warning "Documento gerado — não edite à mão"',
        "    Fonte: `Base.metadata` (modelos ORM) com gate `alembic check`.",
        f"    Regenere: `{COMANDO}` · leitura: {hoje}.",
        "",
        "Convenções: **FK → alvo** = constraint declarada no banco; "
        "`nullable` = aceita vazio (métricas inexpostas por plataforma); "
        "`VECTOR(1536)` = coluna para a fase IA (hoje sempre NULL).",
        "",
        "## Tabelas, campo a campo",
        "",
    ]
    sem_fk: list[str] = []
    for name in sorted(Base.metadata.tables):
        table = Base.metadata.tables[name]
        linhas += [f"### {name}", "", "| Campo | Tipo | Nota |", "|---|---|---|"]
        for col in table.columns:
            linhas.append(f"| `{col.name}` | `{str(col.type).upper()}` | {_notes(table, col)} |")
            if (
                col.name.endswith("_id")
                and not _fk_declarada(col)
                and not _is_composite_pk(table, col)
            ):
                sem_fk.append(f"{name}.{col.name}")
        linhas.append("")
    linhas += ["## Vínculos", ""]
    if sem_fk:
        linhas.append(
            "Vínculo só-por-código (sem FK declarada) — conferir intenção: "
            + ", ".join(f"`{v}`" for v in sem_fk)
        )
    else:
        linhas.append(
            "Nenhum vínculo só-por-código: toda referência entre tabelas tem "
            "**FK declarada** (constraint), inclusive o ciclo "
            "`videos ↔ transcripts` (FK diferida com `use_alter`)."
        )
    linhas.append("")
    return "\n".join(linhas)


def main() -> int:
    test_url = require_test_url()
    alembic_check(test_url)
    validate_engine(test_url)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"gerado: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
