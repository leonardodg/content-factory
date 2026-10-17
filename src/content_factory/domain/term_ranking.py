"""Ranking de temas e veredito por origem (R10, §6) — puro, seed FIXA.

Unidade de análise = VÍDEO ÚNICO por chave (termo ou origem): o mesmo vídeo
achado pelo mesmo termo em 16 coletas conta 1 vez (sem pseudorreplicação).
Um vídeo achado por duas origens conta nas duas (sobreposição reportada).

Duas saídas:
- `rank_terms`: por termo, `n` (vídeos únicos), medianas, fração no top 10%
  do `viral-v2`, `engagement_score`; `n < MIN_N_TERM` ⇒ `insufficient`
  (nunca vencedor).
- `compare_origins`: regra PRÉ-REGISTRADA — origem X vs `seed`: diferença
  das medianas do `viral-v2` com IC 95% por bootstrap; `n < MIN_N_ORIGIN`
  em X ou em `seed` ⇒ `insufficient`; limite inferior > 0 ⇒ `better`;
  limite superior < 0 ⇒ `worse`; senão `directional`.
"""

import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

TERM_RANK_VERSION = "term-rank-v1"
ENGAGEMENT_WEIGHTS: dict[str, float] = {
    "top10_fraction": 0.4,
    "median_velocity": 0.3,
    "median_engagement": 0.3,
}
MIN_N_TERM = 5
MIN_N_ORIGIN = 30
BASELINE_ORIGIN = "seed"
BOOTSTRAP_SEED = 20261016
BOOTSTRAP_RESAMPLES = 1000


@dataclass(frozen=True)
class TermSample:
    """Uma atribuição: vídeo x termo x origem, com o `viral-v2` no corte."""

    video_id: int
    term: str
    origin: str
    viral_v2: float | None
    velocity: float | None
    engagement: float | None
    channel_relative: float | None


@dataclass(frozen=True)
class TermSummary:
    key: str
    n: int
    median_viral_v2: float | None
    median_velocity: float | None
    median_engagement: float | None
    median_channel_relative: float | None
    top10_fraction: float | None
    engagement_score: float | None
    verdict: str  # "ranked" | "insufficient"


@dataclass(frozen=True)
class OriginComparison:
    origin: str
    n: int
    baseline_n: int
    median_viral_v2: float | None
    baseline_median: float | None
    diff: float | None
    ci_low: float | None
    ci_high: float | None
    overlap: int  # vídeos que também vieram da `seed`
    verdict: str  # "better" | "worse" | "directional" | "insufficient"


def median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _unique_by_video(samples: Iterable[TermSample]) -> list[TermSample]:
    seen: dict[int, TermSample] = {}
    for s in samples:
        seen.setdefault(s.video_id, s)
    return list(seen.values())


def _group(samples: Sequence[TermSample], by: str) -> dict[str, list[TermSample]]:
    groups: dict[str, list[TermSample]] = {}
    for s in samples:
        groups.setdefault(s.term if by == "term" else s.origin, []).append(s)
    return {k: _unique_by_video(v) for k, v in groups.items()}


def top10_threshold(samples: Sequence[TermSample]) -> float | None:
    """Percentil 90 do `viral-v2` dos vídeos ÚNICOS do recorte."""
    viral = [s.viral_v2 for s in _unique_by_video(samples) if s.viral_v2 is not None]
    if len(viral) < 10:
        return None
    return statistics.quantiles(sorted(viral), n=10)[8]


def _score(components: dict[str, float | None]) -> float | None:
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None
    weight_sum = sum(ENGAGEMENT_WEIGHTS[k] for k in available)
    return round(sum(v * ENGAGEMENT_WEIGHTS[k] for k, v in available.items()) / weight_sum, 6)


def summarize(group: Sequence[TermSample], key: str, threshold: float | None) -> TermSummary:
    viral = [s.viral_v2 for s in group if s.viral_v2 is not None]
    frac = (
        round(sum(1 for v in viral if v >= threshold) / len(viral), 6)
        if viral and threshold is not None
        else None
    )
    med_v = median([s.velocity for s in group if s.velocity is not None])
    med_e = median([s.engagement for s in group if s.engagement is not None])
    insufficient = len(group) < MIN_N_TERM
    return TermSummary(
        key=key,
        n=len(group),
        median_viral_v2=median(viral),
        median_velocity=med_v,
        median_engagement=med_e,
        median_channel_relative=median(
            [s.channel_relative for s in group if s.channel_relative is not None]
        ),
        top10_fraction=frac,
        engagement_score=None
        if insufficient
        else _score({"top10_fraction": frac, "median_velocity": med_v, "median_engagement": med_e}),
        verdict="insufficient" if insufficient else "ranked",
    )


def rank_terms(samples: Sequence[TermSample]) -> list[TermSummary]:
    threshold = top10_threshold(samples)
    out = [summarize(g, k, threshold) for k, g in _group(samples, "term").items()]
    return sorted(
        out, key=lambda s: (s.verdict == "insufficient", -(s.engagement_score or 0.0), s.key)
    )


def bootstrap_diff_ci(
    a: Sequence[float],
    b: Sequence[float],
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[float, float]:
    """IC 95% (percentis 2,5/97,5) de mediana(a) - mediana(b); reamostra cada grupo."""
    rng = random.Random(seed)
    diffs = sorted(
        statistics.median(rng.choices(a, k=len(a))) - statistics.median(rng.choices(b, k=len(b)))
        for _ in range(resamples)
    )
    return round(diffs[int(0.025 * resamples)], 6), round(diffs[int(0.975 * resamples) - 1], 6)


def compare_origins(samples: Sequence[TermSample]) -> list[OriginComparison]:
    groups = _group(samples, "origin")
    base = [s.viral_v2 for s in groups.get(BASELINE_ORIGIN, []) if s.viral_v2 is not None]
    base_ids = {s.video_id for s in groups.get(BASELINE_ORIGIN, [])}
    out: list[OriginComparison] = []
    for origin, group in sorted(groups.items()):
        if origin == BASELINE_ORIGIN:
            continue
        values = [s.viral_v2 for s in group if s.viral_v2 is not None]
        med, base_med = median(values), median(base)
        ci_low = ci_high = diff = None
        if med is None or base_med is None or min(len(values), len(base)) < MIN_N_ORIGIN:
            verdict = "insufficient"
        else:
            diff = round(med - base_med, 6)
            ci_low, ci_high = bootstrap_diff_ci(values, base)
            verdict = "better" if ci_low > 0 else "worse" if ci_high < 0 else "directional"
        out.append(
            OriginComparison(
                origin=origin,
                n=len(values),
                baseline_n=len(base),
                median_viral_v2=med,
                baseline_median=base_med,
                diff=diff,
                ci_low=ci_low,
                ci_high=ci_high,
                overlap=sum(1 for s in group if s.video_id in base_ids),
                verdict=verdict,
            )
        )
    return out
