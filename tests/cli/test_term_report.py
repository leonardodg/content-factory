"""Bloco 5 Task 8: `cf term-report` — envelope só com contagens; CSV fora do repo."""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from content_factory.app.term_report import TermReportOutcome, TopVideo
from content_factory.cli.main import app
from content_factory.domain.models import TermReportRecord

runner = CliRunner()
AS_OF = "2026-10-21T12:00:00+00:00"


def _outcome() -> TermReportOutcome:
    as_of = datetime(2026, 10, 21, 12, tzinfo=UTC)
    base: dict[str, object] = {
        "report_id": "rep-1",
        "segment_id": 1,
        "platform": "youtube",
        "version": "term-rank-v1",
        "rules_version": "term-filter-v1+viral-v2",
        "as_of": as_of,
    }

    def rec(**values: object) -> TermReportRecord:
        return TermReportRecord.model_validate({**base, **values})

    records = [
        rec(scope="term", report_key="como fazer vela", n=12, verdict="ranked"),
        rec(scope="origin", report_key="seed", n=40, verdict="baseline"),
        rec(scope="origin", report_key="suggest", n=40, baseline_n=40, verdict="better"),
        rec(scope="video", report_key="yt1", n=1, verdict="top", rank=1),
    ]
    return TermReportOutcome(
        report_id="rep-1", records=records, top=[TopVideo(1, "yt1", "Vela 1", 0.9, 1000)]
    )


def _patch(monkeypatch: pytest.MonkeyPatch, seen: list[datetime]) -> None:
    async def _fake(segment: str, platform: str, as_of: datetime, top: int) -> TermReportOutcome:
        _ = segment, platform, top
        seen.append(as_of)
        return _outcome()

    monkeypatch.setattr("content_factory.cli.main._aterm_report", _fake)


def test_envelope_so_com_contagens(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[datetime] = []
    _patch(monkeypatch, seen)
    result = runner.invoke(app, ["term-report", "--segment", "velas", "--as-of", AS_OF])
    assert result.exit_code == 0, result.stdout
    out = json.loads(result.stdout)  # stdout = só o envelope (1 JSON)
    assert out["ok"] is True and out["command"] == "term-report"
    assert out["origins_decided"] == 1 and out["videos"] == 1 and out["rows_saved"] == 4
    assert "Vela 1" not in result.stdout  # título de terceiro não sai no envelope
    assert seen == [datetime(2026, 10, 21, 12, tzinfo=UTC)]


def test_as_of_sem_fuso_e_erro_de_uso(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, [])
    result = runner.invoke(
        app, ["term-report", "--segment", "velas", "--as-of", "2026-10-21T12:00:00"]
    )
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["ok"] is False and out["error"]["code"] == "usage_error"


def test_csv_dentro_do_repositorio_e_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, [])
    inside = Path(__file__).resolve().parents[2] / "relatorio.csv"
    result = runner.invoke(
        app,
        ["term-report", "--segment", "velas", "--as-of", AS_OF, "--out", str(inside)],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "out_inside_repo"
    assert not inside.exists()


def test_csv_fora_do_repositorio_tem_as_tres_secoes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch(monkeypatch, [])
    out_path = tmp_path / "rel.csv"
    result = runner.invoke(
        app,
        ["term-report", "--segment", "velas", "--as-of", AS_OF, "--out", str(out_path)],
    )
    assert result.exit_code == 0, result.stdout
    with out_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert {r["scope"] for r in rows} == {"term", "origin", "video"}
    assert next(r for r in rows if r["scope"] == "video")["title"] == "Vela 1"
