"""Ranking do Suggest `kw-rank-v1` (R2) — puro e determinístico.

Substitui o peso fixo 0,5. Peso em [0,1] = média ponderada de:

- **posição**: média de `1/posição` entre as sementes que trouxeram a frase;
- **recorrência**: nº de sementes distintas / cap;
- **persistência**: nº de dias distintos (últimos 7, **incluindo hoje** — o app
  grava o dia de hoje ANTES de ler) / 7.

Pesos da combinação são constantes versionadas: bumpar `KW_RANK_VERSION`
quando mudarem.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

KW_RANK_VERSION = "kw-rank-v1"
W_POSITION = 0.5
W_RECURRENCE = 0.3
W_PERSISTENCE = 0.2
RECURRENCE_CAP = 3  # 3+ sementes distintas satura a recorrência
PERSISTENCE_DAYS = 7


@dataclass(frozen=True)
class SuggestHit:
    term: str
    seed: str
    position: int  # 1-based na lista daquela semente


def rank_suggestions(hits: Sequence[SuggestHit], days_seen: Mapping[str, int]) -> dict[str, float]:
    by_term: dict[str, list[SuggestHit]] = {}
    for hit in hits:
        by_term.setdefault(hit.term, []).append(hit)

    weights: dict[str, float] = {}
    for term, term_hits in by_term.items():
        position = sum(1.0 / h.position for h in term_hits) / len(term_hits)
        recurrence = min(len({h.seed for h in term_hits}) / RECURRENCE_CAP, 1.0)
        persistence = min(days_seen.get(term, 1) / PERSISTENCE_DAYS, 1.0)
        weights[term] = round(
            W_POSITION * position + W_RECURRENCE * recurrence + W_PERSISTENCE * persistence, 6
        )
    return weights
