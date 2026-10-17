"""Plano de consulta por coleta (R5) — puro, determinístico e sem estado.

Uma VAGA por origem (braço do experimento), fixa e pré-registrada:
`seed` → `suggest` → `trends` → `hashtag:<plataforma>`. A origem gravada na
descoberta é a vaga que ESCOLHEU o termo:

- `suggest`: pool `suggest` ordenado por `kw_rank`;
- `trends`: o mesmo pool ordenado por `trend_score` (Trends não traz termo
  novo, só reordena) — nunca repete o termo já escolhido pela vaga `suggest`;
- `hashtag:<plataforma>`: hashtags mineradas (R11), buscadas como `#tag`.

Vaga sem candidato vai para `suggest` (próximo termo) e é gravada como
`suggest` — a origem reflete quem escolheu, nunca a vaga nominal. Sem
realocação adaptativa durante o teste.

O rodízio é `index % len` por (dia UTC, slot) via `run_index_for`: coletas nos
mesmos horários do dia caem em índices distintos, e repetir o mesmo slot
(catch-up, execução manual) repete as mesmas buscas — reprodutível.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from content_factory.domain.models import SearchQuery

SEARCH_PLAN_VERSION = "search-plan-v1"
SLOT_ORDER: tuple[str, ...] = ("seed", "suggest", "trends", "hashtag")
DEFAULT_SEARCHES_PER_COLLECT: dict[str, int] = {"youtube": 4, "instagram": 1, "tiktok": 1}


@dataclass(frozen=True)
class PoolSnapshot:
    """Retrato do pool de termos do segmento, já ordenado por cada critério."""

    seeds: tuple[str, ...] = ()
    suggest_by_rank: tuple[str, ...] = ()  # pool suggest ativo, kw_rank desc
    suggest_by_trend: tuple[str, ...] = ()  # só termos com trend_score, desc
    hashtags: tuple[str, ...] = ()  # hashtag:<plataforma> ativo, engagement desc


def run_index_for(now: datetime, collections_per_day: int = 4) -> int:
    """Índice sem estado: nº de slots desde a data ordinal (UTC)."""
    slot = min(now.hour * collections_per_day // 24, collections_per_day - 1)
    return now.date().toordinal() * collections_per_day + slot


def searches_per_collect(platform: str, env: Mapping[str, str]) -> int:
    raw = env.get(f"SEARCHES_PER_COLLECT_{platform.upper()}")
    if raw is not None and raw.strip().isdigit() and int(raw) >= 1:
        return int(raw)
    return DEFAULT_SEARCHES_PER_COLLECT.get(platform, 1)


def _rotate(items: tuple[str, ...], index: int, taken: set[str]) -> str | None:
    """Primeiro item a partir de `index % len` que ainda não foi usado."""
    n = len(items)
    for k in range(n):
        item = items[(index + k) % n]
        if item not in taken:
            return item
    return None


def plan_queries(
    pool: PoolSnapshot, platform: str, run_index: int, searches: int
) -> list[SearchQuery]:
    taken: set[str] = set()
    out: list[SearchQuery] = []

    def add(origin: str, term: str, query_term: str) -> None:
        taken.add(term)
        out.append(
            SearchQuery(
                term=query_term,
                origin=origin,
                query_id=f"{SEARCH_PLAN_VERSION}:{run_index}:{len(out)}:{origin}",
            )
        )

    for slot in SLOT_ORDER[: max(searches, 0)]:
        if slot == "seed":
            term = _rotate(pool.seeds, run_index, taken)
            if term is not None:
                add("seed", term, term)
            continue
        if slot == "trends":
            term = _rotate(pool.suggest_by_trend, run_index, taken)
            if term is not None:
                add("trends", term, term)
                continue
        if slot == "hashtag":
            tag = _rotate(pool.hashtags, run_index, taken)
            if tag is not None:
                add(f"hashtag:{platform}", tag, f"#{tag}" if platform == "youtube" else tag)
                continue
        # vaga `suggest` ou fallback de vaga vazia
        term = _rotate(pool.suggest_by_rank, run_index, taken)
        if term is not None:
            add("suggest", term, term)
    # vagas extra além das 4 (env) vão para `suggest`
    for _ in range(max(searches - len(SLOT_ORDER), 0)):
        term = _rotate(pool.suggest_by_rank, run_index, taken)
        if term is None:
            break
        add("suggest", term, term)
    return out
