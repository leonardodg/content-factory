# Bloco 3 — Remedição dos vídeos conhecidos e agendamento de 6 h (R7+R12)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer a curva de engajamento existir. Hoje só **103 de 427** vídeos têm ≥ 2 medições porque um vídeo só é remedido quando a busca o encontra de novo. Aqui a cada coleta remedimos os vídeos do segmento descobertos nos últimos 7 dias, e o collect/score passam a rodar 4x/dia por env.

**Architecture:** A remedição é um use-case próprio (`app/remediate.py`) com uma porta mínima (`RemediationRepository`) e uma porta nova de coletor (`RefreshableCollector.refresh`) — **só o YouTube implementa** (Instagram é mídia própria, TikTok fora de escopo). Ela **não** reusa `upsert_candidates` (que sobrescreveria metadados e `discovery_source`): grava só `video_metrics`. Roda dentro de `run_collect_job`, na mesma UoW do segmento, logo após `collect()`. R12 é trocar constantes de módulo por leitura de env.

**Tech Stack:** Python 3.14, SQLAlchemy 2 async, Alembic, httpx, arq, pytest (asyncio), ruff, pyright strict.

## Global Constraints

- `domain/` puro; `app/` → portas; `infra/` implementa portas; `cli/worker → app → domain`.
- `pyright` strict 0 erros; `ruff` linha 100/aspas duplas; `ruff format` limpo; TDD.
- Envelope só com contagens; sem segredo nem caminho absoluto de home rastreado (`scripts/check_privacy.py`).
- `make gates` antes de "pronto" (inclui `alembic check`).
- Migração aditiva: `0013` segue `0012` (Bloco 2).
- Não tocar `video_scores`/`viral_score`/`FORMULA_VERSION`/`WEIGHTS`.

## Referência

Especificação §4 R7 e R12; §5 (cota: remedição = 3×4×10 lotes = 120 unidades/dia). **Independente dos Blocos 1–2** no código, mas o deploy é único; a ordem da §7 põe R7+R12 em terceiro.

## Decisões deste bloco

- **Remedição dentro da coleta** (não job separado): mantém 1 UoW/commit por segmento e evita um 5º cron. A duração da coleta deve seguir bem abaixo de 6 h (QA mede pelo `ops-report` ticks).
- **YouTube em lotes de 50 ids** (`videos.list`, 1 unidade por lote), teto `REMEDIATION_LIMIT=500` por segmento por coleta (10 lotes).
- **`RefreshableCollector` é porta separada** de `Collector`: não obriga Instagram/TikTok (nem seus fakes) a implementar `refresh`.
- **`videos.removed_at`** (coluna nova) marca removido/privado; alvo de remedição é `removed_at IS NULL`.
- **Intervalo mínimo entre medições** (revisão QA): o alvo da remedição exclui vídeos com medição nas últimas `REMEDIATION_MIN_GAP_HOURS=3` h. Sem isso, o vídeo que a coleta acabou de gravar é remedido **na mesma transação**; como `now()` no Postgres é a hora de início da transação, as duas medições ficam com o mesmo `captured_at`, o `momentum_score` vê intervalo ≤ 0 e a velocidade sai `None` justamente nos vídeos que estão em alta.
- **Remedição com o mesmo contrato da coleta** (revisão QA): `limiter.acquire` por lote, `counting()` para a cota, `report_discover` no breaker e uma `CollectRun` `remediate:<plataforma>`. Erro de contrato (cota, rede) vira run `failed` e **não** derruba a coleta já feita na mesma UoW; exceção fora do contrato propaga (como no collect).
- Envs novas: `SCORE_CRON_HOURS` (= collect), `REMEDIATION_WINDOW_DAYS=7`, `REMEDIATION_LIMIT=500`, `REMEDIATION_MIN_GAP_HOURS=3`.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/models.py` (editar) | `RemediationTarget`, `RefreshResult`. |
| `src/content_factory/app/ports.py` (editar) | `RefreshableCollector`, `RemediationRepository`. |
| `src/content_factory/infra/db/orm.py` (editar) | `VideoRow.removed_at`. |
| `alembic/versions/0013_videos_removed_at.py` (novo) | Coluna `videos.removed_at`. |
| `src/content_factory/infra/db/repositories.py` (editar) | `remediation_targets`, `record_metrics`, `mark_removed`. |
| `src/content_factory/infra/collectors/youtube.py` (editar) | `refresh()` (lotes de 50 + `channels.list`), reusa `_map`. |
| `src/content_factory/app/remediate.py` (novo) | Use-case `remediate()` — puro de I/O via portas. |
| `src/content_factory/infra/queue/worker.py` (editar) | R12: `_collect_hours`/`_score_hours` por env; remedição no collect. |
| `src/content_factory/infra/queue/composition.py` (editar) | `bundle.remediate`. |
| `tests/...` | TDD por arquivo. |

---

### Task 1: Horários por env (R12)

**Files:**
- Modify: `src/content_factory/infra/queue/worker.py`
- Test: `tests/infra/queue/test_worker_catchup.py`, `tests/infra/queue/test_worker.py` (editar)

**Interfaces:**
- Produces:
  - `_collect_hours() -> set[int]` lê `COLLECT_CRON_HOURS`, **default `"02,08,14,20"`**.
  - `_score_hours() -> set[int]` lê `SCORE_CRON_HOURS`, default = `_collect_hours()`.
  - `catchup_schedules()` e `WorkerSettings.cron_jobs` usam essas funções.

- [ ] **Step 1: Testes que falham**

```python
# tests/infra/queue/test_worker_catchup.py  (adicionar)
def test_collect_e_score_4x_por_dia_por_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECT_CRON_HOURS", "02,08,14,20")
    monkeypatch.setenv("SCORE_CRON_HOURS", "02,08,14,20")
    sched = {s.job: s.times for s in w.catchup_schedules()}
    assert sched["collect"] == (time(2, 0), time(8, 0), time(14, 0), time(20, 0))
    assert sched["score"] == (time(2, 30), time(8, 30), time(14, 30), time(20, 30))


