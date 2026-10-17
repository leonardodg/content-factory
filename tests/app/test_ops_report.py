"""C2 `cf ops-report`: agregação pura (sem banco) — plano 2026-10-15."""

from datetime import UTC, datetime, timedelta
from typing import Any

from content_factory.app.ops_report import build_ops_report
from content_factory.domain.models import CollectRun, MetricCoverage

T0 = datetime(2026, 10, 15, 23, 0, tzinfo=UTC)
SINCE = T0 - timedelta(hours=24)
UNTIL = T0 + timedelta(hours=1)


def _run(
    collector: str,
    status: str = "ok",
    *,
    at: timedelta = timedelta(0),
    dur_s: float = 5,
    found: int = 3,
    error_code: str | None = None,
    discover_ms: int | None = 1000,
    persist_ms: int | None = 200,
    quota_used: int = 0,
) -> CollectRun:
    started = T0 + at
    return CollectRun(
        collector=collector,
        segment_id=1,
        status=status,
        found=found,
        inserted=found,
        error_code=error_code,
        quota_used=quota_used,
        started_at=started,
        finished_at=started + timedelta(seconds=dur_s),
        discover_ms=discover_ms,
        persist_ms=persist_ms,
    )


def _report(
    runs: list[CollectRun],
    coverage: dict[str, MetricCoverage] | None = None,
    scored: int = 0,
) -> dict[str, Any]:
    return build_ops_report(runs, coverage or {}, scored, since=SINCE, until=UNTIL)


def test_counts_status_rates_errors_and_empty_per_collector() -> None:
    runs = [
        _run("youtube", quota_used=102),
        _run("youtube", "failed", error_code="rate_limited", found=0),
        _run("tiktok", "failed", error_code="auth_expired", found=0),
        _run("web", found=0),
        _run("web"),
        _run("research:trends", "failed", error_code="upstream_changed", found=0),
    ]
    out = _report(runs)
    yt = out["collectors"]["youtube"]
    assert yt["runs"] == 2 and yt["status"] == {"ok": 1, "failed": 1}
    assert yt["ok_rate"] == 0.5 and yt["errors"] == {"rate_limited": 1}
    assert yt["quota_used"] == 102
    assert out["collectors"]["research:trends"]["errors"] == {"upstream_changed": 1}
    assert out["collectors"]["web"]["empty"] == 1
    assert out["web_empty"] == {"runs": 1, "of": 2, "ambiguous": True}


def test_blocked_runs_are_counted_separately() -> None:
    out = _report([_run("youtube", "blocked", error_code="blocked", found=0), _run("web")])
    assert out["blocked_runs"] == 1


def test_ticks_separate_wall_time_from_summed_call_time() -> None:
    """Concorrência: soma discover+persist passa da parede; as duas saem separadas."""
    tick1 = [
        _run("youtube", at=timedelta(seconds=0), dur_s=6, discover_ms=5000, persist_ms=1000),
        _run("web", at=timedelta(seconds=1), dur_s=5, discover_ms=4000, persist_ms=1000),
    ]
    tick2 = [_run("youtube", at=timedelta(hours=12), dur_s=2, discover_ms=1500, persist_ms=500)]
    research = [_run("research:youtube", at=timedelta(hours=6), dur_s=60)]
    out = _report([*tick1, *tick2, *research])
    ticks = out["ticks"]
    assert ticks["n"] == 2
    first = ticks["items"][0]
    assert first["runs"] == 2
    assert first["wall_ms"] == 6000  # 0 s → 6 s
    assert first["sum_ms"] == 11000  # 5000+1000+4000+1000
    assert ticks["wall_ms_max"] == 6000


def test_coverage_and_scored_pass_through_with_ratios() -> None:
    cov = {"youtube": MetricCoverage(videos=10, with_views=8, with_two_snapshots=6)}
    out = _report([], coverage=cov, scored=42)
    assert out["coverage"] == {
        "segment_all_time": {
            "youtube": {
                "videos": 10,
                "with_views": 8,
                "with_two_snapshots": 6,
                "views_rate": 0.8,
                "two_snapshots_rate": 0.6,
            }
        },
        "window": {},
    }
    assert out["scored"] == 42
    assert out["window"] == {"since": SINCE.isoformat(), "until": UNTIL.isoformat()}
    assert out["ticks"] == {"n": 0, "items": [], "wall_ms_max": None, "wall_ms_avg": None}


