"""Mede, contra o autocompletar real, a cobertura por intencao das sondas.

Read-only: so consulta o Suggest, nao escreve no banco. Uso:

    uv run python scripts/measure_suggest_probes.py
    uv run python scripts/measure_suggest_probes.py velas-artesanais

Compara a cobertura (por classe) do Suggest semeado so com as keywords do
segmento ("SEM sondas") com a cobertura semeada tambem com as sondas de
intencao (`probe-v2`) sobre os termos-cabeca ("COM sondas"). O resultado
alimenta docs/manual/evidencia-sondas-suggest.md.
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from content_factory.app.segment import load_segment_file
from content_factory.domain.suggest_probes import suggest_probes
from content_factory.domain.web_queries import classify_intent, intent_coverage
from content_factory.infra.collectors.web_suggest import WebSuggestProvider

SLUGS = ("velas-artesanais", "sabonetes-artesanais", "resina-artesanal")


def _report(label: str, phrases: list[str]) -> None:
    print(f"  {label}: {len(phrases)} frases -> {intent_coverage(phrases)}")
    for intent in ("resolver", "tendencia"):
        for phrase in phrases:
            if classify_intent(phrase) == intent:
                print(f"      [{intent}] {phrase}")


async def _measure(provider: WebSuggestProvider, slug: str) -> None:
    cfg = load_segment_file(Path(f"segments/{slug}.yaml"))
    probes = suggest_probes(cfg.probe_terms)
    print(f"\n== {slug}  probe_terms={cfg.probe_terms}")
    print(f"  sondas ({len(probes)}): {probes}")
    baseline = await provider.expand(cfg.keywords, 0)
    with_probes = await provider.expand([*cfg.keywords, *probes], 0)
    _report("SEM sondas", [s.keyword for s in baseline])
    _report("COM sondas", [s.keyword for s in with_probes])


async def main(slugs: Sequence[str]) -> None:
    provider = WebSuggestProvider()
    for slug in slugs:
        await _measure(provider, slug)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slugs", nargs="*", default=list(SLUGS))
    asyncio.run(main(parser.parse_args().slugs))
