"""Catch-up de cron no startup do worker (plano 2026-10-16-worker-resiliencia-ranking T2).

O arq calcula o próximo disparo de cada cron a partir do startup e nunca
recupera horário perdido (worker parado, máquina desligada). Aqui fica a
decisão pura: dado o horário atual e a última run de cada job, quais jobs
perderam o horário mais recente e precisam rodar agora.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo

# o cron pode gravar a run segundos antes do minuto cheio (relógio/arredondamento)
_CLOCK_TOLERANCE = timedelta(minutes=1)
DEFAULT_MIN_GAP = timedelta(minutes=60)


@dataclass(frozen=True)
class CronSchedule:
    job: str
    times: tuple[time, ...]  # horários locais (no `tz` do worker)


def _occurrences(now: datetime, times: tuple[time, ...], tz: tzinfo) -> list[datetime]:
    """Horários de ontem, hoje e amanhã no fuso `tz`, ordenados."""
    today = now.astimezone(tz).date()
    days = (today - timedelta(days=1), today, today + timedelta(days=1))
    return sorted(datetime.combine(d, t, tzinfo=tz) for d in days for t in times)


def last_slot(now: datetime, times: tuple[time, ...], tz: tzinfo) -> datetime:
    """Horário agendado mais recente `<= now`."""
    return max(s for s in _occurrences(now, times, tz) if s <= now)


def next_slot(now: datetime, times: tuple[time, ...], tz: tzinfo) -> datetime:
    """Próximo horário agendado `> now`."""
    return min(s for s in _occurrences(now, times, tz) if s > now)


def missed_jobs(
    now: datetime,
    schedules: Sequence[CronSchedule],
    last_runs: Mapping[str, datetime | None],
    tz: tzinfo,
    min_gap: timedelta = DEFAULT_MIN_GAP,
) -> list[str]:
    """Jobs cujo horário mais recente passou sem run, na ordem de `schedules`.

    Só o horário mais recente conta: depois de dias parado, cada job roda uma
    vez. Job cujo próximo horário está a menos de `min_gap` fica para o cron.
    """
    missed: list[str] = []
    for sched in schedules:
        if next_slot(now, sched.times, tz) - now < min_gap:
            continue
        slot = last_slot(now, sched.times, tz)
        ran = last_runs.get(sched.job)
        if ran is None or ran < slot - _CLOCK_TOLERANCE:
            missed.append(sched.job)
    return missed
