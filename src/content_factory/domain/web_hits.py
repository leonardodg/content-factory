"""F0/F1 — partição dos candidatos web em mantidos x hits (puro).

O coletor devolve TODOS os resultados como `CandidateVideo`, com a
proveniência no `raw`. Aqui separamos quem entra em `videos` (`kept`) de
quem só gera rastro em `web_search_hits` (descartado por anúncio ou
duplicata). Os descartados ficam amostráveis na F2.
"""

from collections.abc import Sequence

from content_factory.domain.models import CandidateVideo, WebSearchHit

DISCARDED_STATUSES = frozenset({"discarded_ad", "duplicate"})
KEPT_STATUS = "kept"
# descartado pelo FILTRO DE ANÚNCIO (o que a cobertura da F4 mede). `duplicate`
# é descarte de dedupe, não falso positivo do filtro.
AD_DISCARD_STATUS = "discarded_ad"


def is_discarded(candidate: CandidateVideo) -> bool:
    return str(candidate.raw.get("web_status", KEPT_STATUS)) in DISCARDED_STATUSES


def hit_from_candidate(candidate: CandidateVideo) -> WebSearchHit:
    raw = candidate.raw
    return WebSearchHit(
        query=str(raw.get("web_query", "")),
        query_id=str(raw.get("query_id", "")),
        query_intent=str(raw.get("query_intent", "seed")),
        provider=str(raw.get("provider", "unknown")),
        provider_result_position=int(raw.get("provider_result_position", 0)),
        url_original=str(raw.get("url_original", candidate.url)),
        canonical_url=str(raw.get("canonical_url", candidate.url)),
        status=str(raw.get("web_status", KEPT_STATUS)),
        discard_reason=(
            None if raw.get("discard_reason") is None else str(raw.get("discard_reason"))
        ),
        collector_version=str(raw.get("collector_version", "")),
        rules_version=str(raw.get("rules_version", "")),
        title=candidate.title,
        snippet=candidate.description,
    )


def partition_web(
    candidates: Sequence[CandidateVideo],
) -> tuple[list[CandidateVideo], list[WebSearchHit]]:
    """Devolve (mantidos, hits-de-todos). Mantidos na ordem original."""
    kept: list[CandidateVideo] = []
    hits: list[WebSearchHit] = []
    for candidate in candidates:
        hits.append(hit_from_candidate(candidate))
        if not is_discarded(candidate):
            kept.append(candidate)
    return kept, hits


def discard_counts(hits: Sequence[WebSearchHit]) -> dict[str, int]:
    """Nº de descartes por motivo (para o `ops-report`)."""
    counts: dict[str, int] = {}
    for hit in hits:
        if hit.status in DISCARDED_STATUSES:
            reason = hit.discard_reason or hit.status
            counts[reason] = counts.get(reason, 0) + 1
    return counts
