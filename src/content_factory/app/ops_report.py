"""C2 `cf ops-report`: agregação pura das runs, cobertura e scores de uma janela.

Sem I/O: o repositório devolve as linhas e esta função monta o relatório.
Separa o que a auditoria pediu para não misturar:

- `empty` (run `ok` com `found = 0`) de sucesso com dados — no `web` o vazio
  é **ambíguo** (o `ddgs` não distingue sem resultado de engine bloqueada);
- runs `blocked` (breaker) de falhas;
- duração de **parede** de um tick (min `started_at` → max `finished_at`) da
  **soma** `discover_ms + persist_ms`, que passa da parede com concorrência.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from content_factory.domain.models import CollectRun, MetricCoverage

RESEARCH_PREFIX = "research:"
# runs de um mesmo tick do collect começam em segundos; ticks ficam horas apart
TICK_GAP = timedelta(minutes=10)


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _collector_stats(runs: Sequence[CollectRun]) -> dict[str, Any]:
    status = Counter(r.status for r in runs)
    errors = Counter(r.error_code for r in runs if r.error_code)
    return {
        "runs": len(runs),
        "status": dict(status),
        "ok_rate": _ratio(status["ok"], len(runs)),
        "empty": sum(1 for r in runs if r.status == "ok" and r.found == 0),
        "errors": dict(errors),
        "found": sum(r.found for r in runs),
        "inserted": sum(r.inserted for r in runs),
        "updated": sum(r.updated for r in runs),
        "skipped": sum(r.skipped for r in runs),
        "quota_used": sum(r.quota_used for r in runs),
    }


def _ms(delta: timedelta) -> int:
    return int(delta.total_seconds() * 1000)


def _ticks(runs: Sequence[CollectRun], gap: timedelta) -> dict[str, Any]:
    collect = sorted(
        (r for r in runs if not r.collector.startswith(RESEARCH_PREFIX)),
        key=lambda r: r.started_at,
    )
    groups: list[list[CollectRun]] = []
    for run in collect:
        if groups and run.started_at - groups[-1][0].started_at <= gap:
            groups[-1].append(run)
        else:
            groups.append([run])
    items: list[dict[str, Any]] = []
    for group in groups:
        start = min(r.started_at for r in group)
        ends = [r.finished_at for r in group if r.finished_at is not None]
        items.append(
            {
                "started_at": start.isoformat(),
                "runs": len(group),
                "wall_ms": _ms(max(ends) - start) if ends else None,
                "sum_ms": sum((r.discover_ms or 0) + (r.persist_ms or 0) for r in group),
            }
        )
    walls = [i["wall_ms"] for i in items if i["wall_ms"] is not None]
    return {
        "n": len(items),
        "items": items,
        "wall_ms_max": max(walls) if walls else None,
        "wall_ms_avg": round(sum(walls) / len(walls)) if walls else None,
    }


def build_ops_report(
    runs: Sequence[CollectRun],
    coverage: Mapping[str, MetricCoverage],
    scored: int,
    *,
    since: datetime,
    until: datetime,
    tick_gap: timedelta = TICK_GAP,
) -> dict[str, Any]:
    """Relatório operacional de um segmento na janela `[since, until]`."""
    by_collector: dict[str, list[CollectRun]] = {}
    for run in runs:
        by_collector.setdefault(run.collector, []).append(run)
    collectors = {name: _collector_stats(rs) for name, rs in sorted(by_collector.items())}
    web = collectors.get("web")
    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "collectors": collectors,
        "web_empty": {
            "runs": web["empty"] if web else 0,
            "of": web["runs"] if web else 0,
            "ambiguous": True,
        },
        "blocked_runs": sum(1 for r in runs if r.status == "blocked"),
        "ticks": _ticks(runs, tick_gap),
        "coverage": {
            platform: {
                "videos": c.videos,
                "with_views": c.with_views,
                "with_two_snapshots": c.with_two_snapshots,
                "views_rate": _ratio(c.with_views, c.videos),
                "two_snapshots_rate": _ratio(c.with_two_snapshots, c.videos),
            }
            for platform, c in sorted(coverage.items())
        },
        "scored": scored,
    }
