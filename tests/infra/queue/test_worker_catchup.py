"""Catch-up de cron no worker — plano 2026-10-16-worker-resiliencia-ranking T2."""

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from content_factory.domain.models import Segment
from content_factory.infra.queue import worker as w

BRT = ZoneInfo("America/Sao_Paulo")


class _Campaigns:
    def __init__(self, segments: list[Segment]) -> None:
        self._segments = segments

    async def active_segments(self) -> list[Segment]:
        return self._segments


class _JobRuns:
    def __init__(self, last: dict[str, datetime | None]) -> None:
        self.last = last
        self.asked: list[list[int]] = []

    async def last_runs(self, segment_ids: list[int]) -> dict[str, datetime | None]:
        self.asked.append(segment_ids)
        return self.last


def _seg(i: int) -> Segment:
    return Segment(id=i, slug=f"s{i}", name="S", platforms=["web"], keywords=["k"])


def _ctx(segments: list[Segment], last: dict[str, datetime | None]) -> dict[str, Any]:
    return {"campaigns_deps": _Campaigns(segments), "job_runs": _JobRuns(last), "app_deps": None}


def _fake_jobs(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def make(name: str) -> Any:
        async def job(_ctx: dict[str, Any]) -> dict[str, Any]:
            calls.append(name)
            return {"ok": True, "partial": False, "segments": {}}

        return job

    monkeypatch.setattr(w, "run_research_job", make("research"))
    monkeypatch.setattr(w, "run_collect_job", make("collect"))
    monkeypatch.setattr(w, "run_score_job", make("score"))


async def test_catchup_runs_missed_jobs_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _fake_jobs(monkeypatch, calls)
    monkeypatch.setattr(w, "_now", lambda: datetime(2026, 10, 15, 10, 12, tzinfo=BRT))
    ctx = _ctx([_seg(1), _seg(2)], {"research": None, "collect": None, "score": None})

    out = await w.run_catchup_job(ctx)

    assert calls == ["research", "collect", "score"]
    assert out["ok"] is True
    assert out["missed"] == ["research", "collect", "score"]
    assert set(out["ran"]) == {"research", "collect", "score"}
    assert ctx["job_runs"].asked == [[1, 2]]


async def test_catchup_noop_when_nothing_missed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _fake_jobs(monkeypatch, calls)
    monkeypatch.setattr(w, "_now", lambda: datetime(2026, 10, 16, 11, 0, tzinfo=BRT))
    last: dict[str, datetime | None] = {
        "research": datetime(2026, 10, 16, 7, 0, tzinfo=BRT),
        "collect": datetime(2026, 10, 16, 8, 0, tzinfo=BRT),
        "score": datetime(2026, 10, 16, 8, 30, tzinfo=BRT),
    }

    out = await w.run_catchup_job(_ctx([_seg(1)], last))

    assert calls == []
    assert out == {"ok": True, "missed": [], "ran": {}}


async def test_catchup_noop_without_active_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _fake_jobs(monkeypatch, calls)
    ctx = _ctx([], {"research": None, "collect": None, "score": None})

    out = await w.run_catchup_job(ctx)

    assert calls == []
    assert out == {"ok": True, "missed": [], "ran": {}}
    assert ctx["job_runs"].asked == []


async def test_catchup_job_failure_does_not_stop_the_next(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _fake_jobs(monkeypatch, calls)

    async def boom(_ctx: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("banco fora")

    monkeypatch.setattr(w, "run_research_job", boom)
    monkeypatch.setattr(w, "_now", lambda: datetime(2026, 10, 15, 10, 12, tzinfo=BRT))

    out = await w.run_catchup_job(_ctx([_seg(1)], {}))

    assert calls == ["collect", "score"]
    assert out["ok"] is False
    assert out["ran"]["research"]["error"]["type"] == "RuntimeError"


def test_catchup_schedules_match_registered_crons() -> None:
    """Catch-up e cron leem os MESMOS horários — não podem divergir."""
    by_name = {cj.name: cj for cj in w.WorkerSettings.cron_jobs}
    for sched in w.catchup_schedules():
        cj = by_name[f"{sched.job}_job"]
        assert isinstance(cj.hour, set)
        assert {t.hour for t in sched.times} == cj.hour
        assert isinstance(cj.minute, int)
        assert {t.minute for t in sched.times} == {cj.minute}
    assert [s.job for s in w.catchup_schedules()] == ["research", "collect", "score"]
    assert w.catchup_schedules()[0].times == (time(7, 0),)


def test_catchup_job_is_registered_with_combined_timeout() -> None:
    funcs = {f.name: f for f in w.WorkerSettings.functions}
    assert "catchup_job" in funcs
    expected = w.RESEARCH_JOB_TIMEOUT_S + w.COLLECT_JOB_TIMEOUT_S + w.SCORE_JOB_TIMEOUT_S
    assert funcs["catchup_job"].timeout_s == expected


class _FakePool:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_job(self, name: str, **kwargs: Any) -> None:
        self.enqueued.append((name, kwargs))


class _Deps:
    campaigns = object()
    job_runs = object()

    async def close(self) -> None:
        pass


async def _fake_create(_settings: object) -> object:
    return _Deps()


async def test_startup_enqueues_catchup_with_hourly_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COLLECT_SEGMENT", raising=False)
    monkeypatch.delenv("CATCHUP_ON_STARTUP", raising=False)
    monkeypatch.setattr(w, "create_app_deps", _fake_create)
    monkeypatch.setattr(w, "_now", lambda: datetime(2026, 10, 16, 11, 6, tzinfo=BRT))
    pool = _FakePool()
    ctx: dict[str, Any] = {"redis": pool}

    await w.startup(ctx)

    # id por hora: restarts seguidos na mesma hora não duplicam o catch-up
    assert pool.enqueued == [("catchup_job", {"_job_id": "catchup:2026-10-16T14"})]
    assert ctx["job_runs"] is _Deps.job_runs


async def test_startup_skips_catchup_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COLLECT_SEGMENT", raising=False)
    monkeypatch.setenv("CATCHUP_ON_STARTUP", "0")
    monkeypatch.setattr(w, "create_app_deps", _fake_create)
    pool = _FakePool()

    await w.startup({"redis": pool})

    assert pool.enqueued == []
