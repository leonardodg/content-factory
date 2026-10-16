"""F4 — avaliação pareada com métricas, IC por bootstrap e veredito (puro).

As duas funções (`base-fase-h` e `web-v1`) ordenam o MESMO conjunto de itens
anotados. Toda métrica sai com `n` e intervalo de confiança 95% por bootstrap
(seed fixa). Veredito: aprovado | direcional | insuficiente | não atingido.
"""

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

BOOTSTRAP_SEED = 20261020
BOOTSTRAP_RESAMPLES = 1000

# critérios pré-registrados
TARGET_P5 = 0.6
TARGET_P10 = 0.5
TARGET_GAIN_P10 = 0.1
TARGET_COVERAGE = 0.9
MIN_KEPT_ANNOTATED = 40

INVALID_MARKERS = ("anuncio", "vazia", "duplicata", "inacessivel")

Verdict = Literal["aprovado", "direcional", "insuficiente", "nao_atingido"]


@dataclass(frozen=True)
class WebAnnotation:
    """Rótulos de um item (0 a 2) + marcador de inválido + motivo."""

    item_id: str
    tema: int
    intencao: int
    utilidade: int
    invalido: str | None = None
    motivo: str = ""

    @property
    def relevant(self) -> bool:
        return self.invalido is None and self.tema >= 1

    @property
    def useful(self) -> bool:
        return self.invalido is None and self.utilidade >= 1

    @property
    def legitimate(self) -> bool:
        return self.invalido is None


@dataclass(frozen=True)
class Interval:
    point: float | None
    low: float | None
    high: float | None
    n: int


def precision_at_k(ranking: Sequence[str], relevant: Mapping[str, bool], k: int) -> float | None:
    """Proporção de relevantes nos `k` primeiros; `None` se faltam itens."""
    top = list(ranking[:k])
    if len(top) < k:
        return None
    return sum(1 for item in top if relevant.get(item, False)) / k


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("percentile de lista vazia")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _rebuild(order: Sequence[str], sampled: Sequence[str]) -> list[str]:
    """Reordena o conjunto reamostrado (com repetição) pela ordem original."""
    counts = Counter(sampled)
    out: list[str] = []
    for item in order:
        out.extend([item] * counts.get(item, 0))
    return out


def bootstrap_interval(
    values: Sequence[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """IC percentil 95% de uma estatística sobre reamostragens bootstrap."""
    if not values:
        raise ValueError("bootstrap de lista vazia")
    rng = random.Random(seed)
    stats = sorted(rng.choice(values) for _ in range(resamples))
    return _percentile(stats, alpha / 2), _percentile(stats, 1 - alpha / 2)


def precision_interval(
    ranking: Sequence[str],
    relevant: Mapping[str, bool],
    k: int,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Interval:
    """P@k com IC 95% (bootstrap sobre os itens ranqueados)."""
    pool = list(ranking)
    if len(pool) < k:
        return Interval(point=None, low=None, high=None, n=len(pool))
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(resamples):
        sampled = [rng.choice(pool) for _ in pool]
        rebuilt = _rebuild(pool, sampled)
        value = precision_at_k(rebuilt, relevant, k)
        if value is not None:
            stats.append(value)
    point = precision_at_k(pool, relevant, k)
    if not stats:
        return Interval(point=point, low=None, high=None, n=len(pool))
    return Interval(
        point=point, low=_percentile(stats, 0.025), high=_percentile(stats, 0.975), n=len(pool)
    )


def paired_gain_interval(
    base_ranking: Sequence[str],
    v1_ranking: Sequence[str],
    relevant: Mapping[str, bool],
    k: int,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Interval:
    """Ganho P@k(v1) - P@k(base) no MESMO conjunto reamostrado (pareado)."""
    pool = list(base_ranking)
    if len(pool) < k or len(v1_ranking) < k:
        return Interval(point=None, low=None, high=None, n=len(pool))
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(resamples):
        sampled = [rng.choice(pool) for _ in pool]
        base_val = precision_at_k(_rebuild(base_ranking, sampled), relevant, k)
        v1_val = precision_at_k(_rebuild(v1_ranking, sampled), relevant, k)
        if base_val is not None and v1_val is not None:
            diffs.append(v1_val - base_val)
    base_point = precision_at_k(base_ranking, relevant, k)
    v1_point = precision_at_k(v1_ranking, relevant, k)
    point = None if base_point is None or v1_point is None else v1_point - base_point
    if not diffs:
        return Interval(point=point, low=None, high=None, n=len(pool))
    return Interval(
        point=point, low=_percentile(diffs, 0.025), high=_percentile(diffs, 0.975), n=len(pool)
    )


def filter_coverage(kept_legit: int, discarded_legit: int) -> float | None:
    """Legítimas preservadas / legítimas antes do filtro (estimada pela amostra)."""
    total = kept_legit + discarded_legit
    if total == 0:
        return None
    return kept_legit / total


# --- Spearman (stdlib) ---


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Correlação de Spearman entre dois vetores de mesmo tamanho."""
    if len(xs) != len(ys):
        raise ValueError("vetores de tamanhos diferentes")
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx == 0.0 or deny == 0.0:
        return 0.0
    return num / (denx * deny)


def verdict(
    *,
    kept_annotated: int,
    p5: Interval,
    p10: Interval,
    gain_p10: Interval,
    coverage: float | None,
    intents_without_data: bool = False,
) -> Verdict:
    """Veredito por segmento, dadas as métricas e o pré-registro."""
    if kept_annotated < MIN_KEPT_ANNOTATED or intents_without_data:
        return "insuficiente"
    pointwise_ok = (
        p5.point is not None
        and p10.point is not None
        and gain_p10.point is not None
        and coverage is not None
        and p5.point >= TARGET_P5
        and p10.point >= TARGET_P10
        and gain_p10.point >= TARGET_GAIN_P10
        and coverage >= TARGET_COVERAGE
    )
    if not pointwise_ok:
        return "nao_atingido"
    if gain_p10.low is not None and gain_p10.low > 0:
        return "aprovado"
    return "direcional"
