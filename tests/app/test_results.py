"""C3: contrato único de resultado (CLI e worker usam as mesmas funções)."""

from datetime import UTC, datetime

from content_factory.app.collect import CollectOutcome
from content_factory.app.research_pipeline import ResearchOutcome
from content_factory.app.results import (
    collect_result,
    exception_result,
    job_result,
    research_result,
    score_result,
)
from content_factory.app.score import ScoreOutcome
from content_factory.domain.models import CollectRun
from content_factory.infra.redact import redact

NOW = datetime(2026, 10, 15, tzinfo=UTC)
COUNTS: dict[str, int | bool] = {"found": 3, "inserted": 1, "updated": 2, "failed": 0, "skipped": 0}


def test_collect_ok_partial_and_failure() -> None:
    assert collect_result(CollectOutcome(ok=True, partial=False, counts=COUNTS)) == {
        "ok": True,
        "partial": False,
        **COUNTS,
    }
    partial = collect_result(CollectOutcome(ok=False, partial=True, counts={**COUNTS, "failed": 1}))
    assert partial["ok"] is True and partial["partial"] is True and "error" not in partial
    failed_run = CollectRun(
        collector="tiktok", status="failed", error_code="auth_expired", started_at=NOW
    )
    failed = collect_result(
        CollectOutcome(
            ok=False,
            partial=False,
            counts={**COUNTS, "inserted": 0, "updated": 0, "failed": 1},
            runs=[failed_run],
        )
    )
    assert failed["ok"] is False and failed["partial"] is False
    assert failed["error"] == {
        "code": "auth_expired",
        "message": "coleta falhou em 1 plataforma(s)",
    }
    assert failed["failed"] == 1  # contagens também no erro


def test_collect_lock_busy_is_an_error_without_counts() -> None:
    out = collect_result(CollectOutcome(ok=False, partial=False, counts={"ja_em_execucao": True}))
    assert out == {
        "ok": False,
        "partial": False,
        "error": {"code": "ja_em_execucao", "message": "coleta já em execução"},
    }


def test_score_result_uses_single_counts_source() -> None:
    """A-5: contagens do score vêm de `ScoreOutcome.as_counts()` (antes: 2 dicts)."""
    ok = ScoreOutcome(ok=True, partial=False, scored=5, processed=5)
    assert score_result(ok) == {"ok": True, "partial": False, **ok.as_counts()}
    assert ok.as_counts() == {
        "scored": 5,
        "processed": 5,
        "persisted": 5,
        "failed": 0,
        "failures": {},
    }
    partial = score_result(
        ScoreOutcome(
            ok=False, partial=True, scored=4, processed=5, failed=1, failures={"data_error": 1}
        )
    )
    assert partial["ok"] is True and partial["partial"] is True
    failed = score_result(ScoreOutcome(ok=False, partial=False, scored=0, processed=5, failed=5))
    assert failed["ok"] is False
    assert failed["error"] == {"code": "internal_error", "message": "falha ao gravar scores"}


def test_research_result_failure_code() -> None:
    assert research_result(ResearchOutcome(ok=True, counts={"signals_saved": 3}))["ok"] is True
    failed = research_result(ResearchOutcome(ok=False, partial=False, counts={"signals_saved": 0}))
    assert failed["error"]["code"] == "research_failed"
    busy = research_result(ResearchOutcome(ok=False, counts={"ja_em_execucao": True}))
    assert busy["error"]["code"] == "ja_em_execucao"


def test_exception_result_and_job_rollup() -> None:
    err = exception_result(RuntimeError("boom token=abc123"), redact)
    assert err["ok"] is False and err["partial"] is False
    assert err["error"]["code"] == "internal_error"
    assert err["error"]["type"] == "RuntimeError"
    assert "abc123" not in err["error"]["message"]  # redigido
    ok = {"ok": True, "partial": False}
    part = {"ok": True, "partial": True}
    assert job_result({"a": ok, "b": ok}) == {
        "ok": True,
        "partial": False,
        "segments": {"a": ok, "b": ok},
    }
    assert job_result({"a": ok, "b": part})["partial"] is True
    mixed = job_result({"a": ok, "b": err})
    assert mixed["ok"] is False and mixed["partial"] is True  # um segmento gravou
    assert job_result({}) == {"ok": True, "partial": False, "segments": {}}
