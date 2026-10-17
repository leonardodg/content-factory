"""Probe do smoke REAL da imagem do worker — roda DENTRO da imagem.

Revisão externa (P1): os testes que inspecionam o texto do Dockerfile não
provam que a imagem funciona. Este probe roda no Python da imagem e verifica:

1. os YAMLs dos segmentos do teste carregam (`segments/` está na imagem);
2. o código do worker e do relatório importa (venv completo);
3. o Chromium que o Playwright do venv espera EXISTE no disco (o defeito de
   17/10: a imagem tinha o 1248 e o Playwright travado procurava o 1243).

Os protocolos `q1` da web têm o smoke próprio e estrito
(`scripts/smoke_worker_segments.sh`), que o CI roda quando há `q1` no repo.
"""

from pathlib import Path

SLUGS = ("velas-artesanais", "sabonetes-artesanais", "resina-artesanal")


def check_segments(root: Path, slugs: tuple[str, ...] = SLUGS) -> list[str]:
    from content_factory.app.segment import load_segment_file

    problems: list[str] = []
    for slug in slugs:
        path = root / f"{slug}.yaml"
        try:
            cfg = load_segment_file(path)
        except Exception as exc:  # qualquer falha de leitura reprova o smoke
            problems.append(f"{slug}: segmento não carrega ({type(exc).__name__}: {exc})")
            continue
        if not cfg.anchor_terms:
            problems.append(f"{slug}: sem anchor_terms")
    return problems


def check_imports() -> list[str]:
    try:
        import content_factory.app.term_report
        import content_factory.infra.queue.worker  # noqa: F401
    except Exception as exc:
        return [f"import falhou ({type(exc).__name__}: {exc})"]
    return []


def check_chromium() -> list[str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        path = Path(p.chromium.executable_path)
    if not path.is_file():
        return [f"Chromium esperado pelo Playwright do venv não existe: {path}"]
    return []


def main() -> int:
    problems = check_segments(Path("segments")) + check_imports() + check_chromium()
    for problem in problems:
        print(f"FALHA: {problem}")
    if problems:
        return 1
    print("OK: segmentos, imports e Chromium da versão travada presentes na imagem")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
