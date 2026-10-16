#!/usr/bin/env bash
# Smoke test da imagem do worker (F1): confirma que `segments/` chegou à
# imagem e que o FileWebQueries carrega o protocolo `q1` de cada segmento.
#
# Rodar DEPOIS do build, no deploy (nao exige rebuild agora):
#   docker build -f .devcontainer/Dockerfile --target worker -t content-factory-worker .
#   scripts/smoke_worker_segments.sh [imagem]
#
# Sai != 0 se `segments/web-queries/` faltar, se algum <slug>.q1.yaml estiver
# ausente ou se o adaptador nao carregar o protocolo - sem isso o coletor cai
# no legado de 1 consulta em silencio (ver docs/HANDOFF.md).
set -euo pipefail

IMAGE="${1:-content-factory-worker}"

echo "== smoke test do protocolo q1 na imagem: ${IMAGE} =="
docker run --rm --entrypoint python "$IMAGE" - <<'PY'
import sys
from pathlib import Path

from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.web_queries import FileWebQueries

SLUGS = ["velas-artesanais", "sabonetes-artesanais", "resina-artesanal"]
root = Path("segments/web-queries")  # relativo ao WORKDIR /app, igual ao runtime

if not root.is_dir():
    sys.exit(f"FALHA: {root} ausente na imagem (falta COPY segments no Dockerfile)")

missing = [s for s in SLUGS if not (root / f"{s}.q1.yaml").is_file()]
if missing:
    sys.exit(f"FALHA: protocolos ausentes: {missing}")

for slug in SLUGS:
    seg = SegmentConfig(slug=slug, name=slug, platforms=["web"], keywords=["x"])
    planned = FileWebQueries().queries_for(seg)
    if not planned:
        sys.exit(f"FALHA: FileWebQueries nao carregou o protocolo de {slug}")
    print(f"ok {slug}: {len(planned)} consultas -> {[q.query_id for q in planned]}")

print("OK: protocolos q1 presentes e legiveis na imagem")
PY
