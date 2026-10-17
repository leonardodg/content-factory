"""Score de viralização `viral-v2` (R9) — puro, experimental e versionado.

Roda AO LADO do `fase-h` (linha de base intocada). Todos os componentes são
percentis DENTRO de `(platform, segment_id)` na janela do teste: plataformas
com escalas diferentes não se misturam. Relevância vira FILTRO (R1, no app) e
recência vira JANELA (30 d, no app) — nenhuma das duas é peso aqui.

Componente ausente é excluído e os pesos renormalizam (mesma regra do
`fase-h`): ausência não é zero.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

VIRAL_V2_VERSION = "viral-v2"
WEIGHTS_V2: dict[str, float] = {
    "velocity": 0.35,
    "engagement": 0.25,
    "channel_relative": 0.25,
    "reach": 0.15,
}
WINDOW_DAYS = 30
MIN_POPULATION = 5


@dataclass(frozen=True)
class ViralInput:
    video_id: int
    platform: str
    segment_id: int
    velocity_per_hour: float | None
    engagement_rate: float | None
    views: int | None
    channel_followers: int | None


@dataclass(frozen=True)
class ViralScore:
    video_id: int
    total: float | None
    velocity: float | None
    engagement: float | None
    channel_relative: float | None
    reach: float | None
    population_n: int
    explanation: dict[str, object] = field(default_factory=dict[str, object])


def percentile_ranks(values: Mapping[int, float]) -> dict[int, float]:
    """Percentil em [0,1]: pior = 0, melhor = 1 (`posição/(n-1)`); n=1 ⇒ 0,5.

    Empates recebem a MÉDIA das posições que ocupam — não depende da ordem
    de inserção.
    """
    n = len(values)
    if n == 0:
        return {}
    if n == 1:
        return {next(iter(values)): 0.5}
    order = sorted(values.items(), key=lambda kv: kv[1])
    out: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and order[j + 1][1] == order[i][1]:
            j += 1
        rank = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            out[order[k][0]] = rank
        i = j + 1
    return out


def _log1p(value: float | None) -> float | None:
    """`None` = sem medida (ausente, renormaliza); 0 ou negativo = medida ZERO.

    Velocidade/alcance zero são o pior valor, não falta de dado — tratar 0 como
    ausente deixava o vídeo parado acima de quem cresceu pouco (QA 17/10).
    """
    return None if value is None else math.log1p(max(value, 0.0))


def _raw_components(r: ViralInput) -> dict[str, float | None]:
    followers = r.channel_followers
    relative = (
        r.views / followers
        if r.views is not None and followers is not None and followers > 0
        else None
    )
    return {
        "velocity": _log1p(r.velocity_per_hour),
        "engagement": r.engagement_rate,
        "channel_relative": _log1p(relative),
        "reach": _log1p(float(r.views) if r.views is not None else None),
    }


def score_population(
    rows: Sequence[ViralInput], min_population: int = MIN_POPULATION
) -> list[ViralScore]:
    """Pontua TODOS os vídeos, normalizando por `(platform, segment_id)`."""
    groups: dict[tuple[str, int], list[ViralInput]] = {}
    for r in rows:
        groups.setdefault((r.platform, r.segment_id), []).append(r)

    out: dict[int, ViralScore] = {}
    for group in groups.values():
        raw = {r.video_id: _raw_components(r) for r in group}
        n = len(group)
        ranks: dict[str, dict[int, float]] = {}
        for name in WEIGHTS_V2:
            present = {vid: v for vid, comps in raw.items() if (v := comps[name]) is not None}
            ranks[name] = percentile_ranks(present)
        for r in group:
            comps: dict[str, float | None] = {
                name: ranks[name].get(r.video_id) for name in WEIGHTS_V2
            }
            available = {k: v for k, v in comps.items() if v is not None}
            total: float | None = None
            if available and n >= min_population:
                weight_sum = sum(WEIGHTS_V2[k] for k in available)
                total = round(sum(available[k] * WEIGHTS_V2[k] for k in available) / weight_sum, 6)
            out[r.video_id] = ViralScore(
                video_id=r.video_id,
                total=total,
                velocity=comps["velocity"],
                engagement=comps["engagement"],
                channel_relative=comps["channel_relative"],
                reach=comps["reach"],
                population_n=n,
                explanation={
                    "formula_version": VIRAL_V2_VERSION,
                    "components_used": sorted(available),
                    "population_n": n,
                    "min_population": min_population,
                    "insufficient": n < min_population,
                },
            )
    return list(out.values())
