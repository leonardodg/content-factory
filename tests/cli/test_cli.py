from typer.testing import CliRunner

from content_factory.cli.main import app

runner = CliRunner()


def test_segment_load_missing_file_exit_1() -> None:
    result = runner.invoke(app, ["segment", "load", "nope.yaml"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
