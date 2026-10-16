"""Guarda de regressao: a imagem do worker precisa carregar `segments/`.

O `FileWebQueries` le `segments/web-queries/<slug>.q1.yaml` em runtime e o
worker nao tem bind-mount. Se o estagio `worker` parar de copiar `segments/`,
a F1 degrada em silencio para o legado de 1 consulta (ver docs/HANDOFF.md).
"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / ".devcontainer" / "Dockerfile"


def _stage(text: str, name: str) -> str:
    """Linhas do estagio `FROM ... AS <name>` ate o proximo `FROM` ou o fim."""
    lines = text.splitlines()
    marker = f"AS {name.upper()}"
    start = next(i for i, line in enumerate(lines) if line.strip().upper().endswith(marker))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip().upper().startswith("FROM "):
            break
        body.append(line)
    return "\n".join(body)


def test_worker_stage_copies_segments() -> None:
    worker = _stage(DOCKERFILE.read_text(encoding="utf-8"), "worker")
    copied = [
        line.strip() for line in worker.splitlines() if line.strip().upper().startswith("COPY")
    ]
    assert any("segments" in line for line in copied), (
        f"o estagio worker precisa de `COPY segments /app/segments` (F1); COPYs atuais: {copied}"
    )
