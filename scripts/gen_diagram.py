"""Gera docs/diagram/ (er.mmd · er.html · er.svg · er.png · README.md).

    TEST_DATABASE_URL=... uv run python scripts/gen_diagram.py

Gate antes de gerar: `alembic check` (reusado do gen_datamodel). SVG/PNG
vêm do mermaid-cli via `npx -y @mermaid-js/mermaid-cli`; sem `npx` o
script falha com mensagem clara — gate não degrada silenciosamente.
"""

import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import gen_datamodel as dm
from sqlalchemy import Column, Table

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "diagram"
COMANDO = "uv run python scripts/gen_diagram.py"

_TYPE_MAP = {
    "STRING": "VARCHAR",
    "TEXT": "TEXT",
    "INTEGER": "INTEGER",
    "BIGINT": "BIGINT",
    "FLOAT": "FLOAT",
    "DATETIME": "TIMESTAMP",
    "BOOLEAN": "BOOL",
    "JSONB": "JSONB",
    "VECTOR": "VECTOR",
    "ARRAY": "ARRAY",
}


def _type(col: Column[object]) -> str:
    return _TYPE_MAP.get(type(col.type).__name__.upper(), "TEXT")


def _attr_keys(col: Column[object]) -> str:
    keys: list[str] = []
    if col.primary_key:
        keys.append("PK")
    if col.foreign_keys:
        keys.append("FK")
    return f" {', '.join(keys)}" if keys else ""


def _entity(name: str, table: Table) -> str:
    linhas = [f"  {name} {{"]
    for col in table.columns:
        linhas.append(f"    {_type(col)} {col.name}{_attr_keys(col)}")
    linhas.append("  }")
    return "\n".join(linhas)


def _relationships() -> list[str]:
    """`referenced ||--o{ referencing : "fk_col"`.

    Lado esquerdo: `||` se FK obrigatória, `|o` se nullable (cada linha do
    lado direito referencia no máximo 1 do esquerdo). Lado direito sempre
    `o{` — a FK não garante que toda linha referenciada tenha filhos.
    """
    rels: list[str] = []
    for name in sorted(dm.Base.metadata.tables):
        table = dm.Base.metadata.tables[name]
        for col in table.columns:
            for fk in col.foreign_keys:
                target = fk.column.table.name
                left = "||" if not col.nullable else "|o"
                rels.append(f'  {target} {left}--o{{ {name} : "{col.name}"')
    return sorted(rels)


def build_mmd() -> str:
    partes = ["erDiagram"]
    for name in sorted(dm.Base.metadata.tables):
        partes.append(_entity(name, dm.Base.metadata.tables[name]))
    partes.extend(_relationships())
    return "\n".join(partes) + "\n"


def _html(mmd: str) -> str:
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>ER — content-factory</title>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{ startOnLoad: true, theme: "neutral" }});
</script>
</head>
<body>
<pre class="mermaid">
{mmd}</pre>
</body>
</html>
"""


def _readme(hoje: str) -> str:
    return f"""# Diagrama ER — content-factory

!!! warning "Documento gerado — não edite à mão"
    Fonte: `Base.metadata` (modelos ORM) com gate `alembic check`.
    Regenere: `{COMANDO}` · leitura: {hoje}.

![Diagrama entity-relationship](er.svg)

- [PNG (2x)](er.png) · [HTML interativo](er.html) ·
  [Fonte mermaid (.mmd)](er.mmd)
- Colunas, tipos e notas: [data model](../data-model/README.md)
"""


def render_svgs(mmd_path: Path) -> None:
    for out_name in ("er.svg", "er.png"):
        proc = subprocess.run(
            [
                "npx",
                "-y",
                "@mermaid-js/mermaid-cli",
                "-i",
                str(mmd_path),
                "-o",
                str(OUT_DIR / out_name),
                "-s",
                "2",
                "-b",
                "white",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            sys.exit(f"\nmermaid-cli falhou ao gerar {out_name} — gate é gate")


def main() -> int:
    dm.alembic_check(dm.require_test_url())
    if shutil.which("npx") is None:
        sys.exit(
            "npx não encontrado — instale Node.js (>= 18) para gerar er.svg/er.png; "
            "não degradamos silenciosamente"
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mmd_path = OUT_DIR / "er.mmd"
    mmd_path.write_text(build_mmd(), encoding="utf-8")
    render_svgs(mmd_path)
    (OUT_DIR / "er.html").write_text(_html(mmd_path.read_text(encoding="utf-8")), encoding="utf-8")
    hoje = datetime.now(UTC).strftime("%Y-%m-%d")
    (OUT_DIR / "README.md").write_text(_readme(hoje), encoding="utf-8")
    print(f"gerados: {', '.join(sorted(p.name for p in OUT_DIR.iterdir()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
