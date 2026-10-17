import os
import sys
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from content_factory.app.collect import CollectDeps, CollectOutcome
from content_factory.app.ports import (
    Collector,
    CollectRunRepository,
    OpsReportReader,
    SegmentRepository,
)
from content_factory.app.score import ScoreOutcome
from content_factory.app.web_eval import WebEvalReport
from content_factory.app.web_rank import WebRankOutcome
from content_factory.app.web_sample import WebSampleRun
from content_factory.cli.main import app, main
from content_factory.domain.errors import CollectorError, SegmentNotFound
from content_factory.domain.models import CollectRun, MetricCoverage, Segment, SegmentConfig
from content_factory.domain.web_eval import Interval
from content_factory.domain.web_relevance import WebScore
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
    assert '"partial": false' in result.stdout  # C3: chave sempre presente


def test_collect_lock_held_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = _fake_deps([FakeCollector("youtube")], lock=FakeLock(acquired=False))
    monkeypatch.setattr("content_factory.cli.main._collect_deps", deps)
    result = runner.invoke(app, ["collect", "--segment", "candles", "--platform", "youtube"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "ja_em_execucao"' in result.stdout


async def test_collect_deps_registers_default_collectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composition root do `cf collect` registra os coletores default (gap T17)."""
    from content_factory.cli.main import _collect_deps  # pyright: ignore[reportPrivateUsage]
    from content_factory.config import Settings
    from content_factory.infra.collectors.registry import snapshot

    class _DummySession:
        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[_DummySession]:
        yield _DummySession()

    @asynccontextmanager
    async def _fake_redis(_url: str) -> AsyncGenerator[object]:
        yield object()

    def _fake_get_session_factory(
        _settings: Settings,
    ) -> Callable[[], AbstractAsyncContextManager[_DummySession]]:
        return _fake_session

    monkeypatch.setattr("content_factory.config.get_settings", Settings)
    monkeypatch.setattr(
        "content_factory.infra.db.base.get_session_factory", _fake_get_session_factory
    )
    monkeypatch.setattr("content_factory.infra.redis_ops.redis_client", _fake_redis)

    async with _collect_deps([]):
        pass
    assert {"youtube", "tiktok", "instagram", "web"} <= set(snapshot())


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
    ok: bool,
    partial: bool,
    scored: int,
    processed: int | None = None,
    failed: int = 0,
    failures: dict[str, int] | None = None,
) -> Callable[[str], Coroutine[Any, Any, ScoreOutcome]]:
    """Seam: devolve o ScoreOutcome fake no lugar do wire real (sem DB)."""

    async def _run(slug: str) -> ScoreOutcome:
        _ = slug
        return ScoreOutcome(
            ok=ok,
            partial=partial,
            scored=scored,
            processed=scored + failed if processed is None else processed,
            failed=failed,
            failures=failures,
        )

    return _run


def test_score_success_exit_0_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._ascore", _fake_score(True, False, 3))
    result = runner.invoke(app, ["score", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"scored": 3' in result.stdout
    assert '"processed": 3' in result.stdout
    assert '"persisted": 3' in result.stdout
    assert '"failed": 0' in result.stdout
    assert '"failures": {}' in result.stdout
    assert '"partial": false' in result.stdout  # C3: chave sempre presente


def test_score_partial_exit_2_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5: envelope parcial leva contagens + categorias (não mensagem de erro)."""
    monkeypatch.setattr(
        "content_factory.cli.main._ascore",
        _fake_score(False, True, 2, processed=3, failed=1, failures={"data_error": 1}),
    )
    result = runner.invoke(app, ["score", "--segment", "candles"])
    assert result.exit_code == 2
    assert '"ok": true' in result.stdout
    assert '"partial": true' in result.stdout
    assert '"scored": 2' in result.stdout
    assert '"processed": 3' in result.stdout
    assert '"persisted": 2' in result.stdout
    assert '"failed": 1' in result.stdout
    assert '"data_error": 1' in result.stdout


def test_score_total_failure_exit_1_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5: falha total (0 de N persistidas) é ok=false COM as contagens."""
    monkeypatch.setattr(
        "content_factory.cli.main._ascore",
        _fake_score(False, False, 0, processed=3, failed=3, failures={"data_error": 3}),
    )
    result = runner.invoke(app, ["score", "--segment", "candles"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"processed": 3' in result.stdout
    assert '"persisted": 0' in result.stdout
    assert '"failed": 3' in result.stdout
    assert '"data_error": 3' in result.stdout


class _MissingSegments:
    async def get_by_slug(self, slug: str) -> Segment:
        raise SegmentNotFound(slug)

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        raise NotImplementedError("status não grava segmento")


def _fake_status_deps(
    counts: dict[str, int] | None = None, *, missing: bool = False
) -> Callable[[], AbstractAsyncContextManager[tuple[SegmentRepository, CollectRunRepository]]]:
    """Seam de monkeypatch: (segments, runs) fake no lugar das reais (sem DB)."""

    @asynccontextmanager
    async def factory() -> AsyncGenerator[tuple[SegmentRepository, CollectRunRepository]]:
        repos = FakeRepos(status_counts=counts)
        if missing:
            yield _MissingSegments(), repos.runs
        else:
            yield repos.segments, repos.runs

    return factory


def test_status_success_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "content_factory.cli.main._status_deps", _fake_status_deps({"ok": 2, "blocked": 1})
    )
    result = runner.invoke(app, ["status", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"runs": {' in result.stdout
    assert '"ok": 2' in result.stdout
    assert '"blocked": 1' in result.stdout


def test_status_unknown_segment_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._status_deps", _fake_status_deps(missing=True))
    result = runner.invoke(app, ["status", "--segment", "nao-existe"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "segment_not_found"' in result.stdout


def _fake_timings_deps(
    timings: dict[str, dict[str, float | int | None]] | None = None, *, missing: bool = False
) -> Callable[[], AbstractAsyncContextManager[tuple[SegmentRepository, CollectRunRepository]]]:
    """Seam de monkeypatch: (segments, runs) fake com timing_stats (sem DB)."""

    @asynccontextmanager
    async def factory() -> AsyncGenerator[tuple[SegmentRepository, CollectRunRepository]]:
        repos = FakeRepos(timing_stats=timings)
        if missing:
            yield _MissingSegments(), repos.runs
        else:
            yield repos.segments, repos.runs

    return factory


def test_collect_timings_success_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 4 (baseline): `cf collect-timings` devolve só o envelope, dados no banco."""
    monkeypatch.setattr(
        "content_factory.cli.main._timings_deps",
        _fake_timings_deps(
            {"youtube": {"n": 2, "avg_discover_ms": 150.0, "p95_discover_ms": 150.0}}
        ),
    )
    result = runner.invoke(app, ["collect-timings", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"command": "collect-timings"' in result.stdout
    assert '"youtube"' in result.stdout


def test_collect_timings_unknown_segment_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._timings_deps", _fake_timings_deps(missing=True))
    result = runner.invoke(app, ["collect-timings", "--segment", "nao-existe"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "segment_not_found"' in result.stdout


def _fake_segment_id(_slug: str) -> int:
    return 42


def test_research_start_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime, timedelta

    from content_factory.domain.models import ResearchCampaign

    calls: list[tuple[int, datetime]] = []

    class _Campaigns:
        async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign:
            calls.append((segment_id, ends_at))
            return ResearchCampaign(
                id=1, segment_id=segment_id, started_at=datetime.now(UTC), ends_at=ends_at
            )

        async def stop(self, segment_id: int) -> None:
            raise NotImplementedError

        async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
            raise NotImplementedError

    monkeypatch.setattr("content_factory.cli.main._research_campaigns", lambda: _Campaigns())
    monkeypatch.setattr("content_factory.cli.main._segment_id", _fake_segment_id)
    result = runner.invoke(app, ["research", "start", "--segment", "candles", "--weeks", "6"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"command": "research.start"' in result.stdout
    assert calls and calls[0][0] == 42
    delta = calls[0][1] - datetime.now(UTC)
    assert timedelta(days=40) < delta < timedelta(days=44)  # ~6 semanas


def test_research_stop_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[int] = []

    class _Campaigns:
        async def start(self, segment_id: int, ends_at: object) -> object:
            raise NotImplementedError

        async def stop(self, segment_id: int) -> None:
            stopped.append(segment_id)

        async def active_for_segment(self, segment_id: int) -> object:
            raise NotImplementedError

    monkeypatch.setattr("content_factory.cli.main._research_campaigns", lambda: _Campaigns())
    monkeypatch.setattr("content_factory.cli.main._segment_id", _fake_segment_id)
    result = runner.invoke(app, ["research", "stop", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert stopped == [42]


def test_research_status_active(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime, timedelta

    from content_factory.domain.models import ResearchCampaign

    campaign = ResearchCampaign(
        id=1,
        segment_id=42,
        started_at=datetime.now(UTC),
        ends_at=datetime.now(UTC) + timedelta(weeks=4),
    )

    class _Campaigns:
        async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign:
            raise NotImplementedError

        async def stop(self, segment_id: int) -> None:
            raise NotImplementedError

        async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None:
            return campaign

    monkeypatch.setattr("content_factory.cli.main._research_campaigns", lambda: _Campaigns())
    monkeypatch.setattr("content_factory.cli.main._segment_id", _fake_segment_id)
    result = runner.invoke(app, ["research", "status", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"active": true' in result.stdout


def test_research_status_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Campaigns:
        async def start(self, segment_id: int, ends_at: object) -> object:
            raise NotImplementedError

        async def stop(self, segment_id: int) -> None:
            raise NotImplementedError

        async def active_for_segment(self, segment_id: int) -> object:
            return None

    monkeypatch.setattr("content_factory.cli.main._research_campaigns", lambda: _Campaigns())
    monkeypatch.setattr("content_factory.cli.main._segment_id", _fake_segment_id)
    result = runner.invoke(app, ["research", "status", "--segment", "candles"])
    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"active": false' in result.stdout


def test_research_unknown_segment_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(slug: str) -> int:
        raise SegmentNotFound(slug)

    monkeypatch.setattr("content_factory.cli.main._segment_id", _missing)
    result = runner.invoke(app, ["research", "status", "--segment", "nao-existe"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"code": "segment_not_found"' in result.stdout


def test_research_invalid_action_exit_1() -> None:
    """Subcomando inválido é rejeitado pelo typer (exit 2 = usage error padrão)."""
    result = runner.invoke(app, ["research", "nope", "--segment", "candles"])
    assert result.exit_code == 2  # typer padrão p/ subcomando desconhecido via CliRunner


class _FakeOpsReader:
    async def metric_coverage(
        self, segment_id: int, since: datetime | None = None
    ) -> dict[str, MetricCoverage]:
        _ = segment_id
        if since is not None:  # janela: só 1 vídeo com snapshot recente
            return {"youtube": MetricCoverage(videos=1, with_views=1, with_two_snapshots=0)}
        return {"youtube": MetricCoverage(videos=4, with_views=3, with_two_snapshots=2)}

    async def scored_since(self, segment_id: int, since: datetime) -> int:
        _ = segment_id, since
        return 7

    async def discard_stats(self, segment_id: int, since: datetime) -> dict[str, int]:
        _ = segment_id, since
        return {"ad:bing_aclick": 2, "duplicate": 1}


def _fake_ops_deps(
    *, missing: bool = False
) -> Callable[
    [], AbstractAsyncContextManager[tuple[SegmentRepository, CollectRunRepository, OpsReportReader]]
]:
    """Seam do `ops-report` (C2): segments, runs e leitor fake (sem DB)."""

    @asynccontextmanager
    async def factory() -> AsyncGenerator[
        tuple[SegmentRepository, CollectRunRepository, OpsReportReader]
    ]:
        repos = FakeRepos()
        now = datetime.now(UTC)
        for collector, found in (("web", 0), ("youtube", 3)):
            await repos.runs.record(
                CollectRun(
                    collector=collector,
                    segment_id=1,
                    status="ok",
                    found=found,
                    started_at=now - timedelta(minutes=5),
                    finished_at=now - timedelta(minutes=4),
                )
            )
        segments = _MissingSegments() if missing else repos.segments
        yield segments, repos.runs, _FakeOpsReader()

    return factory


def test_ops_report_success_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """C2: `cf ops-report` agrega runs, web_empty ambíguo, cobertura e scores."""
    import json

    monkeypatch.setattr("content_factory.cli.main._ops_deps", _fake_ops_deps())
    result = runner.invoke(app, ["ops-report", "--segment", "candles", "--hours", "24"])
    assert result.exit_code == 0, result.stdout
    out = json.loads(result.stdout)
    assert out["ok"] is True and out["command"] == "ops-report"
    assert out["segment"] == "candles"
    report = out["report"]
    assert report["web_empty"] == {"runs": 1, "of": 1, "ambiguous": True}
    assert report["web_discards"] == {"ad:bing_aclick": 2, "duplicate": 1}
    assert report["collectors"]["youtube"]["ok_rate"] == 1.0
    cov = report["coverage"]  # escopo explícito (auditoria 3, N-1)
    assert cov["segment_all_time"]["youtube"]["views_rate"] == 0.75
    assert cov["window"]["youtube"]["videos"] == 1
    assert report["scored"] == 7


def test_ops_report_unknown_segment_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._ops_deps", _fake_ops_deps(missing=True))
    result = runner.invoke(app, ["ops-report", "--segment", "nao-existe"])
    assert result.exit_code == 1
    assert '"code": "segment_not_found"' in result.stdout


def test_ops_report_rejects_non_positive_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.cli.main._ops_deps", _fake_ops_deps())
    result = runner.invoke(app, ["ops-report", "--segment", "candles", "--hours", "0"])
    assert result.exit_code == 1
    assert '"code": "usage_error"' in result.stdout


@pytest.mark.parametrize(
    "outcome",
    [
        ScoreOutcome(ok=True, partial=False, scored=3, processed=3),
        ScoreOutcome(
            ok=False, partial=True, scored=2, processed=3, failed=1, failures={"data_error": 1}
        ),
        ScoreOutcome(ok=False, partial=False, scored=0, processed=3, failed=3),
    ],
)
def test_score_envelope_parity_cli_and_worker(
    monkeypatch: pytest.MonkeyPatch, outcome: ScoreOutcome
) -> None:
    """C3: mesmo ScoreOutcome → CLI (sem `command`) == segmento do score_job."""
    import asyncio
    import json

    from content_factory.infra.queue import worker as w

    async def _fake_ascore(_segment: str) -> ScoreOutcome:
        return outcome

    monkeypatch.setattr("content_factory.cli.main._ascore", _fake_ascore)
    result = runner.invoke(app, ["score", "--segment", "velas"])
    cli = json.loads(result.stdout)
    assert cli.pop("command") == "score"
    expected_exit = 0 if cli["ok"] and not cli["partial"] else (2 if cli["ok"] else 1)
    assert result.exit_code == expected_exit

    async def _fake_score(_deps: object) -> ScoreOutcome:
        return outcome

    class _Bundle:
        score = None

        class uow:
            @staticmethod
            async def commit() -> None: ...

    class _AppDeps:
        def create_segment_deps(self, _segment: Segment) -> AbstractAsyncContextManager[_Bundle]:
            @asynccontextmanager
            async def _cm() -> AsyncGenerator[_Bundle]:
                yield _Bundle()

            return _cm()

    class _Campaigns:
        async def active_segments(self) -> list[Segment]:
            return [Segment(id=1, slug="velas", name="Velas", platforms=[], keywords=["v"])]

    monkeypatch.setattr(w, "_score_impl", _fake_score)
    job = asyncio.run(w.run_score_job({"campaigns_deps": _Campaigns(), "app_deps": _AppDeps()}))
    assert job["segments"]["velas"] == cli


def _collect_outcomes() -> list[CollectOutcome]:
    counts: dict[str, int | bool] = {
        "found": 3,
        "inserted": 1,
        "updated": 1,
        "failed": 0,
        "skipped": 0,
    }
    now = datetime.now(UTC)
    failed_run = CollectRun(
        collector="tiktok", status="failed", error_code="auth_expired", started_at=now
    )
    return [
        CollectOutcome(ok=True, partial=False, counts=counts),
        CollectOutcome(ok=False, partial=True, counts={**counts, "failed": 1}),
        CollectOutcome(
            ok=False,
            partial=False,
            counts={**counts, "inserted": 0, "updated": 0, "failed": 1},
            runs=[failed_run],
        ),
        CollectOutcome(ok=False, partial=False, counts={"ja_em_execucao": True}),
    ]


@pytest.mark.parametrize("outcome", _collect_outcomes())
def test_collect_envelope_parity_cli_and_worker(
    monkeypatch: pytest.MonkeyPatch, outcome: CollectOutcome
) -> None:
    """C3: mesmo CollectOutcome → CLI (sem `command`) == segmento do collect_job.

    R7: o worker remedeia dentro do job e acrescenta o sub-envelope
    `remediation`; a CLI (`cf collect`) não remedeia. O contrato C3 é o
    `collect_result` (o mesmo dict), então a paridade compara o resto e o
    passo da remedição é verificado à parte.
    """
    import asyncio
    import json

    from content_factory.app.remediate import RemediationOutcome
    from content_factory.infra.queue import worker as w

    async def _fake_acollect(_slug: str, _platforms: list[str], _limit: int) -> CollectOutcome:
        return outcome

    monkeypatch.setattr("content_factory.cli.main._acollect", _fake_acollect)
    result = runner.invoke(app, ["collect", "--segment", "velas"])
    cli = json.loads(result.stdout)
    assert cli.pop("command") == "collect"

    async def _fake_collect(*_args: object) -> CollectOutcome:
        return outcome

    async def _fake_remediate(_segment_id: int, _deps: object) -> RemediationOutcome:
        return RemediationOutcome()

    class _Bundle:
        collect = None
        remediate = None

        class uow:
            @staticmethod
            async def commit() -> None: ...

    class _AppDeps:
        def create_segment_deps(self, _segment: Segment) -> AbstractAsyncContextManager[_Bundle]:
            @asynccontextmanager
            async def _cm() -> AsyncGenerator[_Bundle]:
                yield _Bundle()

            return _cm()

    class _Campaigns:
        async def active_segments(self) -> list[Segment]:
            return [Segment(id=1, slug="velas", name="Velas", platforms=["web"], keywords=["v"])]

    monkeypatch.setattr(w, "_collect_impl", _fake_collect)
    monkeypatch.setattr(w, "_remediate_impl", _fake_remediate)
    cfg: dict[str, Any] = {"platforms": [], "limit": 10}
    monkeypatch.setattr(w, "_load_settings", lambda: cfg)
    job = asyncio.run(w.run_collect_job({"campaigns_deps": _Campaigns(), "app_deps": _AppDeps()}))
    worker_seg = dict(job["segments"]["velas"])
    remediation = worker_seg.pop("remediation", None)
    if "ja_em_execucao" in outcome.counts:
        # R7: lock ocupado ⇒ o collect nem rodou; remedir gravaria métrica duplicada
        assert remediation is None
    else:
        assert remediation == {
            "targets": 0,
            "skipped_language": 0,
            "refreshed": 0,
            "metrics_written": 0,
            "removed": 0,
            "failed_platforms": 0,
            "quota_used": 0,
        }
    assert worker_seg == cli


# --- web commands (R #45) ---


def test_web_queries_requires_propose() -> None:
    result = runner.invoke(app, ["web-queries", "--segment", "velas"])
    assert result.exit_code == 1
    assert '"code": "usage_error"' in result.stdout


def test_web_queries_propose_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json

    async def _fake(
        segment: str, directory: Path, vetoes: dict[str, str]
    ) -> tuple[str, Path, dict[str, int]]:
        _ = vetoes
        return segment, directory / f"{segment}.q1.yaml", {"aprender": 1}

    monkeypatch.setattr("content_factory.cli.main._apropose_web_queries", _fake)
    result = runner.invoke(
        app, ["web-queries", "--segment", "velas", "--propose", "--dir", str(tmp_path)]
    )

    assert result.exit_code == 0, result.stdout
    out = json.loads(result.stdout)
    assert out["command"] == "web-queries" and out["segment"] == "velas"
    assert out["missing"] == {"aprender": 1}
    assert out["path"].endswith("velas.q1.yaml")


def test_web_rank_envelope_with_compare_and_explain(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    outcome = WebRankOutcome(
        ok=True,
        segment="velas",
        scored=1,
        score_version="web-v1",
        top_v1=[
            WebScore(
                video_id=1,
                title="a",
                domain="a.example",
                score=80.0,
                components={"tema": 1.0},
                explanation={"tema": {"matched": ["vela"]}},
            )
        ],
        top_base=[WebScore(video_id=1, title="a", domain="a.example", score=50.0)],
    )

    async def _fake(segment: str, k: int, max_per_domain: int | None) -> WebRankOutcome:
        _ = segment, k, max_per_domain
        return outcome

    monkeypatch.setattr("content_factory.cli.main._aweb_rank", _fake)
    result = runner.invoke(app, ["web-rank", "--segment", "velas", "--compare", "--explain"])

    assert result.exit_code == 0, result.stdout
    out = json.loads(result.stdout)
    assert out["command"] == "web-rank" and out["score_version"] == "web-v1"
    assert out["scored"] == 1 and out["domains_v1"] == 1
    assert out["top_v1"][0]["video_id"] == 1
    assert "components" in out["top_v1"][0] and "explanation" in out["top_v1"][0]
    assert out["top_base"][0]["video_id"] == 1


def test_web_sample_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import json

    as_of = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)

    async def _fake(segment: str, seed: int, out: Path, allow_incomplete: bool) -> WebSampleRun:
        _ = segment, seed, allow_incomplete
        out.write_text("item_id\n", encoding="utf-8")
        return WebSampleRun(rows=3, as_of=as_of, missing_queries=[])

    monkeypatch.setattr("content_factory.cli.main._aweb_sample", _fake)
    out_path = tmp_path / "sample.csv"
    result = runner.invoke(app, ["web-sample", "--segment", "velas", "--out", str(out_path)])

    assert result.exit_code == 0, result.stdout
    out = json.loads(result.stdout)
    assert out["command"] == "web-sample" and out["rows"] == 3
    # a data de corte da amostra sai no envelope: é o --as-of do web-eval
    assert out["as_of"] == as_of.isoformat()
    assert out_path.exists()


def test_web_sample_refuses_incomplete_query_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    async def _fake(segment: str, seed: int, out: Path, allow_incomplete: bool) -> WebSampleRun:
        _ = segment, seed, out, allow_incomplete
        return WebSampleRun(
            rows=0, as_of=datetime(2026, 10, 19, tzinfo=UTC), missing_queries=["q1:seed:00"]
        )

    monkeypatch.setattr("content_factory.cli.main._aweb_sample", _fake)
    result = runner.invoke(
        app, ["web-sample", "--segment", "velas", "--out", str(tmp_path / "s.csv")]
    )

    assert result.exit_code != 0
    out = json.loads(result.stdout)
    assert out["ok"] is False and out["error"]["code"] == "incomplete_cycle"
    assert "q1:seed:00" in out["error"]["message"]


def test_web_eval_envelope_without_ranking_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    report = WebEvalReport(
        ok=True,
        segment="velas",
        kept_annotated=45,
        p5=Interval(point=0.8, low=0.6, high=1.0, n=45),
        p10=Interval(point=0.7, low=0.5, high=0.9, n=45),
        gain_p10=Interval(point=0.2, low=0.05, high=0.4, n=45),
        coverage=0.95,
        verdict="aprovado",
        correlation=0.6,
        useful_top10=0.5,
        ranking_v1=["https://a.example"],
        ranking_base=["https://b.example"],
    )

    seen: list[datetime] = []

    async def _fake(segment: str, csv_path: Path, as_of: datetime) -> WebEvalReport:
        _ = segment, csv_path
        seen.append(as_of)
        return report

    monkeypatch.setattr("content_factory.cli.main._aweb_eval", _fake)
    result = runner.invoke(
        app,
        [
            "web-eval",
            "--segment",
            "velas",
            "--csv",
            "x.csv",
            "--as-of",
            "2026-10-19T23:00:00+00:00",
        ],
    )

    assert result.exit_code == 0, result.stdout
    out = json.loads(result.stdout)
    assert out["command"] == "web-eval" and out["verdict"] == "aprovado"
    assert out["p5"] == {"point": 0.8, "low": 0.6, "high": 1.0, "n": 45}
    assert "ranking_v1" not in out  # rankings só com --explain
    assert seen == [datetime(2026, 10, 19, 23, 0, tzinfo=UTC)]


def test_web_eval_requires_timezone_aware_as_of() -> None:
    import json

    result = runner.invoke(
        app,
        ["web-eval", "--segment", "velas", "--csv", "x.csv", "--as-of", "2026-10-19T23:00"],
    )

    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "usage_error"