def test_score_default_igual_ao_collect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLLECT_CRON_HOURS", "06,18")
    monkeypatch.delenv("SCORE_CRON_HOURS", raising=False)
    sched = {s.job: s.times for s in w.catchup_schedules()}
    assert sched["score"] == (time(6, 30), time(18, 30))
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/infra/queue/test_worker_catchup.py -q` → FAIL (default ainda `08,20`, sem env de score).

- [ ] **Step 3: Implementar**

```python
def _collect_hours() -> set[int]:
    return parse_cron_hours(os.getenv("COLLECT_CRON_HOURS", "02,08,14,20"))


def _score_hours() -> set[int]:
    """R12: score acompanha o collect por padrão (minuto 30, R7 dentro da coleta)."""
    raw = os.getenv("SCORE_CRON_HOURS")
    return parse_cron_hours(raw) if raw is not None else _collect_hours()
```

Em `catchup_schedules`, trocar `SCORE_HOURS` por `_score_hours()`; em `WorkerSettings.cron_jobs`, trocar `hour=SCORE_HOURS` por `hour=_score_hours()`.

Remover as constantes `SCORE_HOURS`/`COLLECT_*` de módulo agora substituídas (o teste existente `test_catchup_schedules_match_registered_crons` deve continuar verde — ele compara cron registrado × `catchup_schedules()`, ambos passam a ler env).

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/infra/queue/ -q` → PASS (novos + `test_catchup_schedules_match_registered_crons`).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/queue/worker.py tests/infra/queue/
git commit -m "feat(worker): collect/score 4x/dia por env COLLECT_CRON_HOURS/SCORE_CRON_HOURS (R12)"
```

---

### Task 2: Coluna `videos.removed_at` (migração `0013`)

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`
- Create: `alembic/versions/0013_videos_removed_at.py`
- Test: `tests/infra/test_orm.py` (editar)

**Interfaces:**
- Produces: `VideoRow.removed_at: datetime | None`.

- [ ] **Step 1: Teste que falha**

```python
def test_videos_tem_removed_at() -> None:
    from content_factory.infra.db.orm import VideoRow

    col = VideoRow.__table__.columns["removed_at"]
    assert col.nullable is True
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`KeyError: 'removed_at'`).

- [ ] **Step 3: Implementar ORM + migração**

```python
# orm.py, em VideoRow (após last_collected_at)
    # R7: marcado quando a remedição não encontra mais o vídeo (removido/privado)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

```python
# alembic/versions/0013_videos_removed_at.py
"""videos.removed_at — parar de remedir removidos/privados (R7)

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "removed_at")
```

- [ ] **Step 4: Rodar e ver passar + `uv run alembic check`** → PASS, sem drift.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/orm.py alembic/versions/0013_videos_removed_at.py tests/infra/test_orm.py
git commit -m "feat(db): migração 0013 — videos.removed_at (R7)"
```

---

### Task 3: DTOs e portas da remedição

**Files:**
- Modify: `src/content_factory/domain/models.py`
- Modify: `src/content_factory/app/ports.py`
- Test: `tests/app/test_ports.py` (editar)

**Interfaces:**
- Produces:
  - `class RemediationTarget(BaseModel): video_id: int; platform: str; platform_video_id: str`
  - `class RefreshResult(BaseModel): candidates: list[CandidateVideo] = []; missing_ids: list[str] = []`
  - `RefreshableCollector` (Protocol): `platform: ClassVar[str]`; `async def refresh(self, targets: Sequence[RemediationTarget]) -> RefreshResult`
  - `RemediationRepository` (Protocol):
    - `async def remediation_targets(self, segment_id: int, since_days: int, limit: int, min_gap_hours: int, platforms: Sequence[str]) -> list[RemediationTarget]`
    - `async def record_metrics(self, target: RemediationTarget, metrics: VideoMetrics, followers_at_capture: int | None) -> None`
    - `async def mark_removed(self, video_ids: Sequence[int]) -> int`

- [ ] **Step 1: Teste que falha**

```python
# tests/app/test_ports.py  (adicionar)
def test_refresh_result_e_remediation_target() -> None:
    from content_factory.app.ports import RefreshableCollector, RemediationRepository
    from content_factory.domain.models import RemediationTarget, RefreshResult

    assert RefreshableCollector.__name__ == "RefreshableCollector"
    assert RemediationRepository.__name__ == "RemediationRepository"
    t = RemediationTarget(video_id=1, platform="youtube", platform_video_id="abc")
    assert RefreshResult().missing_ids == [] and t.platform == "youtube"
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** (models + ports conforme Interfaces).

