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
- Envs novas: `SCORE_CRON_HOURS` (= collect), `REMEDIATION_WINDOW_DAYS=7`, `REMEDIATION_LIMIT=500`.

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
    - `async def remediation_targets(self, segment_id: int, since_days: int, limit: int) -> list[RemediationTarget]`
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
async def test_remediation_targets_exclui_removidos() -> None:
    # cria segmento + 2 vídeos youtube (v1 removido, v2 ativo) com metrics
    targets = await repo.remediation_targets(seg_id, since_days=7, limit=500)
    assert [t.platform_video_id for t in targets] == ["v2"]  # v1 tem removed_at
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
async def remediation_targets(
    self, segment_id: int, since_days: int, limit: int
) -> list[RemediationTarget]:
    since = datetime.now(UTC) - timedelta(days=since_days)
    stmt = (
        select(VideoRow.id, VideoRow.platform, VideoRow.platform_video_id)
        .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoRow.id)
        .where(
            VideoSegmentRow.segment_id == segment_id,
            VideoSegmentRow.discovered_at >= since,
            VideoRow.removed_at.is_(None),
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

> `record_metrics` só adiciona à sessão; o flush/commit é da UoW (mesmo padrão de `VideoMetricsRow` no `upsert_candidates`). Não há savepoint por vídeo aqui: a métrica é só números (sem FK nova) e o lote é da UoW do segmento.

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
- Test: `tests/app/test_remediate.py` (novo)

**Interfaces:**
- Consumes: `RemediationRepository`, `RefreshableCollector`, portas de lock/clock.
- Produces:
  - `@dataclass class RemediationDeps: videos: RemediationRepository; refreshables: dict[str, RefreshableCollector]; clock: Callable[[], datetime] = ...; window_days: int = 7; limit: int = 500`
  - `@dataclass class RemediationOutcome: targets: int; refreshed: int; metrics_written: int; removed: int; platforms: dict[str, int]`
  - `async def remediate(segment_id: int, deps: RemediationDeps) -> RemediationOutcome`

- [ ] **Step 1: Teste que falha**

```python
# tests/app/test_remediate.py
from content_factory.app.remediate import RemediationDeps, remediate


class _FakeRepo:
    def __init__(self, targets, missing=()): ...
    async def remediation_targets(self, segment_id, since_days, limit): ...
    async def record_metrics(self, target, metrics, followers): ...
    async def mark_removed(self, video_ids): ...


class _FakeYouTube:
    platform = "youtube"

    async def refresh(self, targets):
        found = [c for c in self._found if c in {t.platform_video_id for t in targets}]
        missing = [t.platform_video_id for t in targets if t.platform_video_id not in found]


async def test_remediate_grava_metricas_e_marca_removidos() -> None:
    deps = RemediationDeps(
        videos=_FakeRepo(targets=["v1", "v2"]), refreshables={"youtube": _FakeYouTube(found=["v2"])}
    )
    out = await remediate(segment_id=1, deps=deps)
    assert out.targets == 2
    assert out.metrics_written == 1
    assert out.removed == 1


async def test_remediate_sem_alvos_nao_chama_coletor() -> None:
    out = await remediate(
        segment_id=1, deps=RemediationDeps(videos=_FakeRepo(targets=[]), refreshables={})
    )
    assert out == RemediationOutcome(
        targets=0, refreshed=0, metrics_written=0, removed=0, platforms={}
    )
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implementar**

```python
"""R7 — remede os vídeos do segmento descobertos nos últimos N dias.

Sem isso não há curva de engajamento: hoje um vídeo só é remedido quando a
busca o encontra de novo (103 de 427 com 2+ medições). Aqui a remedição é
por LOTE, sobre `video_metrics` apenas — nunca sobrescreve metadados nem
`discovery_source` (isso é papel do `upsert_candidates` da coleta).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from content_factory.app.ports import RefreshableCollector, RemediationRepository
from content_factory.domain.models import RemediationTarget, VideoMetrics


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class RemediationDeps:
    videos: RemediationRepository
    refreshables: dict[str, RefreshableCollector]
    clock: Callable[[], datetime] = field(default_factory=lambda: _utcnow)
    window_days: int = 7
    limit: int = 500


@dataclass
class RemediationOutcome:
    targets: int = 0
    refreshed: int = 0
    metrics_written: int = 0
    removed: int = 0
    platforms: dict[str, int] = field(default_factory=dict[str, int])


async def remediate(segment_id: int, deps: RemediationDeps) -> RemediationOutcome:
    targets = await deps.videos.remediation_targets(segment_id, deps.window_days, deps.limit)
    out = RemediationOutcome(targets=len(targets))
    if not targets:
        return out
    by_id = {t.video_id: t for t in targets}
    by_platform: dict[str, list[RemediationTarget]] = {}
    for t in targets:
        by_platform.setdefault(t.platform, []).append(t)

    missing_video_ids: list[int] = []
    for platform, group in by_platform.items():
        refreshable = deps.refreshables.get(platform)
        if refreshable is None:
            continue  # plataforma sem remedição (Instagram/TikTok): mantém como está
        result = await refreshable.refresh(group)
        for cand in result.candidates:
            target = next((t for t in group if t.platform_video_id == cand.platform_video_id), None)
            if target is None:
                continue
            await deps.videos.record_metrics(
                target,
                VideoMetrics(
                    views=cand.views,
                    likes=cand.likes,
                    comments=cand.comments,
                    shares=cand.shares,
                    saves=cand.saves,
                ),
                cand.creator_followers,
            )
            out.metrics_written += 1
        for missing in result.missing_ids:
            target = next((t for t in group if t.platform_video_id == missing), None)
            if target is not None:
                missing_video_ids.append(target.video_id)
        out.platforms[platform] = len(result.candidates)
    out.refreshed = sum(out.platforms.values())
    out.removed = await deps.videos.mark_removed(missing_video_ids)
    _ = by_id
    return out
```

> `by_id` fica como gancho explícito de leitura (mapa `video_id→target`) — se o lint reclamar de não usado, remova-o; não há comportamento dependente dele.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/remediate.py tests/app/test_remediate.py
git commit -m "feat(app): use-case remediate — métricas por lote e marcação de removidos (R7)"
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

Em `composition.py`: `SegmentBundle` monta `remediate=RemediationDeps(videos=VideoRepositoryImpl(session), refreshables={"youtube": youtube_collector}, ...)`. O coletor do YouTube já existe no `AppDeps`; reaproveite a mesma instância.

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

## Auto-revisão da spec (R7/R12)

- **R7** ✓ alvos últimos 7 dias com teto 500 (Task 5), `videos.list` em lotes de 50 (Task 4), `video_metrics` por remedição (Tasks 5-6), removido/privado marcado (`removed_at`) e fora dos alvos (Tasks 2/5). Instagram segue mídia própria e TikTok fora (porta separada, `refreshables` só com YouTube).
- **R12** ✓ collect `02,08,14,20` e score configuráveis por env (Task 1); research 07:00 inalterado; catch-up e cron leem as mesmas envs (Task 1); remedição dentro da coleta, bem abaixo de 6 h.
- Fora deste bloco: R5 (plano de consulta) e R3 (Trends) no Bloco 4; a remedição não entra no `ops-report` detalhado até R13 (Bloco 6).
