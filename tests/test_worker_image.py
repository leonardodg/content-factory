"""Guarda de regressao: a imagem do worker precisa carregar `segments/`.

O `FileWebQueries` le `segments/web-queries/<slug>.q1.yaml` em runtime e o
worker nao tem bind-mount. Se o estagio `worker` parar de copiar `segments/`,
a F1 degrada em silencio para o legado de 1 consulta (ver docs/HANDOFF.md).
"""

import subprocess
import sys
from pathlib import Path

from content_factory.app.web_queries_plan import write_protocol
from content_factory.domain.web_queries import (
    INTENT_LEXICON_VERSION,
    QUERY_PROTOCOL_VERSION,
    PlannedQuery,
    WebQueryProtocol,
)
from content_factory.infra.collectors.web_queries import protocol_path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / ".devcontainer" / "Dockerfile"
PROBE = REPO_ROOT / "scripts" / "check_worker_segments.py"
SMOKE = REPO_ROOT / "scripts" / "smoke_worker_segments.sh"
SLUGS = ("velas-artesanais", "sabonetes-artesanais", "resina-artesanal")


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


def _run_probe(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROBE)], cwd=cwd, capture_output=True, text=True, check=False
    )


def _write_protocols(base: Path) -> None:
    for slug in SLUGS:
        protocol = WebQueryProtocol(
            segment_slug=slug,
            version=QUERY_PROTOCOL_VERSION,
            seed=1,
            lexicon_version=INTENT_LEXICON_VERSION,
            queries=[PlannedQuery("q1:aprender:00", "como fazer", "aprender")],
        )
        write_protocol(protocol_path(base / "segments" / "web-queries", slug), protocol)


def test_probe_fails_without_protocols(tmp_path: Path) -> None:
    # a imagem de hoje nao tem segments/web-queries/: o probe PRECISA falhar
    result = _run_probe(tmp_path)
    assert result.returncode != 0
    assert "FALHA" in result.stdout


def test_probe_passes_with_protocols(tmp_path: Path) -> None:
    _write_protocols(tmp_path)
    result = _run_probe(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_smoke_script_pipes_probe_into_container() -> None:
    script = SMOKE.read_text(encoding="utf-8")
    # `-i`: sem ele o stdin nao chega ao python e o container sai 0 sem checar
    assert "--rm -i" in script
    # `uv run python`: o python do sistema nao enxerga o content_factory
    assert '"$IMAGE" run python -' in script
    assert "check_worker_segments.py" in script


def test_chromium_do_worker_e_o_da_versao_travada_do_playwright() -> None:
    """QA 17/10: `uv pip install --system playwright` instalava o Playwright
    MAIS RECENTE e baixava o Chromium dele (1248), mas o worker roda o
    Playwright do `uv.lock` (1.63.0), que procura o 1243 — o TikTok nunca
    abria o navegador. O navegador tem de vir do Playwright do venv."""
    stage = _stage(DOCKERFILE.read_text(encoding="utf-8"), "worker")
    worker = "\n".join(ln for ln in stage.splitlines() if not ln.lstrip().startswith("#"))
    assert "pip install --system playwright" not in worker
    assert "/app/.venv/bin/playwright install" in worker
