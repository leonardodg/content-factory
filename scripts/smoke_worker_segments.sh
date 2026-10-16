#!/usr/bin/env bash
# Smoke test da imagem do worker (F1): confirma que `segments/` chegou a
# imagem e que o FileWebQueries carrega o protocolo `q1` de cada segmento.
#
# Rodar DEPOIS do build, no deploy (nao exige rebuild agora):
#   docker build -f .devcontainer/Dockerfile --target worker -t content-factory-worker .
#   scripts/smoke_worker_segments.sh [imagem]
#
# Sai != 0 se `segments/web-queries/` faltar, se algum <slug>.q1.yaml estiver
# ausente ou se o adaptador nao carregar o protocolo - sem isso o coletor cai
# no legado de 1 consulta em silencio (ver docs/HANDOFF.md).
#
# Duas armadilhas ja custaram caro aqui (revisao 16/10) e estao presas pelo
# teste `test_smoke_script_pipes_probe_into_container`:
#   - sem `-i`, o probe nao chega ao python e o container sai 0 sem checar nada;
#   - `--entrypoint python` usa o python do sistema e explode no import do
#     content_factory; e preciso `uv run python` (venv do projeto).
set -euo pipefail

IMAGE="${1:-content-factory-worker}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== smoke test do protocolo q1 na imagem: ${IMAGE} =="
docker run --rm -i --entrypoint uv "$IMAGE" run python - <"${HERE}/check_worker_segments.py"
