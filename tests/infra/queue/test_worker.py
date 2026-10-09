import pytest

from content_factory.app.collect import CollectDeps
from content_factory.domain.errors import SegmentNotFound
from content_factory.infra.queue import worker as w
from tests.stubs import FakeLimiter, FakeLock, FakeRepos


class CountingDeps:
    def __init__(self) -> None:
        self.collect_calls = 0


async def test_run_collect_job_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(slug)
        raise SystemExit  # sai imediato — só conta a chamada

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(
        w, "_load_settings", lambda: {"segment": "candles", "platforms": ["youtube"], "limit": 10}
    )
    with pytest.raises(SystemExit):
        await w.run_collect_job({"collect_deps": None})
    assert calls == ["candles"]


def test_cron_hours_parse() -> None:
    assert w.parse_cron_hours("08,20") == {8, 20}
    assert w.parse_cron_hours("") == {8, 20}  # default


async def test_collect_job_empty_platforms_fall_back_to_segment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`COLLECT_PLATFORMS` vazio → usa as plataformas declaradas do segmento."""
    calls: list[list[str]] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(platforms)
        raise SystemExit

    cfg: dict[str, object] = {"segment": "candles", "platforms": [], "limit": 5}
    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: cfg)
    repos = FakeRepos()
    deps = CollectDeps(
        collectors=[],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    with pytest.raises(SystemExit):
        await w.run_collect_job({"collect_deps": deps, "app_deps": None})
    assert calls == [["youtube", "tiktok"]]  # plataformas do segmento (stub)


def test_workersettings_registers_jobs_and_cron() -> None:
    """Contrato: `functions` = collect_job/score_job; cron 2x/dia nesses jobs.

    Asserts diretos nas classes de settings (o `create_worker` do arq é
    validado de ponta a ponta na subida do container worker).
    """
    settings = w.WorkerSettings
    assert {f.name for f in settings.functions} == {"collect_job", "score_job"}
    assert [cj.coroutine for cj in settings.cron_jobs] == [w.run_collect_job, w.run_score_job]
    assert [cj.minute for cj in settings.cron_jobs] == [0, 30]
    assert all(cj.unique for cj in settings.cron_jobs)
    assert settings.on_startup is w.startup
    assert settings.on_job_end is w.job_end
    assert settings.on_shutdown is w.shutdown


def test_workersettings_max_jobs_is_one() -> None:
    """Sessão única compartilhada: 1 job por vez (arq default é 10)."""
    assert w.WorkerSettings.max_jobs == 1


def test_workersettings_timezone_is_sao_paulo() -> None:
    """Cron dispara 08:00/20:00 no horário local do projeto, não em UTC."""
    from zoneinfo import ZoneInfo

    tz = w.WorkerSettings.timezone
    assert isinstance(tz, ZoneInfo)
    assert tz.key == "America/Sao_Paulo"


async def test_startup_failure_then_shutdown_propagates_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """arq chama on_shutdown no finally; falha de startup não vira KeyError."""

    async def boom(_settings: object) -> w.AppDeps:
        raise SegmentNotFound("candles")

    monkeypatch.setattr(w, "create_app_deps", boom)
    ctx: dict[str, object] = {}
    with pytest.raises(SegmentNotFound):
        try:
            await w.startup(ctx)
        finally:
            await w.shutdown(ctx)


async def test_shutdown_closes_app_deps_when_present() -> None:
    closed: list[str] = []

    class FakeAppDeps:
        async def close(self) -> None:
            closed.append("close")

    await w.shutdown({"app_deps": FakeAppDeps()})
    assert closed == ["close"]
