"""Trends aplicado ao pool (R3) — puro e versionado.

O Trends normaliza DENTRO de cada consulta: lotes só se comparam com uma
âncora comum (mesma em todos os lotes). Série DIÁRIA (`today 3-m`):

- razão termo/âncora por DIA (alinhada por `period_start`; dia com âncora 0
  é descartado, não vira 0);
- `interest_mean` = média das razões nos últimos 7 dias (com teto
  `RATIO_CAP`, para um termo 100x maior não dominar);
- `slope` = (média 7 d - média dos 21 d anteriores) / média dos 21 d,
  limitada a [-1, 1];
- `trend_score` = 0,7 x mean/RATIO_CAP + 0,3 x (slope + 1)/2 em [0, 1].
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from content_factory.domain.models import TrendPoint

TRENDS_RANK_VERSION = "trends-anchor-v1"
W_INTEREST = 0.7
W_SLOPE = 0.3
PER_BATCH = 4  # + 1 âncora = 5 (limite do Trends por consulta)
RATIO_CAP = 5.0
RECENT_DAYS = 7
BASE_DAYS = 21


@dataclass(frozen=True)
class Batch:
    anchor: str
    terms: tuple[str, ...]


def plan_trend_batches(
    terms: Sequence[str], anchor: str, per_batch: int = PER_BATCH, max_terms: int = 16
) -> list[Batch]:
    selected = [t for t in dict.fromkeys(terms) if t.casefold() != anchor.casefold()][:max_terms]
    return [
        Batch(anchor=anchor, terms=tuple(selected[i : i + per_batch]))
        for i in range(0, len(selected), per_batch)
    ]


def _daily_ratios(
    points: Sequence[TrendPoint], anchor_points: Sequence[TrendPoint]
) -> list[tuple[datetime, float]]:
    anchor = {p.period_start: p.interest for p in anchor_points}
    out: list[tuple[datetime, float]] = []
    for p in sorted(points, key=lambda q: q.period_start):
        base = anchor.get(p.period_start)
        if base is not None and base > 0:
            out.append((p.period_start, min(p.interest / base, RATIO_CAP)))
    return out


def relative_interest(
    points: Sequence[TrendPoint], anchor_points: Sequence[TrendPoint]
) -> tuple[float | None, float | None]:
    """(média 7 d da razão, inclinação 7 d x 21 d anteriores) — `None` sem âncora."""
    ratios = [r for _, r in _daily_ratios(points, anchor_points)]
    if not ratios:
        return None, None
    recent = ratios[-RECENT_DAYS:]
    base = ratios[-(RECENT_DAYS + BASE_DAYS) : -RECENT_DAYS]
    mean = sum(recent) / len(recent)
    slope: float | None = None
    if base:
        base_mean = sum(base) / len(base)
        slope = (
            0.0
            if base_mean == 0 and mean == 0
            else (1.0 if base_mean == 0 else max(-1.0, min(1.0, (mean - base_mean) / base_mean)))
        )
    return round(mean, 6), (round(slope, 6) if slope is not None else None)


def trend_score(interest_mean: float | None, slope: float | None) -> float | None:
    """[0, 1]; `None` quando não há âncora comparável (não é zero)."""
    if interest_mean is None:
        return None
    s = 0.0 if slope is None else slope
    return round(W_INTEREST * interest_mean / RATIO_CAP + W_SLOPE * (s + 1) / 2, 6)
