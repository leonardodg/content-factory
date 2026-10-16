"""Catch-up de cron no startup do worker — plano 2026-10-16-worker-resiliencia-ranking T2."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from content_factory.app.catchup import CronSchedule, last_slot, missed_jobs

BRT = ZoneInfo("America/Sao_Paulo")
SCHEDULES = [
    CronSchedule("research", (time(7, 0),)),
    CronSchedule("collect", (time(8, 0), time(20, 0))),
    CronSchedule("score", (time(8, 30), time(21, 30))),
]


def _brt(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=BRT)


def test_last_slot_is_latest_occurrence_not_after_now() -> None:
    times = (time(8, 0), time(20, 0))
    assert last_slot(_brt(16, 11), times, BRT) == _brt(16, 8)
    assert last_slot(_brt(16, 21), times, BRT) == _brt(16, 20)
    # antes do primeiro horário do dia: o último foi ontem
    assert last_slot(_brt(16, 7), times, BRT) == _brt(15, 20)
    # exatamente no horário conta como já agendado
    assert last_slot(_brt(16, 8), times, BRT) == _brt(16, 8)


def test_nothing_missed_when_every_job_ran_after_its_last_slot() -> None:
    now = _brt(16, 11)
    last_runs = {
        "research": _brt(16, 7, 0) + timedelta(seconds=1),
        "collect": _brt(16, 8, 0),
        "score": _brt(16, 8, 30),
    }
    assert missed_jobs(now, SCHEDULES, last_runs, BRT) == []


def test_morning_lost_to_downtime_runs_all_three_in_order() -> None:
    # incidente de 15/10: máquina fora 00:29 a 10:12, manhã inteira perdida
    now = _brt(15, 10, 12)
    last_runs = {
        "research": _brt(14, 7),
        "collect": _brt(14, 20),
        "score": _brt(14, 21, 30),
    }
    assert missed_jobs(now, SCHEDULES, last_runs, BRT) == ["research", "collect", "score"]


def test_never_ran_counts_as_missed() -> None:
    now = _brt(16, 11)
    assert missed_jobs(now, SCHEDULES, {}, BRT) == ["research", "collect", "score"]


def test_skips_job_whose_next_slot_is_within_min_gap() -> None:
    # volta 08:05: score das 21:30 perdido, mas o das 08:30 vem em 25 min
    now = _brt(16, 8, 5)
    last_runs = {
        "research": _brt(16, 7),
        "collect": _brt(16, 8),
        "score": _brt(15, 8, 30),
    }
    assert missed_jobs(now, SCHEDULES, last_runs, BRT) == []


def test_min_gap_is_configurable() -> None:
    now = _brt(16, 8, 5)
    last_runs = {"research": _brt(16, 7), "collect": _brt(16, 8), "score": _brt(15, 8, 30)}
    assert missed_jobs(now, SCHEDULES, last_runs, BRT, min_gap=timedelta(0)) == ["score"]


def test_last_run_compares_across_timezones() -> None:
    # collect_runs grava em UTC; 08:00 BRT = 11:00 UTC
    now = _brt(16, 11)
    last_runs = {
        "research": datetime(2026, 10, 16, 10, 0, tzinfo=UTC),
        "collect": datetime(2026, 10, 16, 11, 0, tzinfo=UTC),
        "score": datetime(2026, 10, 16, 11, 30, tzinfo=UTC),
    }
    assert missed_jobs(now, SCHEDULES, last_runs, BRT) == []


def test_run_started_a_little_before_slot_still_counts() -> None:
    # tolerância de relógio: o cron pode registrar a run segundos antes do minuto
    now = _brt(16, 11)
    last_runs = {
        "research": _brt(16, 6, 59) + timedelta(seconds=50),
        "collect": _brt(16, 8),
        "score": _brt(16, 8, 30),
    }
    assert missed_jobs(now, SCHEDULES, last_runs, BRT) == []
