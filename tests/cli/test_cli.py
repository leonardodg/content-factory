import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from content_factory.cli.main import app, main

runner = CliRunner()
CANDLES = Path("segments/candles.yaml")


def test_segment_load_missing_file_exit_1() -> None:
    result = runner.invoke(app, ["segment", "load", "nope.yaml"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout


def test_segment_load_db_down_envelope_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://contentfactory:cf@127.0.0.1:1/none")
    result = runner.invoke(app, ["segment", "load", str(CANDLES)])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "internal_error"' in result.stdout


def test_usage_error_missing_arg_exit_1_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["cf", "segment", "load"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert '"ok": false' in out
    assert '"code": "usage_error"' in out
    assert '"command": "segment.load"' in out