No `models.py`:
```python
class RemediationTarget(BaseModel):
    """R7: alvo de remedição — identificadores só, sem métrica."""

    video_id: int
    platform: str
    platform_video_id: str


class RefreshResult(BaseModel):
    """R7: o que a remedição achou e o que sumiu (removido/privado)."""

    candidates: list[CandidateVideo] = Field(default_factory=list[CandidateVideo])
    missing_ids: list[str] = Field(default_factory=list[str])
```

Em `ports.py`: dois `Protocol`. `RefreshableCollector.refresh` recebe `Sequence[RemediationTarget]` e devolve `RefreshResult`; `RemediationRepository` com os 3 métodos.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/models.py src/content_factory/app/ports.py tests/app/test_ports.py
git commit -m "feat(ports): RefreshableCollector e RemediationRepository (R7)"
```

---

### Task 4: `YoutubeCollector.refresh`

**Files:**
- Modify: `src/content_factory/infra/collectors/youtube.py`
- Test: `tests/infra/collectors/test_youtube.py` (editar)

**Interfaces:**
- Consumes: `RemediationTarget`, `RefreshResult`, `_map`.
- Produces: `YoutubeCollector.refresh(targets)` — lotes de 50; ids que o `videos.list` não devolveu viram `missing_ids` (removido/privado); cota 1 unidade/lote.

- [ ] **Step 1: Teste que falha**

```python
def test_refresh_lotes_de_50_e_detecta_removido() -> None:
    # transport devolve só "v2" no videos.list (v1 sumiu) e um channel
    col = _collector_with_transport(_youtube_refresh_transport(returned=["v2"]))
    out = asyncio.run(
        col.refresh(
            [
                RemediationTarget(video_id=1, platform="youtube", platform_video_id="v1"),
                RemediationTarget(video_id=2, platform="youtube", platform_video_id="v2"),
            ]
        )
    )
    assert [c.platform_video_id for c in out.candidates] == ["v2"]
    assert out.missing_ids == ["v1"]
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`AttributeError: refresh`).

- [ ] **Step 3: Implementar**

```python
    async def refresh(self, targets: Sequence[RemediationTarget]) -> RefreshResult:
        """R7: `videos.list` em lotes de 50 + `channels.list`; ids ausentes = removidos."""
        found: list[CandidateVideo] = []
        missing: list[str] = []
        ids = [t.platform_video_id for t in targets]
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            stats = await self._get(
                "videos",
                {"part": "statistics,snippet,contentDetails", "id": ",".join(chunk)},
            )
            record_quota(self.COST_LIST)
            stats_by_id = {str(v["id"]): v for v in stats.get("items", [])}
            missing += [i for i in chunk if i not in stats_by_id]
            if not stats_by_id:
                continue
            channels = await self._get(
                "channels",
                {
                    "part": "statistics",
                    "id": ",".join({v["snippet"]["channelId"] for v in stats_by_id.values()}),
                },
            )
            record_quota(self.COST_LIST)
            followers = {
                str(c["id"]): int(c["statistics"].get("subscriberCount", 0))
                for c in channels.get("items", [])
            }
            found += [self._map(v, followers) for v in stats_by_id.values()]
        return RefreshResult(candidates=found, missing_ids=missing)
```

Import `RemediationTarget`, `RefreshResult`, `Sequence`.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/collectors/youtube.py tests/infra/collectors/test_youtube.py
git commit -m "feat(youtube): refresh em lotes de 50 e detecção de removidos (R7)"
```

---

### Task 5: Repositório da remedição

**Files:**
- Modify: `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Produces: `VideoRepositoryImpl` passa a satisfazer `RemediationRepository`.

- [ ] **Step 1: Teste que falha** (integração)

