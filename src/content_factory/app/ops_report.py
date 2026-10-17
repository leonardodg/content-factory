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
    ok_runs = [r for r in runs if r.status == "ok"]
    data_runs = sum(1 for r in ok_runs if r.found > 0)
    return {
        "runs": len(runs),
        "status": dict(status),
        "ok_rate": _ratio(status["ok"], len(runs)),
        # auditoria 3 (N-3): suficiência ≠ sucesso operacional — ok vazio
        # continua ok, mas não conta como run com dado útil
        "data_runs": data_runs,
        "data_rate": _ratio(data_runs, len(runs)),
        "found_avg_ok": round(sum(r.found for r in ok_runs) / len(ok_runs), 2) if ok_runs else None,
        "empty": len(ok_runs) - data_runs,
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


def _coverage(coverage: Mapping[str, MetricCoverage]) -> dict[str, Any]:
    return {
        platform: {
            "videos": c.videos,
            "with_views": c.with_views,
            "with_two_snapshots": c.with_two_snapshots,
            "views_rate": _ratio(c.with_views, c.videos),
            "two_snapshots_rate": _ratio(c.with_two_snapshots, c.videos),
        }
        for platform, c in sorted(coverage.items())
    }


def build_ops_report(
    runs: Sequence[CollectRun],
    coverage: Mapping[str, MetricCoverage],
    scored: int,
    *,
    since: datetime,
    until: datetime,
    coverage_window: Mapping[str, MetricCoverage] | None = None,
    web_discards: Mapping[str, int] | None = None,
    tick_gap: timedelta = TICK_GAP,
    search_yield: Mapping[str, Mapping[str, int]] | None = None,
    remediation_quota: int = 0,
    discoveries_by_origin: Mapping[str, int] | None = None,
    remediation: Mapping[str, int] | None = None,
    pool: Mapping[str, Mapping[str, int]] | None = None,
    filter_discards: Mapping[str, int] | None = None,
    language: Mapping[str, Mapping[str, int]] | None = None,
) -> dict[str, Any]:
    """Relatório operacional de um segmento na janela `[since, until]`.

    Auditoria 3 (N-1): runs, ticks e scores são **da janela**; a cobertura
    sai com o escopo no nome — `segment_all_time` (todo o histórico) e
    `window` (só vídeos com snapshot capturado na janela). `web_discards`
    (F0) traz os descartes do filtro web por motivo, para medir falso
    positivo de forma auditável.
    """
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
        "web_discards": dict(web_discards or {}),
        "blocked_runs": sum(1 for r in runs if r.status == "blocked"),
        "ticks": _ticks(runs, tick_gap),
        "coverage": {
            "segment_all_time": _coverage(coverage),
            "window": _coverage(coverage_window or {}),
        },
        "scored": scored,
        # Bloco 6 (R13): teste viral-por-tema, NA JANELA [since, until]. Cota e
        # rendimento vêm do log de buscas (`search_queries`): busca vazia também
        # gasta cota e mede o rendimento; a cota da remedição soma no total.
        "search_quota": {
            "by_origin": {
                key: {**c, "videos_per_search": _ratio(c.get("kept", 0), c.get("searches", 0))}
                for key, c in sorted((search_yield or {}).items())
            },
            "searches_total": sum(c.get("searches", 0) for c in (search_yield or {}).values()),
            "quota_total": sum(c.get("quota", 0) for c in (search_yield or {}).values())
            + remediation_quota,
            "remediation_quota": remediation_quota,
        },
        "discovery_origins": dict(sorted((discoveries_by_origin or {}).items())),
        "remediation": {
            "targets": (remediation or {}).get("targets", 0),
            "remeasured": (remediation or {}).get("remeasured", 0),
            "removed": (remediation or {}).get("removed", 0),
            "coverage": _ratio(
                (remediation or {}).get("remeasured", 0), (remediation or {}).get("targets", 0)
            ),
        },
        # ACUMULADO do segmento (não é da janela): estado do pool, descartes do
        # filtro e cobertura de idioma — escopo no nome, como em `coverage`.
        "segment_all_time": {
            "term_pool": {k: dict(v) for k, v in sorted((pool or {}).items())},
            "filter_discards": dict(sorted((filter_discards or {}).items())),
            # R14: cobertura de idioma declarado por plataforma
            "language": {
                plat: {
                    **counts,
                    "rate": _ratio(counts.get("with_language", 0), counts.get("videos", 0)),
                }
                for plat, counts in sorted((language or {}).items())
            },
        },
    }
