import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from content_factory.app.collect import CollectDeps
from content_factory.app.ports import Collector
from content_factory.cli.main import app, main
from content_factory.domain.errors import CollectorError
from tests.stubs import FakeCollector, FakeLimiter, FakeLock, FakeRepos

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


def _fake_deps(
    collectors: list[Collector], lock: FakeLock | None = None, *, updates_only: bool = False
) -> Callable[[list[str]], AbstractAsyncContextManager[CollectDeps]]:
    """Seam de monkeypatch: devolve deps fake no lugar das reais (sem DB/Redis)."""
    lock = FakeLock() if lock is None else lock

    @asynccontextmanager
    async def factory(platforms: list[str]) -> AsyncGenerator[CollectDeps]:
        repos = FakeRepos(updates_only=updates_only)
        yield CollectDeps(
            collectors=collectors,
            videos=repos.videos,
            segments=repos.segments,
            runs=repos.runs,
            limiter=FakeLimiter(),
            lock=lock,
        )

    return factory


def test_collect_partial_failure_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _fake_deps(
        [
            FakeCollector("youtube", n=3),
            FakeCollector("tiktok", fail=CollectorError("blocked", "tiktok")),
        ]
    )
    monkeypatch.setattr("content_factory.cli.main._collect_deps", deps)
    result = runner.invoke(
        app,
        ["collect", "--segment", "candles", "--platform", "youtube,tiktok", "--limit", "10"],
    )
    assert result.exit_code == 2
    assert '"ok": true' in result.stdout
    assert '"partial": true' in result.stdout
    assert '"inserted": 3' in result.stdout
    assert '"failed": 1' in result.stdout


def test_collect_updates_only_partial_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _fake_deps(
        [
            FakeCollector("youtube", n=3),
            FakeCollector("tiktok", fail=CollectorError("blocked", "tiktok")),
        ],
        updates_only=True,
    )
    monkeypatch.setattr("content_factory.cli.main._collect_deps", deps)
    result = runner.invoke(
        app,
        ["collect", "--segment", "candles", "--platform", "youtube,tiktok", "--limit", "10"],
    )
    assert result.exit_code == 2
    assert '"ok": true' in result.stdout
    assert '"partial": true' in result.stdout
    assert '"inserted": 0' in result.stdout
    assert '"updated": 3' in result.stdout
    assert '"failed": 1' in result.stdout


def test_collect_success_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _fake_deps([FakeCollector("youtube", n=2)])
    monkeypatch.setattr("content_factory.cli.main._collect_deps", deps)
    result = runner.invoke(
        app, ["collect", "--segment", "candles", "--platform", "youtube", "--limit", "5"]
    )
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"partial"' not in result.stdout


def test_collect_lock_held_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _fake_deps([FakeCollector("youtube")], lock=FakeLock(acquired=False))
    monkeypatch.setattr("content_factory.cli.main._collect_deps", deps)
    result = runner.invoke(app, ["collect", "--segment", "candles", "--platform", "youtube"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "ja_em_execucao"' in result.stdout
