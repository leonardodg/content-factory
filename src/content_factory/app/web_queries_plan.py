"""F1 — `cf web-queries --propose`: monta e grava o protocolo `q1` do segmento.

O arquivo gerado é revisado e aprovado pelo usuário (só veto com motivo) e
vai para o commit antes da coleta.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml

from content_factory.domain.web_queries import (
    INTENT_LEXICON_VERSION,
    QUERY_FILTER_VERSION,
    QUERY_PROTOCOL_VERSION,
    QUERY_SEED,
    WebQueryProtocol,
    protocol_to_dict,
    select_queries,
)


def build_protocol(
    segment_slug: str,
    signals: Sequence[str],
    keywords: Sequence[str],
    *,
    vetoes: Mapping[str, str] | None = None,
    per_class: int = 3,
    seed: int = QUERY_SEED,
) -> WebQueryProtocol:
    queries, missing = select_queries(
        signals, keywords, per_class=per_class, seed=seed, vetoes=vetoes
    )
    return WebQueryProtocol(
        segment_slug=segment_slug,
        version=QUERY_PROTOCOL_VERSION,
        seed=seed,
        lexicon_version=INTENT_LEXICON_VERSION,
        queries=queries,
        filter_version=QUERY_FILTER_VERSION,
        missing=missing,
        vetoes=dict(vetoes or {}),
    )


def dump_protocol_yaml(protocol: WebQueryProtocol) -> str:
    return yaml.safe_dump(protocol_to_dict(protocol), allow_unicode=True, sort_keys=False)


def write_protocol(path: Path, protocol: WebQueryProtocol) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_protocol_yaml(protocol), encoding="utf-8")
