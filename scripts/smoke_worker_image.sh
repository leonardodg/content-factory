#!/usr/bin/env bash
# Smoke REAL da imagem do worker (revisão externa P1): roda o probe no Python
# da própria imagem. Mesmas armadilhas do smoke q1: `-i` (senão o probe não
# chega ao python e o container sai 0) e `uv run python` (venv do projeto).
#
#   docker build -f .devcontainer/Dockerfile --target worker -t content-factory-worker .
#   scripts/smoke_worker_image.sh [imagem]
set -euo pipefail

IMAGE="${1:-content-factory-worker}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== smoke da imagem do worker: ${IMAGE} =="
docker run --rm -i --entrypoint uv "$IMAGE" run --no-sync python - <"${HERE}/check_worker_image.py"