```python
async def test_remediation_targets_exclui_removidos_e_medidos_ha_pouco() -> None:
    # segmento + 3 vídeos youtube: v1 removido; v2 medido há 5 h; v3 medido
    # AGORA (mesma transação, como a coleta que acabou de gravá-lo)
    targets = await repo.remediation_targets(
        seg_id, since_days=7, limit=500, min_gap_hours=3, platforms=["youtube"]
    )
    assert [t.platform_video_id for t in targets] == ["v2"]
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
async def remediation_targets(
    self,
    segment_id: int,
    since_days: int,
    limit: int,
    min_gap_hours: int,
    platforms: Sequence[str],
) -> list[RemediationTarget]:
    # func.now() (hora da transação), não datetime.now(): a medição que a
    # coleta gravou NESTA transação tem captured_at = now() e precisa cair.
    recent = (
        select(VideoMetricsRow.id)
        .where(
            VideoMetricsRow.video_id == VideoRow.id,
            VideoMetricsRow.captured_at > func.now() - timedelta(hours=min_gap_hours),
        )
        .exists()
    )
    stmt = (
        select(VideoRow.id, VideoRow.platform, VideoRow.platform_video_id)
        .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoRow.id)
        .where(
            VideoSegmentRow.segment_id == segment_id,
            VideoSegmentRow.discovered_at >= func.now() - timedelta(days=since_days),
            VideoRow.removed_at.is_(None),
            VideoRow.platform.in_(list(platforms)),
            ~recent,
        )
        .order_by(VideoSegmentRow.discovered_at.desc())
        .limit(limit)
    )
    rows = (await self._s.execute(stmt)).all()
    return [
        RemediationTarget(video_id=vid, platform=plat, platform_video_id=pid)
        for vid, plat, pid in rows
    ]


async def record_metrics(
    self, target: RemediationTarget, metrics: VideoMetrics, followers_at_capture: int | None
) -> None:
    self._s.add(
        VideoMetricsRow(
            video_id=target.video_id,
            views=metrics.views,
            likes=metrics.likes,
            comments=metrics.comments,
            shares=metrics.shares,
            saves=metrics.saves,
            followers_at_capture=followers_at_capture,
        )
    )


async def mark_removed(self, video_ids: Sequence[int]) -> int:
    if not video_ids:
        return 0
    stmt = update(VideoRow).where(VideoRow.id.in_(video_ids)).values(removed_at=datetime.now(UTC))
    result = await self._s.execute(stmt)
    return int(result.rowcount or 0)
```

> `record_metrics` só adiciona à sessão; o flush/commit é da UoW (mesmo padrão de `VideoMetricsRow` no `upsert_candidates`). Não há savepoint por vídeo aqui: a métrica é só números (sem FK nova) e o lote é da UoW do segmento. A remedição **só remede vídeos do YouTube** (o único `refreshable`): filtre `VideoRow.platform.in_(platforms)` no SQL para não gastar o `limit` com alvos do Instagram/TikTok/web — `remediation_targets` ganha o parâmetro `platforms: Sequence[str]` (já na assinatura acima).

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(db): alvos de remedição, gravação de métricas e mark_removed (R7)"
```

---

### Task 6: Use-case `remediate`

**Files:**
- Create: `src/content_factory/app/remediate.py`
- Test: `tests/app/test_remediate.py` (novo), `tests/stubs.py` (fakes completos e tipados)

**Interfaces:**
- Consumes: `RemediationRepository`, `RefreshableCollector`, `CollectRunRepository`, `RateLimiter`, `PlatformHealth`, `counting`, `report_discover`.
- Produces:
  - `@dataclass class RemediationDeps: videos: RemediationRepository; refreshables: dict[str, RefreshableCollector]; runs: CollectRunRepository; limiter: RateLimiter; health: PlatformHealth = field(default_factory=NoPlatformHealth); clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow); window_days: int = 7; limit: int = 500; min_gap_hours: int = 3`
  - `@dataclass class RemediationOutcome: targets: int = 0; refreshed: int = 0; metrics_written: int = 0; removed: int = 0; failed_platforms: int = 0; quota_used: int = 0` (só `int`: vai direto para o envelope)
  - `async def remediate(segment_id: int, deps: RemediationDeps) -> RemediationOutcome`

- [ ] **Step 1: Testes que falham**

```python
# tests/app/test_remediate.py
async def test_remediate_grava_metricas_e_marca_removidos() -> None:
    repo = FakeRemediationRepo(targets=["v1", "v2"])
    deps = remediation_deps(repo, refreshables={"youtube": FakeRefresher(found=["v2"])})
    out = await remediate(segment_id=1, deps=deps)
    assert (out.targets, out.metrics_written, out.removed) == (2, 1, 1)
    assert repo.removed == [1]  # video_id do v1
    assert [r.collector for r in deps.runs.saved] == ["remediate:youtube"]


async def test_remediate_sem_alvos_nao_chama_coletor_nem_grava_run() -> None:
    refresher = FakeRefresher(found=[])
    deps = remediation_deps(FakeRemediationRepo(targets=[]), refreshables={"youtube": refresher})
    out = await remediate(segment_id=1, deps=deps)
    assert out == RemediationOutcome()
    assert refresher.calls == 0 and deps.runs.saved == []


