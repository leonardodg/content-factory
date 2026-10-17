"""Use-case `remediate` (R7) — plano 2026-10-16-viral-por-tema-bloco-3.

O contrato é o da coleta: uma `CollectRun` `remediate:<plataforma>` por
plataforma, cota contada por `counting()`, breaker via `report_discover` e
erro de contrato virando run `failed` sem derrubar o caller.
"""

from datetime import UTC, datetime

from tests.stubs import FakeHealth, FakeLimiter, FakeRefresher, FakeRemediationRepo, FakeRuns

from content_factory.app.ports import RefreshableCollector
from content_factory.app.remediate import RemediationDeps, RemediationOutcome, remediate
from content_factory.domain.errors import RateLimited


def _clock() -> datetime:
    return datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def remediation_deps(
    repo: FakeRemediationRepo,
    *,
    refreshables: dict[str, RefreshableCollector],
    runs: FakeRuns | None = None,
    health: FakeHealth | None = None,
    limit: int = 500,
    accepted_language: str | None = None,
) -> RemediationDeps:
    """`RemediationDeps` com clock fixo e portas em memória (R7).

    Os fakes observáveis (`runs`/`health`) entram explicitamente: os campos de
    `RemediationDeps` são os Protocolos, então o teste passa a instância
    concreta e asserta nela.
    """
    return RemediationDeps(
        videos=repo,
        refreshables=refreshables,
        runs=runs if runs is not None else FakeRuns(),
        limiter=FakeLimiter(),
        health=health if health is not None else FakeHealth(),
        clock=_clock,
        limit=limit,
        accepted_language=accepted_language,
    )


async def test_remediate_grava_metricas_e_marca_removidos() -> None:
    repo = FakeRemediationRepo(targets=["v1", "v2"])
    runs = FakeRuns()
    deps = remediation_deps(repo, refreshables={"youtube": FakeRefresher(found=["v2"])}, runs=runs)
    out = await remediate(segment_id=1, deps=deps)
    assert (out.targets, out.metrics_written, out.removed) == (2, 1, 1)
    assert repo.removed == [1]  # video_id do v1
    assert [r.collector for r in runs.saved] == ["remediate:youtube"]


async def test_remediate_sem_alvos_nao_chama_coletor_nem_grava_run() -> None:
    refresher = FakeRefresher(found=[])
    runs = FakeRuns()
    deps = remediation_deps(
        FakeRemediationRepo(targets=[]), refreshables={"youtube": refresher}, runs=runs
    )
    out = await remediate(segment_id=1, deps=deps)
    assert out == RemediationOutcome()
    assert refresher.calls == 0 and runs.saved == []


async def test_erro_de_contrato_vira_run_failed_e_nao_levanta() -> None:
    refresher = FakeRefresher(error=RateLimited("youtube", "quota"))
    runs = FakeRuns()
    health = FakeHealth()
    deps = remediation_deps(
        FakeRemediationRepo(targets=["v1"]),
        refreshables={"youtube": refresher},
        runs=runs,
        health=health,
    )
    out = await remediate(segment_id=1, deps=deps)
    assert out.failed_platforms == 1 and out.removed == 0  # erro NÃO marca removido
    assert refresher.error is not None
    run = runs.saved[0]
    assert run.status == "failed" and run.error_code == refresher.error.code
    assert health.failures == ["youtube"]


async def test_cota_da_remedicao_vai_para_a_run() -> None:
    runs = FakeRuns()
    deps = remediation_deps(
        FakeRemediationRepo(targets=[f"v{i}" for i in range(120)]),
        refreshables={"youtube": FakeRefresher(found="all", quota_per_batch=2)},
        runs=runs,
    )
    out = await remediate(segment_id=1, deps=deps)
    assert out.quota_used == runs.saved[0].quota_used == 3 * 2  # 3 lotes de 50


async def test_alvo_fora_do_idioma_nao_e_remedido() -> None:
    """R14/17-10: com `accepted_language: pt-BR`, o `pt-PT` sai antes do coletor.

    Mesma regra da coleta: `pt` sem região e idioma não declarado ficam. O
    descartado não consome cota, não vira métrica e não entra no `found` da run
    (é `skipped_language` no sub-envelope)."""
    refresher = FakeRefresher(found="all")
    repo = FakeRemediationRepo(targets=["v1", "v2", "v3"], languages={"v2": "pt-PT", "v3": "pt"})
    runs = FakeRuns()
    deps = remediation_deps(
        repo, refreshables={"youtube": refresher}, runs=runs, accepted_language="pt-BR"
    )
    out = await remediate(segment_id=1, deps=deps)
    assert (out.targets, out.skipped_language, out.refreshed) == (2, 1, 2)
    assert [t.platform_video_id for t in refresher.asked] == ["v1", "v3"]
    assert [t.platform_video_id for t, _, _ in repo.metrics] == ["v1", "v3"]
    assert runs.saved[0].found == 2
    assert out.removed == 0


async def test_todos_fora_do_idioma_nao_chama_coletor() -> None:
    """Sem alvo aceito, a remediação não abre run nem gasta cota."""
    refresher = FakeRefresher()
    runs = FakeRuns()
    deps = remediation_deps(
        FakeRemediationRepo(targets=["v1"], languages={"v1": "pt-PT"}),
        refreshables={"youtube": refresher},
        runs=runs,
        accepted_language="pt-BR",
    )
    out = await remediate(segment_id=1, deps=deps)
    assert out == RemediationOutcome(skipped_language=1)
    assert refresher.calls == 0 and runs.saved == [] and out.removed == 0


async def test_sem_idioma_aceito_nada_e_descartado() -> None:
    """Segmento sem exigência de idioma: a remediação mantém todos os alvos."""
    refresher = FakeRefresher()
    deps = remediation_deps(
        FakeRemediationRepo(targets=["v1"], languages={"v1": "pt-PT"}),
        refreshables={"youtube": refresher},
    )
    out = await remediate(segment_id=1, deps=deps)
    assert (out.targets, out.skipped_language) == (1, 0)
    assert [t.platform_video_id for t in refresher.asked] == ["v1"]