def test_web_discards_passthrough_defaults_empty() -> None:
    """F0: descartes por motivo chegam ao relatório; default é vazio."""
    assert _report([_run("web")])["web_discards"] == {}
    out = build_ops_report(
        [_run("web")],
        {},
        0,
        since=SINCE,
        until=UNTIL,
        web_discards={"ad:bing_aclick": 3, "duplicate": 1},
    )
    assert out["web_discards"] == {"ad:bing_aclick": 3, "duplicate": 1}


def test_data_sufficiency_is_separate_from_ok_rate() -> None:
    """Auditoria 3, N-3: ok com found=0 é sucesso operacional sem dado útil.

    `ok_rate` não muda de semântica; `data_rate` (runs ok com found > 0 /
    runs) e `found_avg_ok` (média de found nas runs ok) mostram a suficiência.
    """
    runs = [
        _run("research:trends", found=0),
        _run("research:trends", found=0),
        _run("research:trends", found=10),
        _run("research:trends", "failed", error_code="upstream_changed", found=0),
    ]
    trends = _report(runs)["collectors"]["research:trends"]
    assert trends["ok_rate"] == 0.75
    assert trends["data_runs"] == 1
    assert trends["data_rate"] == 0.25
    assert trends["found_avg_ok"] == round(10 / 3, 2)


# --- Bloco 6 (R13/R14): seções do teste viral-por-tema ---

_SINCE = datetime(2026, 10, 20, tzinfo=UTC)
_UNTIL = _SINCE + timedelta(days=1)


def test_ops_report_traz_as_secoes_do_teste_viral() -> None:
    rep = build_ops_report(
        runs=[],
        coverage={},
        scored=0,
        since=_SINCE,
        until=_UNTIL,
        search_yield={
            "youtube:seed": {
                "searches": 4,
                "empty": 1,
                "failed": 0,
                "quota": 408,
                "results": 30,
                "kept": 28,
            },
            "youtube:suggest": {
                "searches": 8,
                "empty": 0,
                "failed": 1,
                "quota": 816,
                "results": 80,
                "kept": 60,
            },
        },
        remediation_quota=12,
        discoveries_by_origin={"seed": 10, "suggest": 25},
        remediation={"targets": 100, "remeasured": 95, "removed": 3},
        pool={"seed": {"active": 3, "excluded": 0}, "suggest": {"active": 20, "excluded": 5}},
        filter_discards={"lang": 4, "no_anchor": 1},
        language={"youtube": {"videos": 50, "with_language": 40}},
    )
    assert rep["search_quota"]["searches_total"] == 12
    assert rep["search_quota"]["quota_total"] == 408 + 816 + 12
    assert rep["search_quota"]["by_origin"]["youtube:seed"]["videos_per_search"] == 7.0
    assert rep["discovery_origins"]["suggest"] == 25
    assert rep["remediation"]["coverage"] == 0.95
    assert rep["segment_all_time"]["term_pool"]["suggest"]["excluded"] == 5
    assert rep["segment_all_time"]["filter_discards"] == {"lang": 4, "no_anchor": 1}
    assert rep["segment_all_time"]["language"]["youtube"]["rate"] == 0.8
    # escopo explícito (revisão externa): o acumulado não fica no topo da janela
    assert "term_pool" not in rep and "language" not in rep


def test_ops_report_sem_dados_novos_continua_valido() -> None:
    rep = build_ops_report(runs=[], coverage={}, scored=0, since=_SINCE, until=_UNTIL)
    assert rep["remediation"]["coverage"] is None
    assert rep["segment_all_time"]["term_pool"] == {}
    assert rep["segment_all_time"]["filter_discards"] == {}