async def test_erro_de_contrato_vira_run_failed_e_nao_levanta() -> None:
    refresher = FakeRefresher(error=RateLimited("youtube", "quota"))
    deps = remediation_deps(
        FakeRemediationRepo(targets=["v1"]), refreshables={"youtube": refresher}
    )
    out = await remediate(segment_id=1, deps=deps)
    assert out.failed_platforms == 1 and out.removed == 0  # erro NÃO marca removido
    run = deps.runs.saved[0]
    assert run.status == "failed" and run.error_code == refresher.error.code
    assert deps.health.reports == [("youtube", refresher.error.code)]


async def test_cota_da_remedicao_vai_para_a_run() -> None:
    deps = remediation_deps(
        FakeRemediationRepo(targets=[f"v{i}" for i in range(120)]),
        refreshables={"youtube": FakeRefresher(found="all", quota_per_batch=2)},
    )
    out = await remediate(segment_id=1, deps=deps)
    assert out.quota_used == deps.runs.saved[0].quota_used == 3 * 2  # 3 lotes de 50
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar**

```python
"""R7 — remede os vídeos do segmento descobertos nos últimos N dias.

Sem isso não há curva de engajamento: hoje um vídeo só é remedido quando a
busca o encontra de novo (103 de 427 com 2+ medições). A remedição grava só
`video_metrics` — nunca metadados nem `discovery_source` (papel do
`upsert_candidates`). Mesmo contrato da coleta: limiter, cota por
`counting()`, breaker e uma `CollectRun` `remediate:<plataforma>`; erro de
contrato vira run `failed` e não derruba a coleta da mesma UoW.
"""


async def remediate(segment_id: int, deps: RemediationDeps) -> RemediationOutcome:
    platforms = sorted(deps.refreshables)
    targets = await deps.videos.remediation_targets(
        segment_id, deps.window_days, deps.limit, deps.min_gap_hours, platforms
    )
    out = RemediationOutcome(targets=len(targets))
    by_platform: dict[str, list[RemediationTarget]] = {}
    for t in targets:
        by_platform.setdefault(t.platform, []).append(t)

    missing_video_ids: list[int] = []
    for platform, group in sorted(by_platform.items()):
        refreshable = deps.refreshables[platform]
        by_pid = {t.platform_video_id: t for t in group}
        run = CollectRun(
            collector=f"remediate:{platform}", segment_id=segment_id, started_at=deps.clock()
        )
        with counting() as counters:
            try:
                await deps.limiter.acquire(platform)
                result = await refreshable.refresh(group)
            except CollectorError as exc:
                await report_discover(deps.health, platform, exc.code)
                run.status, run.error_code, run.failed = "failed", exc.code, 1
                out.failed_platforms += 1
                result = None
            else:
                await report_discover(deps.health, platform, None)
        run.quota_used = counters.quota_used
        out.quota_used += counters.quota_used
        if result is not None:
            for cand in result.candidates:
                target = by_pid.get(cand.platform_video_id)
                if target is None:
                    continue
                await deps.videos.record_metrics(target, _metrics_of(cand), cand.creator_followers)
                out.metrics_written += 1
            missing_video_ids += [by_pid[m].video_id for m in result.missing_ids if m in by_pid]
            out.refreshed += len(result.candidates)
            run.status, run.found, run.updated = "ok", len(group), len(result.candidates)
        run.finished_at = deps.clock()
        await deps.runs.record(run)
    out.removed = await deps.videos.mark_removed(missing_video_ids)
    return out
```

> O `limiter.acquire` cobre o pedido inteiro da plataforma (até 10 lotes de `videos.list` de 1 unidade cada + `channels.list`). Se o limitador do YouTube for por requisição, mova o `acquire` para dentro do `refresh` por lote. Decida com o `RateLimiter` real e teste.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/remediate.py tests/app/test_remediate.py tests/stubs.py
git commit -m "feat(app): use-case remediate — métricas por lote, removidos, cota e breaker (R7)"
```

---

### Task 7: Ligar a remedição na coleta (`run_collect_job`)

**Files:**
- Modify: `src/content_factory/app/segment_deps.py` (bundle ganha `remediate`)
- Modify: `src/content_factory/infra/queue/composition.py` (wire `RemediationDeps`)
- Modify: `src/content_factory/infra/queue/worker.py` (`_remediate_impl`, chamada no `_one`)
- Modify: `src/content_factory/app/results.py` (merge do sub-envelope `remediation`)
- Test: `tests/infra/queue/test_worker.py` (editar)

**Interfaces:**
- Consumes: `remediate`, `RemediationDeps`.
- Produces:
  - `SegmentBundle.remediate: RemediationDeps`
  - `_remediate_impl` (hook de teste, default `remediate`)
  - Envelope do collect ganha `"remediation": {"targets": n, "refreshed": n, "metrics_written": n, "removed": n}` por segmento.
  - Envs `REMEDIATION_WINDOW_DAYS` (7), `REMEDIATION_LIMIT` (500).

- [ ] **Step 1: Teste que falha**

```python
# tests/infra/queue/test_worker.py  (adicionar, no estilo do arquivo)
async def test_collect_job_remede_apos_coletar(monkeypatch, ...) -> None:
    chamadas: list[str] = []
    monkeypatch.setattr(w, "_remediate_impl", _fake_remediate(chamadas))
    out = await w.run_collect_job(ctx_fake)
    assert "remediation" in out["segments"]["velas-artesanais"]
    assert chamadas  # a remedição rodou
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL (sem `remediation` no envelope).

