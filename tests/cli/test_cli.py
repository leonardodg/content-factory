import os
import sys
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from content_factory.app.collect import CollectDeps
from content_factory.app.ports import Collector
from content_factory.app.score import ScoreOutcome
from content_factory.cli.main import app, main
from content_factory.domain.errors import CollectorError
from tests.conftest import requires_infra
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


def test_ingest_blocks_private_url_exit_1_envelope() -> None:
    """Sem rede: guarda SSRF do caminho real rejeita 127.0.0.1 antes do fetch."""
    result = runner.invoke(app, ["ingest", "--url", "http://127.0.0.1:9/x"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "invalid_url"' in result.stdout


def test_ingest_success_exit_0_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory.app import ingest as ingest_mod
    from content_factory.domain.models import CandidateVideo

    seen: dict[str, object] = {}

    async def _fake(url: str, **kw: object) -> CandidateVideo:
        seen.update(kw)
        return CandidateVideo(
            platform="web", platform_video_id=url, url=url, creator_handle="", title="T"
        )

    monkeypatch.setattr(ingest_mod, "ingest_url", _fake)
    result = runner.invoke(app, ["ingest", "--url", "https://ex.com/a"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"title": "T"' in result.stdout
    assert seen.get("guard") is not None  # composition root fia a guarda real


def test_ingest_with_segment_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory.app import ingest as ingest_mod
    from content_factory.domain.models import CandidateVideo

    async def _fake(url: str, **kw: object) -> CandidateVideo:
        return CandidateVideo(
            platform="web", platform_video_id=url, url=url, creator_handle="", title="T"
        )

    saved: list[tuple[str, CandidateVideo]] = []

    async def _fake_save(slug: str, cand: CandidateVideo) -> None:
        saved.append((slug, cand))

    monkeypatch.setattr(ingest_mod, "ingest_url", _fake)
    monkeypatch.setattr("content_factory.cli.main._ingest_save", _fake_save)
    result = runner.invoke(app, ["ingest", "--url", "https://ex.com/a", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert saved and saved[0][0] == "candles"
    assert saved[0][1].platform == "web"


@requires_infra
def test_score_unknown_segment_exit_1_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """R7 trd-scoring: slug inexistente → segment_not_found + exit 1 (Postgres real)."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    result = runner.invoke(app, ["score", "--segment", "nao-existe-task16"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"command": "score"' in result.stdout
    assert '"code": "segment_not_found"' in result.stdout


def _fake_score(
    ok: bool, partial: bool, scored: int
) -> Callable[[str], Coroutine[Any, Any, ScoreOutcome]]:
    """Seam: devolve o ScoreOutcome fake no lugar do wire real (sem DB)."""

    async def _run(slug: str) -> ScoreOutcome:
        _ = slug
        return ScoreOutcome(ok=ok, partial=partial, scored=scored)

    return _run


def test_score_success_exit_0_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._ascore", _fake_score(True, False, 3))
    result = runner.invoke(app, ["score", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"scored": 3' in result.stdout
    assert '"partial"' not in result.stdout


def test_score_partial_exit_2_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._ascore", _fake_score(False, True, 2))
    result = runner.invoke(app, ["score", "--segment", "candles"])
    assert result.exit_code == 2
    assert '"ok": true' in result.stdout
    assert '"partial": true' in result.stdout
    assert '"scored": 2' in result.stdout


def test_score_total_failure_exit_1_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._ascore", _fake_score(False, False, 0))
    result = runner.invoke(app, ["score", "--segment", "candles"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
