"""Task 8 (Fase H) — avaliação empírica do ranking.

Dois comandos, dois arquivos de trabalho:
- `cf score-sample`: exporta o ranking atual de um segmento em CSV
  (componentes do ScoreResult, não só o total) para um humano anotar
  uma coluna `avaliacao_manual` fora da ferramenta.
- `cf score-correlation`: relê o CSV já anotado e devolve a
  correlação de Spearman entre o rank do sistema (total) e o rank
  humano.

Spearman é implementado aqui em stdlib (rank com média para empates +
Pearson sobre os ranks) — sem scipy: scipy seria uma dependência de
~40MB para uma fórmula de 15 linhas, e o p-value exato exigiria a CDF
de Student. Para um ferramenta de validação manual com n tipicamente
>= 30, o r de Spearman é a métrica que importa; se um dia o p-value
exato for necessário, aí sim scipy entra.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class CorrelationResult:
    """Envelope do score-correlation: todos os campos sempre presentes
    (None quando não aplicáveis) — evita a união de TypedDicts que o
    pyright desta versão não discrimina via `result.ok`."""

    ok: bool
    n: int | None = None
    spearman: float | None = None
    error_code: str | None = None
    message: str | None = None


@dataclass
class ScoreSampleRow:
    video_id: int
    url: str
    total: float
    performance: float | None
    velocity: float | None
    engagement: float | None
    relevance: float
    recency: float
    metrics_available: list[str]
    avaliacao_manual: float | None = None


class ScoreSampleRepository(Protocol):
    async def top_scored_for_segment(self, segment_id: int, limit: int) -> list[ScoreSampleRow]:
        """Últimos scores calculados, ordenados por viral_score desc."""
        ...


async def score_sample(
    segment_id: int, limit: int, repo: ScoreSampleRepository
) -> list[ScoreSampleRow]:
    return await repo.top_scored_for_segment(segment_id, limit)


# --- CSV roundtrip ---

CSV_HEADER = [
    "video_id",
    "url",
    "total",
    "performance",
    "velocity",
    "engagement",
    "relevance",
    "recency",
    "metrics_available",
    "avaliacao_manual",
]


def write_score_csv(rows: list[ScoreSampleRow], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for r in rows:
            writer.writerow(
                [
                    r.video_id,
                    r.url,
                    r.total,
                    "" if r.performance is None else r.performance,
                    "" if r.velocity is None else r.velocity,
                    "" if r.engagement is None else r.engagement,
                    r.relevance,
                    r.recency,
                    ";".join(r.metrics_available),
                    "" if r.avaliacao_manual is None else r.avaliacao_manual,
                ]
            )


def _opt_float(value: str) -> float | None:
    return None if value == "" else float(value)


def read_score_csv(path: Path) -> list[ScoreSampleRow]:
    rows: list[ScoreSampleRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for rec in reader:
            rows.append(
                ScoreSampleRow(
                    video_id=int(rec["video_id"]),
                    url=rec["url"],
                    total=float(rec["total"]),
                    performance=_opt_float(rec["performance"]),
                    velocity=_opt_float(rec["velocity"]),
                    engagement=_opt_float(rec["engagement"]),
                    relevance=float(rec["relevance"]),
                    recency=float(rec["recency"]),
                    metrics_available=(
                        rec["metrics_available"].split(";") if rec["metrics_available"] else []
                    ),
                    avaliacao_manual=_opt_float(rec["avaliacao_manual"]),
                )
            )
    return rows


# --- Spearman (stdlib) ---


def _ranks(values: list[float]) -> list[float]:
    """Ranks 1-based; empates recebem a média dos ranks que ocupariam."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # ranks i+1..j+1 (1-based)
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    den = den_x * den_y
    if den == 0.0:
        return 0.0  # uma das colunas é constante — sem correlação definível
    return num / den


def spearman_rank(totals: list[float], manuals: list[float]) -> tuple[float, int]:
    """Correlação de Spearman entre dois vetores; devolve (r, n)."""
    if len(totals) != len(manuals):
        raise ValueError("totals e manuals devem ter o mesmo comprimento")
    r = _pearson(_ranks(totals), _ranks(manuals))
    return r, len(totals)


def score_correlation(path: Path) -> CorrelationResult:
    """Envelope do `cf score-correlation`: soma os ranks das linhas anotadas.

    Linhas sem `avaliacao_manual` são ignoradas (o humano ainda não
    anotou aquilo — não é missing data, é trabalho pendente).
    """
    rows = read_score_csv(path)
    pairs: list[tuple[float, float]] = []
    for row in rows:
        manual = row.avaliacao_manual
        if manual is not None:
            pairs.append((row.total, manual))
    if len(pairs) < 2:
        return CorrelationResult(
            ok=False,
            error_code="not_enough_annotated",
            message=(
                f"preciso de pelo menos 2 linhas com avaliacao_manual preenchida; "
                f"achei {len(pairs)}"
            ),
        )
    r, n = spearman_rank(
        [total for total, _ in pairs],
        [manual for _, manual in pairs],
    )
    return CorrelationResult(ok=True, n=n, spearman=r)