- [ ] **Step 3: Implementar**

Em `worker.py`:

```python
_remediate_impl: Callable[..., Awaitable[Any]] = remediate


def _remediation_deps_from_env(
    videos: RemediationRepository, refreshables: dict[str, RefreshableCollector]
) -> RemediationDeps:
    return RemediationDeps(
        videos=videos,
        refreshables=refreshables,
        window_days=int(os.getenv("REMEDIATION_WINDOW_DAYS", "7")),
        limit=int(os.getenv("REMEDIATION_LIMIT", "500")),
        min_gap_hours=int(os.getenv("REMEDIATION_MIN_GAP_HOURS", "3")),
    )
```

Em `run_collect_job._one`, após `collect(...)` e antes do commit:

```python
        async with app_deps.create_segment_deps(segment) as bundle:
            out = await _collect_impl(segment.slug, platforms, cfg["limit"], bundle.collect)
            remediation = await _remediate_impl(segment.id, bundle.remediate)
            await bundle.uow.commit()
        return {**collect_result(out), "remediation": dataclasses.asdict(remediation)}
```

Em `composition.py`: `SegmentBundle` monta `remediate=RemediationDeps(videos=VideoRepositoryImpl(session), refreshables={"youtube": youtube_collector}, runs=..., limiter=..., health=...)` — as mesmas instâncias do `CollectDeps` (limiter e breaker compartilhados: a remedição e a busca disputam a mesma cota do YouTube). O coletor do YouTube já existe no `AppDeps`; reaproveite a mesma instância.

**Tempo do job:** `COLLECT_JOB_TIMEOUT_S = 1500`. Com 4 buscas + até 10 lotes por segmento × 3 segmentos, medir no teste da Task 7 (fakes com latência) e, no QA, pelo `ops-report.ticks.wall_ms_max`. Se passar de 50% do timeout, levar ao orquestrador antes de subir o timeout (o catch-up soma os três timeouts).

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/infra/queue/ tests/app/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/segment_deps.py src/content_factory/infra/queue/composition.py \
        src/content_factory/infra/queue/worker.py src/content_factory/app/results.py tests/infra/queue/test_worker.py
git commit -m "feat(worker): remedição dentro da coleta (R7)"
```

---

### Task 8: Encerrar o bloco — gates e aceite

- [ ] **Step 1: Gates** — `make gates` verde.

- [ ] **Step 2: Aceite R7 (após 2 coletas ≈ 12 h)**

```sql
-- nenhum par de medições do mesmo vídeo a menos de 1 h (o bug da mesma transação)
SELECT count(*) FROM (
  SELECT video_id, captured_at - lag(captured_at) OVER (PARTITION BY video_id ORDER BY captured_at) AS gap
  FROM video_metrics) g WHERE gap < interval '1 hour';  -- esperado: 0
