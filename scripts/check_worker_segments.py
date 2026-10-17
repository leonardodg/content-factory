"""Probe de smoke test da imagem do worker (F1) - roda DENTRO da imagem.

Verifica que `segments/web-queries/` existe, que cada `<slug>.q1.yaml` esta
presente e que o `FileWebQueries` carrega o protocolo. Sai != 0 em qualquer
falha. Chamado por `scripts/smoke_worker_segments.sh` (que injeta este arquivo
no container via stdin) e tambem roda direto no host, da raiz do repositorio.
"""

from pathlib import Path

from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.web_queries import FileWebQueries

SLUGS = ("velas-artesanais", "sabonetes-artesanais", "resina-artesanal")


def main(slugs: tuple[str, ...] = SLUGS) -> int:
    root = Path("segments/web-queries")  # relativo ao WORKDIR /app, igual ao runtime

    if not root.is_dir():
        print(f"FALHA: {root} ausente na imagem (falta COPY segments no Dockerfile)")
        return 1

    missing = [slug for slug in slugs if not (root / f"{slug}.q1.yaml").is_file()]
    if missing:
        print(f"FALHA: protocolos ausentes: {missing}")
        return 1

    for slug in slugs:
        seg = SegmentConfig(slug=slug, name=slug, platforms=["web"], keywords=["x"])
        planned = FileWebQueries().queries_for(seg)
        if not planned:
            print(f"FALHA: FileWebQueries nao carregou o protocolo de {slug}")
            return 1
        print(f"ok {slug}: {len(planned)} consultas -> {[q.query_id for q in planned]}")

    print("OK: protocolos q1 presentes e legiveis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