-- cota e erros da remedição
SELECT status, count(*), sum(quota_used) FROM collect_runs
WHERE collector LIKE 'remediate:%' AND started_at > now() - interval '1 day' GROUP BY 1;
-- ≥ 90% dos vídeos YouTube acompanhados com medição nova na última janela
SELECT count(DISTINCT vm.video_id) AS remedidos
FROM video_metrics vm
WHERE vm.captured_at > now() - interval '6 hours';
-- e: após 12 h, velocity não nula para ≥ 90% deles
SELECT count(*) FILTER (WHERE vs.velocity IS NOT NULL)::float / count(*) AS frac_velocity
FROM (SELECT DISTINCT ON (video_id) * FROM video_scores ORDER BY video_id, computed_at DESC) vs
WHERE vs.computed_at > now() - interval '12 hours';
-- esperado: frac_velocity >= 0.90
```

- [ ] **Step 3: Aceite R12** — 4 runs `collect` e 4 `score` no primeiro dia (QA pelos job runs e pelo `ops-report` ticks); duração de 1 coleta bem abaixo de 6 h (`ops-report.ticks.wall_ms_max`).

- [ ] **Step 4: Confirmar que removidos param de ser remedidos**

```sql
SELECT count(*) FROM videos WHERE removed_at IS NOT NULL;  -- esperado: > 0 se houver removido
SELECT count(*) FROM video_metrics vm JOIN videos v ON v.id = vm.video_id
WHERE v.removed_at IS NOT NULL AND vm.captured_at > v.removed_at;  -- esperado: 0
```

- [ ] **Step 5: Commit de encerramento** — `git commit -am "docs(plans): bloco 3 concluído — R7/R12 verificados" || true`

---

### Task 9: Idioma na remediação (R14/17-10) — *acrescentada após o merge da `main`*

Decisão do usuário (17/10, revisão do PR #52): o público é o Brasil (mesma
decisão que restringiu a coleta a `pt-BR` na `main`, `f6d2353`) e a remediação
**herda a regra** — não remedir vídeo cujo idioma declarado não casa. Sem isso,
os 12 vídeos `pt-PT` da coleta real de 16/10 (27 → 15 com o filtro da busca)
seguiriam sendo remedidos por até 7 dias, gastando cota para alimentar uma
métrica que a relevância do Bloco 5 descarta.

- [x] **Step 1: Regra única no domínio** — `domain/language.py` com
  `language_matches` (movida do `youtube.py`, que passa a importá-la como
  `_language_matches` — o nome segue importável para os Blocos 5/6) e
  `wanted_language(search)` = `accepted_language or relevance_language`.
  Testes: `tests/domain/test_language.py`.
- [x] **Step 2: Alvo carrega o idioma** — `RemediationTarget.language` (`videos.language`)
  no `SELECT` do `remediation_targets`. Teste de integração em
  `tests/infra/test_repositories.py`.
- [x] **Step 3: Filtro no use-case** — `RemediationDeps.accepted_language`
  (montado por segmento na composition); `remediate` descarta o alvo que não
  casa **antes** do coletor (sem cota, sem `found`, sem métrica) e conta em
  `RemediationOutcome.skipped_language`. Testes em `tests/app/test_remediate.py`
  (`pt-PT` sai, `pt`/sem idioma ficam, sem exigência nada é descartado).
- [x] **Step 4: Envelope** — `skipped_language` no sub-envelope
  (`results.remediation_result`) + `contrato-envelopes.md`; `targets` passa a
  ser o que a remediação TENTOU remedir (`targets + skipped_language` = alvos
  do repositório). Wiring coberto por
  `tests/infra/queue/test_worker.py::test_create_segment_deps_builds_per_segment_bundle`.

---

## Auto-revisão da spec (R7/R12)

- **R7** ✓ alvos últimos 7 dias com teto 500, só YouTube e sem medição nas últimas 3 h (Task 5); contrato de erro/cota/breaker igual ao da coleta (Task 6); `videos.list` em lotes de 50 (Task 4), `video_metrics` por remedição (Tasks 5-6), removido/privado marcado (`removed_at`) e fora dos alvos (Tasks 2/5). Instagram segue mídia própria e TikTok fora (porta separada, `refreshables` só com YouTube).
- **R12** ✓ collect `02,08,14,20` e score configuráveis por env (Task 1); research 07:00 inalterado; catch-up e cron leem as mesmas envs (Task 1); remedição dentro da coleta, bem abaixo de 6 h.
- Fora deste bloco: R5 (plano de consulta) e R3 (Trends) no Bloco 4; a remedição não entra no `ops-report` detalhado até R13 (Bloco 6).

---

## Resultado (execução do bloco)

Tasks 1–9 implementadas com TDD (teste falhando → implementação → verde) e
commit por task. Migração `0013` validada do zero: `upgrade head`, `downgrade
0012` e `upgrade head` de novo numa base vazia, `alembic check` sem drift.
`make gates` verde (test/lint/typecheck/security/docs/privacy) e
`pytest -m integration` com Postgres+Redis descartáveis: 63 passed.

### Desvios do plano (e por quê)

1. **Envs da remedição**: o helper `_remediation_deps_from_env` do plano
   devolvia `RemediationDeps` sem `runs`/`limiter`/`health` (campos
   obrigatórios da Task 6) e `composition.py` não lê env. As envs
   `REMEDIATION_*` são lidas no `create_app_deps` (com `_int_env`, que tolera
   valor inválido como `_max_concurrency_from_env`) e passadas a
   `compose_app_deps` → `SharedDeps.remediation_*`.
2. **Sub-envelope**: além de `targets/refreshed/metrics_written/removed`, o
   envelope leva `failed_platforms` e `quota_used` — são contagens do
   `RemediationOutcome` (Task 6) e são o sinal operacional do contrato de
   erro/cota.
3. **Lock ocupado**: a remedição **não** roda quando o collect devolveu
   `ja_em_execucao`. O plano chamava sempre; com o outro collect da mesma UoW
   ainda não commitado, a remedição gravaria `video_metrics` duplicado do
   mesmo vídeo (o gap de 3 h só compara contra o que já está commitado).
4. **Paridade CLI × worker** (`tests/cli/test_cli.py`): a CLI (`cf collect`)
   não remedeia — o escopo do plano é o `run_collect_job`. O teste de paridade
   passa a comparar o envelope do collect e verifica o sub-envelope
   `remediation` à parte (presente com zeros, ausente no `ja_em_execucao`).
5. **`tests/app/test_remediate.py`**: os fakes observáveis (`runs`, `health`)
   são passados explicitamente pelo helper, porque os campos de
   `RemediationDeps` são os Protocolos (pyright strict não deixa acessar
   `.saved` num `CollectRunRepository`). O breaker é verificado por
   `FakeHealth.failures`: `PlatformHealth.record_failure(platform)` não recebe
   o código do erro, então `reports == [(platform, code)]` não é observável no
   fake sem inventar uma porta nova.
6. **Integração do worker** (`tests/infra/queue/test_worker_integration.py`): o
   `_remediate_impl` é neutralizado nos 2 testes de UoW/erro. Com o bundle
   real, a remedição tocaria o YouTube de verdade (chave vazia → `AuthExpired`)
   e somaria 1 `CollectRun` `remediate:youtube` por segmento às contagens de
   `collect_runs` que esses testes assertam.
7. **Docs**: `SCORE_CRON_HOURS` (Task 1) e `REMEDIATION_*` entram em
   `operacao.md` + `app.env.example` + `defaults.env`; o sub-envelope em
   `contrato-envelopes.md`; os diagramas do ciclo ganham o cron 4×/dia e a
   re-medição.
8. **Task 9 (idioma) muda o contrato do sub-envelope**: a revisão de 17/10
   decidiu que a remediação herda a regra de idioma da coleta, então o
   sub-envelope ganha `skipped_language` e `targets` passa a ser "alvos
   aceitos" (`targets + skipped_language` = o que o repositório devolveu) — o
   plano (Task 7/8) previa `targets` como os alvos crus. TDD do Task 9 cobre os
   dois valores; o aceite do Task 8 (`≥ 90% dos vídeos com medição nova`) passa
   a usar o denominador correto, sem pt-PT.

### Verificação local com dados reais (pré-deploy)

E2E com Postgres real (`alembic upgrade head`) + `YoutubeCollector` com
transporte falso, 4 vídeos: 1 medido agora (fora), 1 medido há 5 h (dentro), 1
removido (fora), 1 nunca medido (dentro):

- `RemediationOutcome(targets=2, refreshed=2, metrics_written=2, removed=0,
  failed_platforms=0, quota_used=2)`;
- `collect_runs`: 1 run `remediate:youtube` `ok` com `quota_used = 2`;
- `video_metrics`: 2 linhas novas com as métricas do YouTube e
  `followers_at_capture = 777`;
- SQL de aceite do Task 8: 0 pares de medição com gap < 1 h e 0 medições após
  `removed_at`;
- 2ª remedição imediata: `targets=0` (gap de 3 h), sem run nova e sem cota.

**Regra de idioma (Task 9)**, mesmo E2E, 2 segmentos no banco descartável
(`cfidioma`), transporte falso devolvendo todos os alvos:

- segmento com `search.accepted_language: pt-BR` e 4 vídeos (`pt-BR`, `pt-PT`,
  sem idioma, `en`): `targets=2, skipped_language=2, refreshed=2,
  metrics_written=2, quota_used=2`; `remediate:youtube` `ok` com `found=2`;
- no banco, só `a-br` e `a-none` ganharam medição — **nenhuma** linha para
  `a-pt` (`pt-PT`) nem `a-en` (`en`), que nem entraram no `videos.list`;
- segmento sem `SearchConfig` com `b-pt` (`pt-PT`): `targets=1,
  skipped_language=0` e medição gravada — o fallback "sem exigência, mantém"
  segue igual ao Bloco 3.

### Pendente (pós-deploy, com dados de produção)

SQL do Task 8 após 2 coletas (≈ 12 h): gap < 1 h = 0, `remediate:*` com cota e
status, ≥ 90% dos vídeos YouTube com medição nova e `velocity` não nula; R12
com 4 runs `collect`/`score` no dia e `ticks.wall_ms_max` bem abaixo de 6 h;
`removed_at > 0` e nenhuma medição posterior.

Idioma (Task 9) depois do deploy — confirma com os dados reais que o histórico
em `pt-PT` parou de ser remedido:

```sql
-- descartes por idioma na última janela (esperado > 0 enquanto houver pt-PT recente)
SELECT count(*) FROM video_metrics;  -- baseline antes
-- nenhum alvo pt-PT ganhou medição DEPOIS do deploy (language é o declarado do vídeo)
SELECT count(*) FROM video_metrics vm JOIN videos v ON v.id = vm.video_id
WHERE v.language = 'pt-PT' AND vm.captured_at > now() - interval '1 day';
-- e o sub-envelope do collect no dia deve trazer skipped_language > 0
```
