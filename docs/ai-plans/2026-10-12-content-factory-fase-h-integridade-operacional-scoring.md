# Fase H' — Integridade operacional + auditabilidade do score

> **Para quem for executar:** TDD sempre (RED confirmado antes do GREEN),
> plano bite-sized por task, commit por task. Testes de infra usam
> `@requires_infra` contra `TEST_DATABASE_URL` real (ver padrão em
> `tests/infra/test_repositories.py`) — não mockar Postgres.

**Origem:** revisão técnica externa ao repositório (2026-10-12), validada
linha a linha contra o código atual antes deste plano. Ver HANDOFF.md
para o contexto da Fase F/G que esta fase continua.

**Goal:** fechar os 7 itens do checklist de reavaliação (P0/P1/P2): (1) o
pipeline de pesquisa social reporta sucesso mesmo quando toda a coleta
social falhou; (2) a política de snapshots não distinguia histórico
comparável de ausência de dado; (3) `velocity` media idade do vídeo, não
crescimento, e inventava um valor quando a data de publicação era
desconhecida; (4) a cobertura de métricas calculada (`metrics_available`)
não era persistida junto ao score; (5) o histórico do Google Trends podia
acumular o mesmo período duplicado; (6) o upsert de vídeo não atualizava
metadados em conflito e a auditoria de `CollectRun` misturava "candidato
ignorado" com "plataforma falhou" no mesmo campo; (7) falta uma
metodologia (não necessariamente código) para validar o ranking contra
dados reais.

**Fora de escopo de verdade (decisão deliberada, não checklist):**
recalibração dos pesos de `WEIGHTS` com dados reais — o roadmap já
proíbe calibrar no escuro, e o item 7 (Task 8) entrega a metodologia
para fazer isso depois, com dados, não a recalibração em si. O cap de
1000 views/hora usado no componente de momentum (Task 3) é um valor MVP
explícito — fica documentado como "a calibrar", não "calibrado".

**Revisão de posição:** na primeira leitura eu tinha descartado
`run.failed = stats.skipped` como "não é bug" porque o mesmo padrão já
existe, documentado, em `app/collect.py:69`. Isso está certo quanto a
ser *deliberado e consistente* — mas o usuário pediu explicitamente
para resolver a ambiguidade de auditoria, e a objeção de fundo da
revisão continua válida: uma linha de `collect_runs` com `status="ok"`
e `failed=2` lê como "2 falhas" para quem consome a tabela, mesmo sendo
"2 candidatos ignorados pelo savepoint". Task 7 resolve isso com um
campo novo (`skipped`), sem quebrar o que já existe.

## Pré-requisito

Fases F e G mergeadas em `main` (confirmado: `video_metrics` grava
snapshot, `VideoMetrics`/`ScoreResult` já distinguem `None` de zero).

## Task 1 — `research_pipeline`: ok/partial real, não sempre `ok=True`

**Problema confirmado:** `run_research_cycle` (`src/content_factory/app/research_pipeline.py:124`)
sempre devolve `ResearchOutcome(ok=True, ...)`, mesmo que TODAS as
plataformas sociais tenham falhado — o `try/except` por plataforma marca
o `CollectRun` como `failed` mas não propaga isso para o retorno da
função. `collect()` (`app/collect.py:92-96`) já resolve exatamente esse
problema (`ok = errors == 0; partial = not ok and inserted+updated>0`) —
este fix é portar o mesmo contrato, não inventar um novo.

**Files:**
- Modify: `src/content_factory/app/research_pipeline.py`
- Test: `tests/app/test_research_pipeline.py`

### Passo 1.1 — `ResearchOutcome` ganha `partial`

```python
@dataclass
class ResearchOutcome:
    ok: bool
    partial: bool = False
    counts: dict[str, int | bool] = field(default_factory=dict[str, int | bool])
```

### Passo 1.2 — contar erros de plataforma social separado de skip de candidato

No loop de `platform, discoverer in deps.socials.items()`, hoje o
`except Exception` só incrementa `counts[f"{platform}_failed"]`. Adicionar
um contador agregado `social_platform_errors` e um contador de
plataformas efetivamente tentadas (`social_platforms_attempted`, só as
que estão em `segment.platforms` — já é o que o `continue` filtra):

```python
social_found = social_inserted = social_updated = social_skipped = 0
social_platforms_attempted = 0
social_platform_errors = 0
for platform, discoverer in deps.socials.items():
    if platform not in segment.platforms:
        continue
    social_platforms_attempted += 1
    started = datetime.now(UTC)
    run = CollectRun(collector=f"research:{platform}", segment_id=segment.id, started_at=started)
    try:
        await deps.limiter.acquire(platform)
        candidates = await discoverer.discover_by_keywords(keywords_for_social, [], 1)
        stats = await deps.videos.upsert_candidates(segment.id, platform, candidates)
        run.status = "ok"
        run.found = len(candidates)
        run.inserted, run.updated = stats.inserted, stats.updated
        run.failed = stats.skipped
        social_found += len(candidates)
        social_inserted += stats.inserted
        social_updated += stats.updated
        social_skipped += stats.skipped
    except Exception as exc:
        run.status = "failed"
        run.error_code = getattr(exc, "code", "internal_error")
        social_platform_errors += 1
        counts[f"{platform}_failed"] = int(counts.get(f"{platform}_failed", 0)) + 1
    finally:
        run.finished_at = datetime.now(UTC)
        await deps.runs.record(run)
counts["social_found"] = social_found
counts["social_inserted"] = social_inserted
counts["social_updated"] = social_updated
counts["social_skipped"] = social_skipped
```

### Passo 1.3 — calcular `ok`/`partial` no retorno final

```python
ok = social_platform_errors == 0
partial = (not ok) and (social_inserted + social_updated > 0)
return ResearchOutcome(ok=ok, partial=partial, counts=counts)
```

Semântica: nenhuma plataforma social configurada para o segmento →
`social_platform_errors == 0` trivialmente → `ok=True` (não é falha,
não havia coleta social esperada). Todas as plataformas configuradas
falharam → `ok=False`, `partial=False` (falha total). Algumas falharam
mas outras inseriram/atualizaram candidato → `ok=False`, `partial=True`.
Falha de `web.expand`/`trends.interest_over_time` (etapas obrigatórias)
já propaga como exceção não capturada — comportamento existente, não
muda.

### Passo 1.4 — atualizar teste existente que ficou errado pela correção

`test_run_research_cycle_social_failure_records_failed_run`
(`tests/app/test_research_pipeline.py:~233`) tem **1 única plataforma
social configurada, e ela falha** — esse é exatamente o caso de falha
total. Hoje o teste afirma `out.ok is True` com o comentário "ciclo não
aborta" — confundindo "não lançou exceção" com "teve sucesso". Corrigir
a asserção:

```python
out = await run_research_cycle("candles", DepthConfig(), deps)
assert out.ok is False  # falha TOTAL da única plataforma social configurada
assert out.partial is False  # nada foi inserido/atualizado
assert out.counts.get("instagram_failed") == 1
assert isinstance(repos.runs, FakeRuns)
assert repos.runs.recorded[0].status == "failed"
assert repos.runs.recorded[0].error_code == "blocked"
```

### Passo 1.5 — novos testes

```python
@pytest.mark.asyncio
async def test_run_research_cycle_partial_when_one_of_two_social_fails() -> None:
    """1 plataforma falha, outra insere candidato → ok=False, partial=True."""

    class _BoomSocial:
        platform: ClassVar[str] = "tiktok"

        async def discover_by_keywords(
            self, keywords: list[str], hashtags: list[str], depth: int
        ) -> list[CandidateVideo]:
            from content_factory.domain.errors import CollectorError

            raise CollectorError("blocked", "tiktok", "rate limit")

    cands = [
        CandidateVideo(
            platform="instagram",
            platform_video_id="ig1",
            url="https://instagram.com/reel/ig1",
            creator_handle="u",
        )
    ]

    class _SegmentsWithTwoPlatforms:
        async def get_by_slug(self, slug: str) -> Segment:
            return Segment(
                id=1, slug=slug, name=slug, platforms=["instagram", "tiktok"], keywords=["vela"]
            )

        async def upsert(self, cfg: SegmentConfig) -> Segment:
            raise NotImplementedError

    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={"instagram": _FakeSocial(cands), "tiktok": _BoomSocial()},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_SegmentsWithTwoPlatforms(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is False
    assert out.partial is True
    assert out.counts["social_inserted"] == 1
    assert out.counts.get("tiktok_failed") == 1


@pytest.mark.asyncio
async def test_run_research_cycle_ok_when_no_social_platform_configured() -> None:
    """Segmento sem rede social configurada: ausência de coleta social não é falha."""
    repos = FakeRepos()
    deps = ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={},
        signals=_FakeSignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_FakeSegmentsWithKeywords(),  # platforms=["instagram"], mas deps.socials={}
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    assert out.partial is False
```

- [x] RED (os 2 testes novos falham; `test_run_research_cycle_social_failure_records_failed_run` falha na asserção corrigida contra o código atual)
- [x] GREEN
- [x] `pytest tests/app/test_research_pipeline.py -v`
- [x] Commit: `fix(research): run_research_cycle reporta ok/partial real, nao sempre ok=True`

## Task 2 — schema: `video_scores` ganha `metrics_available`, `formula_version`, `velocity` nullable

**Problema confirmado:** `ScoreResult.metrics_available` existe desde a
Fase G mas nunca é persistido (`VideoScoreRow` não tem a coluna,
`ScoreRepositoryImpl.upsert` não a grava). E a Task 3 deste plano torna
`velocity` opcional (`float | None`) — a coluna no banco ainda é
`NOT NULL`, mesmo erro que já aconteceu com `performance`/`engagement`
na Fase G (migração `0005`).

**Files:**
- Create: `alembic/versions/0006_video_scores_metrics_available_formula_version.py`
- Modify: `src/content_factory/infra/db/orm.py` (`VideoScoreRow`)
- Modify: `src/content_factory/infra/db/repositories.py` (`ScoreRepositoryImpl.upsert`)
- Modify: `src/content_factory/domain/scoring.py` (constante `FORMULA_VERSION`)
- Test: `tests/infra/test_repositories.py`

### Passo 2.1 — migração

```python
"""video_scores: metrics_available + formula_version + velocity nullable

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("video_scores", "velocity", existing_type=sa.Float(), nullable=True)
    op.add_column(
        "video_scores",
        sa.Column(
            "metrics_available",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "video_scores",
        sa.Column("formula_version", sa.String(length=16), nullable=False, server_default="fase-h"),
    )


def downgrade() -> None:
    op.drop_column("video_scores", "formula_version")
    op.drop_column("video_scores", "metrics_available")
    op.alter_column("video_scores", "velocity", existing_type=sa.Float(), nullable=False)
```

### Passo 2.2 — ORM

Em `src/content_factory/infra/db/orm.py`, no `VideoScoreRow`:

```python
velocity: Mapped[float | None] = mapped_column(Float)  # era Mapped[float] NOT NULL
...
metrics_available: Mapped[list[str]] = mapped_column(JSONB, default=list)
formula_version: Mapped[str] = mapped_column(String(16), default="fase-h")
```

(`JSONB` e `String` já importados no topo do arquivo — confirmar antes
de adicionar import duplicado.)

### Passo 2.3 — constante de versão em `domain/scoring.py`

```python
FORMULA_VERSION = "fase-h"  # bump quando WEIGHTS ou a fórmula de um componente mudar
```

### Passo 2.4 — `ScoreRepositoryImpl.upsert` grava os campos novos

Em `src/content_factory/infra/db/repositories.py`, dentro do
`VideoScoreRow(...)` construído em `ScoreRepositoryImpl.upsert`:

```python
from content_factory.domain.scoring import FORMULA_VERSION

...
VideoScoreRow(
    video_id=video_id,
    segment_id=segment_id,
    computed_at=computed_at,
    viral_score=result.total,
    performance=result.performance,
    velocity=result.velocity,
    engagement=result.engagement,
    relevance=result.relevance,
    recency=result.recency,
    trend_decay=result.trend_decay,
    metrics_available=result.metrics_available,
    formula_version=FORMULA_VERSION,
)
```

### Passo 2.5 — teste de integração

```python
@requires_infra
async def test_score_upsert_persists_metrics_available_and_formula_version() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-coverage-{uuid4().hex[:8]}",
                name="Teste coverage",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        c = candidate(6)
        await VideoRepositoryImpl(s).upsert_candidates(seg.id, "youtube", [c])
        vid = await video_id_of(s, c)
        result = ScoreResult(
            total=42.5,
            performance=10.0,
            velocity=80.0,  # valor concreto — Task 3 é quem introduz velocity=None
            engagement=20.0,
            relevance=100.0,
            recency=100.0,
            trend_decay="low",
            metrics_available=["views", "likes"],
        )
        await ScoreRepositoryImpl(s).upsert(vid, seg.id, result, datetime.now(UTC))
        await s.commit()
        row = (
            await s.execute(select(VideoScoreRow).where(VideoScoreRow.video_id == vid))
        ).scalar_one()
        assert row.metrics_available == ["views", "likes"]
        assert row.formula_version == "fase-h"
        assert row.velocity == 80.0
    await engine.dispose()
```

- [x] `alembic upgrade head` local contra `TEST_DATABASE_URL`
- [x] `alembic check` → sem novas operações
- [x] RED → GREEN no teste novo
- [x] Commit: `feat(scoring): persiste metrics_available e formula_version em video_scores`

## Task 3 — `velocity` deixa de inventar idade; passa a medir crescimento real

**Problema confirmado:** `viral_score` (`src/content_factory/domain/scoring.py:61-65`)
calcula `age_days = (now - published_at).days if published_at else 999` e
usa isso para `velocity` por faixa — não mede crescimento, mede idade
(que já é o papel de `recency`). Quando `published_at is None`, inventa
`age_days=999` → `velocity=20`, um valor que não representa nada real.
Essa task resolve de uma vez dois achados da revisão: "`published_at`
ausente ainda afeta o score" (não inventa mais idade) e "idade continua
influenciando dois componentes" (depois desta task, só `recency` deriva
de idade — `velocity` passa a depender exclusivamente de crescimento
real entre capturas, não mais dos mesmos 999 dias/bucket que alimentavam
os dois componentes e inflavam a recência para 40% do peso combinado).

**Decisão de design:** `velocity` passa a ser calculado pela diferença
de `views` entre os dois snapshots mais recentes de `video_metrics` do
mesmo vídeo, dividida pelo intervalo real entre capturas (views/hora).
Sem dois snapshots comparáveis (vídeo novo, só uma captura, ou captura
não reportou `views`), o componente é `None` — ausente, excluído da
média ponderada (mesma regra que já existe para `performance`/
`engagement` desde a Fase G). `recency` continua baseada em idade
(papel que já tinha e que o TRD já documenta) — não muda.

O cap de conversão (`VELOCITY_VIEWS_PER_HOUR_CAP = 1000.0`) é um valor
MVP para ter uma escala 0-100 sem calibração real ainda — documentado
explicitamente no código como heurística a calibrar, igual ao
`performance` (`views / 1_000_000 * 100`) já é.

**Files:**
- Modify: `src/content_factory/domain/models.py` (`VideoSnapshot`, `ScoreResult.velocity`)
- Modify: `src/content_factory/domain/scoring.py` (`viral_score`, nova função `momentum_score`)
- Test: `tests/domain/test_scoring.py`

### Passo 3.1 — `VideoSnapshot` ganha os campos de histórico

Em `src/content_factory/domain/models.py`:

```python
class VideoSnapshot(BaseModel):
    """Vídeo + sua métrica mais recente — entrada do use-case de score."""

    video_id: int
    title: str = ""
    description: str = ""
    published_at: datetime | None = None
    metrics: VideoMetrics
    captured_at: datetime | None = None  # quando `metrics` foi capturada
    previous_metrics: VideoMetrics | None = None  # captura anterior do mesmo vídeo, se existir
    previous_captured_at: datetime | None = None
```

E `ScoreResult.velocity` muda de `float` para `float | None`:

```python
class ScoreResult(BaseModel):
    total: float
    performance: float | None = None
    velocity: float | None = None  # era float — Fase H: None = sem snapshot comparável
    engagement: float | None = None
    relevance: float
    recency: float
    trend_decay: str
    metrics_available: list[str] = Field(default_factory=list[str])
```

### Passo 3.2 — `momentum_score` pura em `domain/scoring.py`

```python
VELOCITY_VIEWS_PER_HOUR_CAP = 1000.0  # MVP — calibrar com dados reais (roadmap Fase G/H)


def momentum_score(
    metrics: VideoMetrics,
    previous_metrics: VideoMetrics | None,
    captured_at: datetime | None,
    previous_captured_at: datetime | None,
) -> float | None:
    """Crescimento de `views` entre as duas capturas mais recentes, em views/hora.

    `None` quando não há par de capturas comparável: vídeo só tem uma
    captura, alguma delas não reportou `views`, ou o intervalo entre
    capturas não é positivo (relógio incoerente/mesma captura).
    """
    if previous_metrics is None or captured_at is None or previous_captured_at is None:
        return None
    if metrics.views is None or previous_metrics.views is None:
        return None
    delta_hours = (captured_at - previous_captured_at).total_seconds() / 3600
    if delta_hours <= 0:
        return None
    delta_views = metrics.views - previous_metrics.views
    if delta_views <= 0:
        return 0.0
    return min(delta_views / delta_hours / VELOCITY_VIEWS_PER_HOUR_CAP * 100, 100.0)
```

### Passo 3.3 — `viral_score` usa `momentum_score`, não mais idade

Em `viral_score`, remover o bloco de `age_days`/`decay`-based velocity e
trocar por:

```python
def viral_score(
    m: VideoMetrics,
    relevance: float,
    now: datetime,
    published_at: datetime | None,
    captured_at: datetime | None = None,
    previous_metrics: VideoMetrics | None = None,
    previous_captured_at: datetime | None = None,
) -> ScoreResult:
    components: dict[str, float | None] = {}

    if m.views is not None:
        components["performance"] = min(m.views / 1_000_000 * 100, 100.0)
    else:
        components["performance"] = None

    eng = engagement_rate(m)
    components["engagement"] = min(eng * 10, 100.0) if eng is not None else None

    components["velocity"] = momentum_score(m, previous_metrics, captured_at, previous_captured_at)

    decay = trend_decay(published_at, now)
    components["recency"] = {"none": 50.0, "low": 100.0, "medium": 50.0, "high": 15.0}[decay]
    components["relevance"] = min(max(relevance, 0.0), 1.0) * 100

    available = {k: v for k, v in components.items() if v is not None}
    if available:
        weight_sum = sum(WEIGHTS[k] for k in available)
        total = round(sum(available[k] * WEIGHTS[k] for k in available) / weight_sum, 2)
    else:
        total = 0.0

    if m.views == 0 and m.likes == 0 and relevance == 0:
        total = 0.0

    metrics_available = [
        name
        for name, val in (
            ("views", m.views),
            ("likes", m.likes),
            ("comments", m.comments),
            ("shares", m.shares),
            ("saves", m.saves),
        )
        if val is not None
    ]

    return ScoreResult(
        total=total,
        performance=components["performance"],
        velocity=components["velocity"],  # já pode ser None — não força 0.0
        engagement=components["engagement"],
        relevance=components["relevance"] or 0.0,
        recency=components["recency"] or 0.0,
        trend_decay=decay,
        metrics_available=metrics_available,
    )
```

Note que `age_days`/`999` desaparecem inteiramente — `trend_decay`
continua recebendo `published_at` direto (já trata `None` como `"none"`
→ `recency=50`, comportamento existente, não muda).

### Passo 3.4 — testes

Os testes existentes em `tests/domain/test_scoring.py` já escrevem
`if comp is not None: assert 0<=comp<=100` para `velocity` (comentário
"None = métrica ausente (Fase G)") — continuam passando sem alteração,
porque sem passar `previous_metrics`/`captured_at` o novo `velocity` é
`None` por padrão, o que já era um valor aceito pelo teste.
`test_viral_score_prefers_recent_over_old` continua passando: a
diferença entre `recent`/`old` agora vem só de `recency` (que já
diferenciava os dois antes), não mais de `velocity` reforçando o mesmo
sinal — ainda `recent.total > old.total`.

Novos testes:

```python
def test_momentum_score_none_without_previous_snapshot() -> None:
    from content_factory.domain.scoring import momentum_score

    assert momentum_score(metrics(views=1000), None, NOW, None) is None


def test_momentum_score_none_when_views_missing_in_either_snapshot() -> None:
    from content_factory.domain.scoring import momentum_score
    from content_factory.domain.models import VideoMetrics

    prev = VideoMetrics(views=None)
    assert momentum_score(metrics(views=1000), prev, NOW, NOW - timedelta(hours=1)) is None


def test_momentum_score_computes_views_per_hour_rate() -> None:
    from content_factory.domain.scoring import momentum_score
    from content_factory.domain.models import VideoMetrics

    prev = VideoMetrics(views=1000)
    cur = VideoMetrics(views=2000)
    # 1000 views em 1h = 1000 views/h = cap → 100.0
    assert momentum_score(cur, prev, NOW, NOW - timedelta(hours=1)) == 100.0


def test_momentum_score_negative_delta_clamped_to_zero() -> None:
    from content_factory.domain.scoring import momentum_score
    from content_factory.domain.models import VideoMetrics

    prev = VideoMetrics(views=5000)
    cur = VideoMetrics(views=4000)  # views "caiu" (correção de contagem da plataforma)
    assert momentum_score(cur, prev, NOW, NOW - timedelta(hours=1)) == 0.0


def test_viral_score_velocity_none_without_snapshot_history() -> None:
    r = viral_score(metrics(), 0.9, NOW, NOW - timedelta(hours=6))
    assert r.velocity is None  # sem previous_metrics/captured_at passados


def test_viral_score_velocity_from_real_momentum() -> None:
    from content_factory.domain.models import VideoMetrics

    prev = VideoMetrics(views=1000)
    cur = metrics(views=2000)
    r = viral_score(
        cur,
        0.9,
        NOW,
        NOW - timedelta(hours=6),
        captured_at=NOW,
        previous_metrics=prev,
        previous_captured_at=NOW - timedelta(hours=1),
    )
    assert r.velocity == 100.0


def test_viral_score_missing_published_at_does_not_invent_velocity() -> None:
    """Antes: published_at=None forçava age_days=999 → velocity=20. Agora: None."""
    r = viral_score(metrics(), 0.9, NOW, None)
    assert r.velocity is None
```

- [x] RED (testes novos falham contra o `viral_score` atual — `momentum_score` não existe, assinatura sem os 3 kwargs novos)
- [x] GREEN
- [x] `pytest tests/domain/test_scoring.py -v`
- [x] Commit: `fix(scoring): velocity mede crescimento real entre snapshots, nao idade`

## Task 4 — `latest_for_segment` traz o snapshot anterior; `app/score.py` repassa pro `viral_score`

**Objetivo:** sem isso, a Task 3 nunca recebe `previous_metrics`/
`captured_at` de verdade — fica sempre `None` em produção.

**Files:**
- Modify: `src/content_factory/infra/db/repositories.py` (`_snapshot`, `VideoRepositoryImpl.latest_for_segment`)
- Modify: `src/content_factory/app/score.py` (`score`)
- Test: `tests/infra/test_repositories.py`

### Passo 4.1 — query da captura anterior (segunda mais recente por vídeo)

Em `src/content_factory/infra/db/repositories.py`, adicionar import de
`aliased` (`from sqlalchemy.orm import aliased`, se ainda não importado)
e `func` (já importado via `func.now()` — confirmar). Reescrever
`latest_for_segment`:

```python
async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
    """1 snapshot por vídeo do segmento com a métrica mais recente + a
    captura imediatamente anterior (Task 4 — base para `momentum_score`).

    Vídeo sem captura em `video_metrics` entra com `metrics=VideoMetrics()`
    (todos os campos `None` — Fase G: ausência ≠ zero).
    """
    stmt = (
        select(VideoRow, VideoMetricsRow)
        .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoRow.id)
        .outerjoin(VideoMetricsRow, VideoMetricsRow.video_id == VideoRow.id)
        .where(VideoSegmentRow.segment_id == segment_id)
        .ext(distinct_on(VideoRow.id))
        .order_by(VideoRow.id, VideoMetricsRow.captured_at.desc())
    )
    rows = (await self._s.execute(stmt)).all()

    rn_col = (
        func.row_number()
        .over(
            partition_by=VideoMetricsRow.video_id,
            order_by=VideoMetricsRow.captured_at.desc(),
        )
        .label("rn")
    )
    ranked = (
        select(VideoMetricsRow, rn_col)
        .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoMetricsRow.video_id)
        .where(VideoSegmentRow.segment_id == segment_id)
        .subquery()
    )
    PrevMetrics = aliased(VideoMetricsRow, ranked)
    prev_stmt = select(PrevMetrics).where(ranked.c.rn == 2)
    prev_rows = (await self._s.execute(prev_stmt)).scalars().all()
    previous_by_video = {row.video_id: row for row in prev_rows}

    return [_snapshot(video, m, previous_by_video.get(video.id)) for video, m in rows]
```

### Passo 4.2 — `_snapshot` recebe a captura anterior

```python
def _snapshot(
    video: VideoRow, m: VideoMetricsRow | None, previous: VideoMetricsRow | None = None
) -> VideoSnapshot:
    metrics = (
        VideoMetrics(
            views=m.views, likes=m.likes, comments=m.comments, shares=m.shares, saves=m.saves
        )
        if m is not None
        else VideoMetrics()
    )
    previous_metrics = (
        VideoMetrics(
            views=previous.views,
            likes=previous.likes,
            comments=previous.comments,
            shares=previous.shares,
            saves=previous.saves,
        )
        if previous is not None
        else None
    )
    return VideoSnapshot(
        video_id=video.id,
        title=video.title,
        description=video.description,
        published_at=video.published_at,
        metrics=metrics,
        captured_at=m.captured_at if m is not None else None,
        previous_metrics=previous_metrics,
        previous_captured_at=previous.captured_at if previous is not None else None,
    )
```

(Confirmar a assinatura de `_snapshot` nas outras chamadas do arquivo —
hoje só é usada dentro de `latest_for_segment`, então não há outro
call site para atualizar.)

### Passo 4.3 — `app/score.py` repassa os campos novos pro `viral_score`

Em `src/content_factory/app/score.py`:

```python
result = viral_score(
    snap.metrics,
    rel,
    now,
    snap.published_at,
    captured_at=snap.captured_at,
    previous_metrics=snap.previous_metrics,
    previous_captured_at=snap.previous_captured_at,
)
```

### Passo 4.4 — teste de integração

Estender o teste já existente `test_latest_for_segment_picks_newest_metric_and_zero_fills`
(`tests/infra/test_repositories.py:157`) — ele já cria 2 capturas
(`base` e `base + 1h`) para o mesmo vídeo, o fixture ideal pra isso:

```python
        snaps = {snap.video_id: snap for snap in await repo.latest_for_segment(seg.id)}
        assert set(snaps) == {vid, vid_vazio}
        assert snaps[vid].metrics.views == 9_999
        assert snaps[vid].metrics.likes == 50
        assert snaps[vid].captured_at == base + timedelta(hours=1)
        assert snaps[vid].previous_metrics is not None
        assert snaps[vid].previous_metrics.views == 10
        assert snaps[vid].previous_captured_at == base
        # vídeo com só 1 captura (ou nenhuma): sem par comparável
        assert snaps[vid_vazio].previous_metrics is None
        assert snaps[vid_vazio].metrics.views is None
```

- [x] RED (asserções novas falham: `VideoSnapshot` ainda não tem `previous_metrics`/`captured_at` preenchidos de verdade antes do Passo 4.1)
- [x] GREEN
- [x] `pytest tests/infra/test_repositories.py -v` (requer `TEST_DATABASE_URL`)
- [x] `pyright` (o `aliased(VideoMetricsRow, ranked)` precisa tipar limpo — se o pyright reclamar do retorno de `aliased`, usar `cast` explícito documentando o motivo, não `# type: ignore`)
- [x] Commit: `feat(scoring): latest_for_segment traz snapshot anterior para calculo de momentum`

## Task 5 — `upsert_candidates` atualiza metadados em conflito

**Problema confirmado:** em `VideoRepositoryImpl.upsert_candidates`
(`src/content_factory/infra/db/repositories.py:99-105`), o
`on_conflict_do_update` só atualiza `title` e `last_collected_at`.
`description`, `published_at`, `duration_s`, `thumbnail_url`, `raw`
ficam congelados no valor do primeiro insert — mesmo que a plataforma
devolva dados mais completos numa coleta posterior. `description` e
`title` alimentam `relevance_of` (`app/score.py:32-37`), então um título
vazio que nunca é corrigido deprime o score de relevância para sempre.

**Decisão de design:** política por campo — texto (`title`,
`description`) só é sobrescrito quando o valor novo não é vazio
(`NULLIF(excluded, '') ` + `COALESCE` para manter o antigo se o novo
vier vazio); campos opcionais (`published_at`, `duration_s`,
`thumbnail_url`) só são sobrescritos quando o novo valor não é `NULL`;
`raw` é sempre sobrescrito pelo mais recente (é o payload bruto completo
da plataforma naquela coleta, não um campo parcial — não faz sentido
mesclar).

**Files:**
- Modify: `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py`

### Passo 5.1 — `on_conflict_do_update` com `COALESCE`/`NULLIF`

```python
from sqlalchemy import func  # já deve estar importado — confirmar antes de duplicar

...
stmt = stmt.on_conflict_do_update(
    index_elements=["platform", "platform_video_id"],
    set_={
        "title": func.coalesce(func.nullif(stmt.excluded.title, ""), VideoRow.title),
        "description": func.coalesce(
            func.nullif(stmt.excluded.description, ""), VideoRow.description
        ),
        "published_at": func.coalesce(stmt.excluded.published_at, VideoRow.published_at),
        "duration_s": func.coalesce(stmt.excluded.duration_s, VideoRow.duration_s),
        "thumbnail_url": func.coalesce(stmt.excluded.thumbnail_url, VideoRow.thumbnail_url),
        "raw": stmt.excluded.raw,
        "last_collected_at": stmt.excluded.last_collected_at,
    },
)
```

### Passo 5.2 — corrigir o docstring desatualizado de `latest_for_segment`

Já reescrito no Passo 4.1 ("entra com `metrics=VideoMetrics()` (todos os
campos `None`...)" em vez do antigo "entra com métricas zeradas"). Sem
passo adicional aqui — só confirmar que o texto novo substituiu o velho
ao revisar o diff da Task 4.

### Passo 5.3 — teste de integração

```python
@requires_infra
async def test_upsert_candidates_updates_metadata_on_conflict_when_new_value_present() -> None:
    """Segunda coleta com descrição/duração reais atualiza o que a primeira
    deixou vazio — sem isso, relevance_of nunca vê a descrição real."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-metadata-{uuid4().hex[:8]}",
                name="Teste metadata",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = VideoRepositoryImpl(s)
        c1 = candidate(7)  # title="", description="" (default), published_at=None
        await repo.upsert_candidates(seg.id, "youtube", [c1])
        vid = await video_id_of(s, c1)

        c2 = cast(
            CandidateVideo,
            c1.model_copy(
                update={
                    "title": "Como fazer vela de soja",
                    "description": "Tutorial completo passo a passo",
                    "duration_s": 180,
                }
            ),
        )
        await repo.upsert_candidates(seg.id, "youtube", [c2])

        row = (await s.execute(select(VideoRow).where(VideoRow.id == vid))).scalar_one()
        assert row.title == "Como fazer vela de soja"
        assert row.description == "Tutorial completo passo a passo"
        assert row.duration_s == 180
    await engine.dispose()


@requires_infra
async def test_upsert_candidates_preserves_metadata_when_new_value_is_empty() -> None:
    """Coleta posterior sem descrição não apaga a descrição já conhecida."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-preserva-{uuid4().hex[:8]}",
                name="Teste preserva",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = VideoRepositoryImpl(s)
        c1 = cast(CandidateVideo, candidate(8).model_copy(update={"description": "descricao real"}))
        await repo.upsert_candidates(seg.id, "youtube", [c1])
        vid = await video_id_of(s, c1)

        c2 = c1.model_copy(update={"description": ""})  # coleta seguinte sem descrição
        await repo.upsert_candidates(seg.id, "youtube", [c2])

        row = (await s.execute(select(VideoRow).where(VideoRow.id == vid))).scalar_one()
        assert row.description == "descricao real"  # não foi apagada
    await engine.dispose()
```

- [x] RED → GREEN
- [x] `pytest tests/infra/test_repositories.py -v`
- [x] Commit: `fix(repositories): upsert_candidates atualiza metadados quando a nova coleta tem dado melhor`

## Task 6 — `trend_observations` não acumula o mesmo período duplicado

**Problema confirmado:** `TrendObservationRow` (`infra/db/orm.py:249-254`)
não tem unique constraint — o próprio docstring documenta que isso é
deliberado ("o mesmo período pode ser legitimamente regravado... Fase
F3 — deduplicar na leitura se necessário"). O risco real: se
`history_for_keyword(limit=N)` for consumido por uma análise futura que
trata cada linha como um ponto independente (ex.: calcular tendência de
crescimento do interesse pela keyword), reexecuções do ciclo de
pesquisa infestam o histórico com o mesmo período repetido, distorcendo
qualquer cálculo de inclinação/crescimento.

**Decisão de design:** a identidade lógica de uma observação de Trends é
`(segment_id, keyword, period_start, period_granularity)` — mesmo
período, mesma keyword, mesmo segmento é a MESMA observação, só que
medida de novo. Trocar insert puro por upsert (mesmo padrão que
`KeywordSignalRepositoryImpl.upsert_many` já usa), atualizando `interest`
e `collected_at` (quando a consulta foi feita) a cada regravação. Isso
reverte a decisão "append-only" documentada na Fase F3, mas é a correção
certa: a motivação original era não perder dado, e upsert com
`collected_at` atualizado não perde nada — só para de contar a mesma
medição duas vezes.

**Files:**
- Create: `alembic/versions/0007_trend_observations_unique_period.py`
- Modify: `src/content_factory/infra/db/orm.py` (`TrendObservationRow`)
- Modify: `src/content_factory/infra/db/repositories.py` (`TrendObservationRepositoryImpl.record_many`)
- Test: `tests/infra/test_repositories.py`

### Passo 6.1 — migração

```python
"""trend_observations: unique (segment_id, keyword, period_start, period_granularity)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # dedupe antes da constraint: mantém a linha mais recente (maior collected_at)
    # por (segment_id, keyword, period_start, period_granularity)
    op.execute(
        """
        DELETE FROM trend_observations t1
        USING trend_observations t2
        WHERE t1.segment_id = t2.segment_id
          AND t1.keyword = t2.keyword
          AND t1.period_start = t2.period_start
          AND t1.period_granularity = t2.period_granularity
          AND t1.collected_at < t2.collected_at
        """
    )
    op.drop_index("ix_trend_obs_segment_keyword_period", table_name="trend_observations")
    op.create_unique_constraint(
        "uq_trend_observation_period",
        "trend_observations",
        ["segment_id", "keyword", "period_start", "period_granularity"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_trend_observation_period", "trend_observations", type_="unique")
    op.create_index(
        "ix_trend_obs_segment_keyword_period",
        "trend_observations",
        ["segment_id", "keyword", "period_start"],
    )
```

### Passo 6.2 — ORM

```python
class TrendObservationRow(Base):
    """Série temporal do Google Trends — upsert por período (Fase H: mesmo
    período medido de novo atualiza a observação, não duplica)."""

    __tablename__ = "trend_observations"
    __table_args__ = (
        UniqueConstraint(
            "segment_id",
            "keyword",
            "period_start",
            "period_granularity",
            name="uq_trend_observation_period",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    keyword: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_granularity: Mapped[str] = mapped_column(String(8))
    interest: Mapped[float] = mapped_column(Float)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

### Passo 6.3 — `record_many` com upsert

```python
class TrendObservationRepositoryImpl(TrendObservationRepository):
    """Upsert por (segment_id, keyword, period_start, period_granularity) —
    Fase H: mesmo período regravado atualiza `interest`/`collected_at`,
    não duplica a linha (ver ADR-0005 revisado)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record_many(self, segment_id: int, points: Sequence[TrendPoint]) -> int:
        for p in points:
            stmt = pg_insert(TrendObservationRow).values(
                segment_id=segment_id,
                keyword=p.keyword,
                period_start=p.period_start,
                period_granularity=p.period_granularity,
                interest=p.interest,
                collected_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["segment_id", "keyword", "period_start", "period_granularity"],
                set_={
                    "interest": stmt.excluded.interest,
                    "collected_at": stmt.excluded.collected_at,
                },
            )
            await self._s.execute(stmt)
        await self._s.flush()
        return len(points)

    async def history_for_keyword(
        self, segment_id: int, keyword: str, limit: int
    ) -> list[TrendPoint]:
        # sem mudança nesta task — já ordena por period_start desc, e com
        # a unique constraint do Passo 6.1 nunca mais devolve o mesmo
        # período 2x
        stmt = (
            select(TrendObservationRow)
            .where(
                TrendObservationRow.segment_id == segment_id,
                TrendObservationRow.keyword == keyword,
            )
            .order_by(TrendObservationRow.period_start.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            TrendPoint(
                keyword=r.keyword,
                period_start=r.period_start,
                period_granularity=r.period_granularity,
                interest=r.interest,
            )
            for r in rows
        ]
```

### Passo 6.4 — teste de integração

```python
@requires_infra
async def test_trend_observation_record_many_upserts_same_period() -> None:
    """Reexecutar o ciclo de pesquisa na mesma semana atualiza a observação,
    não duplica — sem isso, history_for_keyword(limit=N) conta o mesmo
    período 2x."""
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-trend-{uuid4().hex[:8]}",
                name="Teste trend",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = TrendObservationRepositoryImpl(s)
        period = datetime(2026, 10, 1, tzinfo=UTC)
        point = TrendPoint(
            keyword="vela aromatica", period_start=period, period_granularity="week", interest=40.0
        )
        await repo.record_many(seg.id, [point])
        # pytrends reconsultado no mesmo período, interesse mudou
        point_atualizado = point.model_copy(update={"interest": 65.0})
        await repo.record_many(seg.id, [point_atualizado])
        await s.commit()

        history = await repo.history_for_keyword(seg.id, "vela aromatica", limit=10)
        assert len(history) == 1  # não duplicou
        assert history[0].interest == 65.0  # valor mais recente venceu
    await engine.dispose()
```

- [x] `alembic upgrade head`, `alembic check`
- [x] RED → GREEN
- [x] `pytest tests/infra/test_repositories.py -v`
- [x] Commit: `fix(trends): trend_observations faz upsert por periodo em vez de acumular duplicata`

## Task 7 — `CollectRun` separa candidatos ignorados de falha de plataforma

**Problema:** `CollectRun.failed` hoje carrega dois significados
diferentes dependendo do caminho de execução. No caminho de sucesso
(`collect.py:69`, `research_pipeline.py:102`), `run.failed = stats.skipped`
— candidatos revertidos pelo savepoint (`DataError`/`IntegrityError` no
upsert), com `run.status="ok"`. No caminho de exceção
(`collect.py:74-78`, loop social de `research_pipeline.py:110-115`),
`run.status` vira `"failed"`/`"blocked"` mas `run.failed` nunca é
setado — fica no default `0` do dataclass. Resultado: uma linha de
`collect_runs` com `status="ok", failed=2` (2 candidatos ignorados) é
indistinguível, só pelo nome da coluna, de uma falha real de
plataforma — e uma falha real de plataforma tem `failed=0`, o que é o
inverso do que o nome da coluna sugere. É deliberado e documentado (3
lugares: comentário em `collect.py:67-68`, docstring do teste
`test_skipped_candidates_visible_in_run_failed_and_counts`), mas a
ambiguidade de leitura é real — resolver com um campo novo, sem quebrar
o que já existe.

**Decisão de design:** `CollectRun` ganha `skipped: int = 0`. O caminho
de sucesso passa a gravar `run.skipped = stats.skipped` (não mais
`run.failed`). O caminho de exceção passa a gravar `run.failed = 1`
(hoje fica implícito em `run.status`; explicitar no campo numérico
também permite somar falhas de plataforma ao longo de uma janela via
`SUM(failed)`, não só contar por `status`). `counts["failed"]`/
`counts["skipped"]` no retorno do use-case (`CollectOutcome`/
`ResearchOutcome`) não mudam — já estavam corretos, o problema era só
a coluna do audit.

**Files:**
- Create: `alembic/versions/0008_collect_runs_skipped_column.py`
- Modify: `src/content_factory/domain/models.py` (`CollectRun`)
- Modify: `src/content_factory/infra/db/orm.py` (`CollectRunRow`)
- Modify: `src/content_factory/app/collect.py`
- Modify: `src/content_factory/app/research_pipeline.py`
- Test: `tests/app/test_collect.py`, `tests/app/test_research_pipeline.py`, `tests/infra/test_repositories.py`

### Passo 7.1 — migração

```python
"""collect_runs: coluna skipped (separa de failed)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collect_runs", sa.Column("skipped", sa.Integer(), nullable=False, server_default="0")
    )
    # backfill: runs antigas gravaram o "ignorado" em failed quando status=ok —
    # migrar o valor pra skipped e zerar failed nesses casos específicos
    op.execute(
        "UPDATE collect_runs SET skipped = failed, failed = 0 WHERE status = 'ok' AND failed > 0"
    )


def downgrade() -> None:
    op.execute("UPDATE collect_runs SET failed = skipped WHERE status = 'ok' AND skipped > 0")
    op.drop_column("collect_runs", "skipped")
```

### Passo 7.2 — `CollectRun` e `CollectRunRow`

Em `src/content_factory/domain/models.py`:

```python
class CollectRun(BaseModel):
    collector: str
    segment_id: int | None = None
    status: str = "running"  # running|ok|partial|failed|blocked
    found: int = 0
    inserted: int = 0
    updated: int = 0
    failed: int = 0  # Fase H: só falha de plataforma (discover/upsert abortou)
    skipped: int = 0  # Fase H: candidatos ignorados pelo savepoint, run continua ok
    quota_used: int = 0
    retries: int = 0
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
```

Em `src/content_factory/infra/db/orm.py`, no `CollectRunRow`, adicionar
`skipped: Mapped[int] = mapped_column(Integer, default=0)` ao lado de
`failed`. `CollectRunRepositoryImpl.record` já usa
`CollectRunRow(**run.model_dump())` — não precisa editar, o campo novo
flui automaticamente.

### Passo 7.3 — `app/collect.py`

```python
try:
    await deps.limiter.acquire(collector.platform)
    found = await collector.discover(segment, limit)
    stats = await deps.videos.upsert_candidates(segment.id, collector.platform, found)
    run.status = "ok"
    run.found = len(found)
    run.inserted, run.updated = stats.inserted, stats.updated
    run.skipped = stats.skipped  # era run.failed = stats.skipped
    counts["found"] = int(counts["found"]) + len(found)
    counts["inserted"] = int(counts["inserted"]) + stats.inserted
    counts["updated"] = int(counts["updated"]) + stats.updated
    counts["skipped"] = int(counts["skipped"]) + stats.skipped
except CollectorError as exc:
    run.status = "blocked" if exc.code == "blocked" else "failed"
    run.error_code = exc.code
    run.failed = 1  # Fase H: explícito no campo numérico, não só em status
    errors += 1
    counts["failed"] = int(counts["failed"]) + 1
```

### Passo 7.4 — `app/research_pipeline.py`

No mesmo loop social já alterado pela Task 1:

```python
try:
    await deps.limiter.acquire(platform)
    candidates = await discoverer.discover_by_keywords(keywords_for_social, [], 1)
    stats = await deps.videos.upsert_candidates(segment.id, platform, candidates)
    run.status = "ok"
    run.found = len(candidates)
    run.inserted, run.updated = stats.inserted, stats.updated
    run.skipped = stats.skipped  # era run.failed = stats.skipped
    social_found += len(candidates)
    social_inserted += stats.inserted
    social_updated += stats.updated
    social_skipped += stats.skipped
except Exception as exc:
    run.status = "failed"
    run.error_code = getattr(exc, "code", "internal_error")
    run.failed = 1  # Fase H
    social_platform_errors += 1
    counts[f"{platform}_failed"] = int(counts.get(f"{platform}_failed", 0)) + 1
```

### Passo 7.5 — atualizar os testes existentes que checavam o campo antigo

`tests/app/test_collect.py::test_skipped_candidates_visible_in_run_failed_and_counts`
— renomear e trocar a asserção final:

```python
async def test_skipped_candidates_visible_in_run_skipped_and_counts() -> None:
    """Candidato descartado pelo savepoint (REVISÃO §6 #1) aparece no audit,
    separado de falha de plataforma (Fase H: CollectRun.skipped != .failed).
    """
    repos = FakeRepos(skipped=2)
    deps = CollectDeps(
        collectors=[FakeCollector("youtube", n=3)],
        videos=repos.videos,
        segments=repos.segments,
        runs=repos.runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    out = await collect("candles", ["youtube"], limit=5, deps=deps)
    assert out.ok is True  # plataforma respondeu; só candidatos caíram
    assert out.counts["skipped"] == 2
    assert out.counts["failed"] == 0
    assert out.runs[0].skipped == 2  # era out.runs[0].failed == 2
    assert out.runs[0].failed == 0  # Fase H: failed não carrega mais skip
    assert out.runs[0].status == "ok"
```

E `test_limiter_blocked_marks_run_blocked_without_discover` ganha uma
asserção nova confirmando que `run.failed` agora é populado de verdade
no caminho de exceção:

```python
    run = out.runs[0]
    assert run.status == "blocked" and run.error_code == "blocked"
    assert run.failed == 1  # Fase H: explícito, não só via status
    assert run.skipped == 0
    assert run.finished_at is not None
```

Em `tests/app/test_research_pipeline.py::test_run_research_cycle_social_failure_records_failed_run`
(já tocado pela Task 1), adicionar:

```python
    assert repos.runs.recorded[0].failed == 1  # Fase H
    assert repos.runs.recorded[0].skipped == 0
```

### Passo 7.6 — teste de integração do roundtrip no Postgres

```python
@requires_infra
async def test_collect_run_record_persists_skipped_separate_from_failed() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-skipped-run-{uuid4().hex[:8]}",
                name="Teste skipped run",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        run = CollectRun(
            collector="youtube",
            segment_id=seg.id,
            status="ok",
            found=3,
            inserted=1,
            updated=0,
            failed=0,
            skipped=2,
            started_at=datetime.now(UTC),
        )
        run_id = await CollectRunRepositoryImpl(s).record(run)
        await s.commit()
        row = (
            await s.execute(select(CollectRunRow).where(CollectRunRow.id == run_id))
        ).scalar_one()
        assert row.skipped == 2
        assert row.failed == 0
    await engine.dispose()
```

- [x] RED (testes renomeados/novos falham contra o código atual — `CollectRun` ainda não tem `skipped`)
- [x] GREEN
- [x] `alembic upgrade head`, `alembic check`
- [x] `pytest tests/app/test_collect.py tests/app/test_research_pipeline.py tests/infra/test_repositories.py -v`
- [x] Commit: `fix(audit): CollectRun separa candidatos ignorados (skipped) de falha de plataforma (failed)`

## Task 8 — metodologia de avaliação empírica do ranking (P2)

**Natureza da task:** diferente das Tasks 1-7, isto não é um bug a
corrigir no domínio — é a ausência de um processo de validação. Não dá
pra "testar com TDD" se o ranking está certo, porque "certo" aqui
depende de julgamento humano sobre vídeos reais, não de uma asserção
determinística. O que esta task entrega é a ferramenta pra fazer essa
validação de forma repetível, não o veredito em si (que depende de
dados reais coletados em produção, fora do escopo deste plano).

**Decisão de design:** um comando CLI novo, `cf score-sample`, que
exporta os N vídeos mais bem pontuados de um segmento (com todos os
componentes do `ScoreResult`, não só o total) em CSV, para um humano
anotar manualmente uma coluna `avaliacao_manual` (ex.: 1-5) fora da
ferramenta. Não automatiza a avaliação — automatiza a extração dos
dados que o humano precisa pra avaliar, e depois a comparação
(Spearman/Kendall tau entre o rank do sistema e o rank manual) num
segundo comando.

**Files:**
- Create: `src/content_factory/app/score_sample.py`
- Modify: `src/content_factory/cli/main.py` (novo subcomando)
- Test: `tests/app/test_score_sample.py`

### Passo 8.1 — use-case puro de exportação

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ScoreSampleRow:
    video_id: int
    url: str
    total: float
    performance: float | None
    velocity: float | None
    engagement: float | None
    relevance: float
    recency: float
    metrics_available: list[str]


class ScoreSampleRepository(Protocol):
    async def top_scored_for_segment(self, segment_id: int, limit: int) -> list[ScoreSampleRow]:
        """Últimos scores calculados, ordenados por viral_score desc."""
        ...


async def score_sample(
    segment_id: int, limit: int, repo: ScoreSampleRepository
) -> list[ScoreSampleRow]:
    return await repo.top_scored_for_segment(segment_id, limit)
```

### Passo 8.2 — implementação do repositório (junta `video_scores` + `videos`)

Em `src/content_factory/infra/db/repositories.py`, nova classe
`ScoreSampleRepositoryImpl` — query: `video_scores` mais recente por
vídeo (mesmo padrão `DISTINCT ON` de `latest_for_segment`) joinado com
`videos.url`, ordenado por `viral_score DESC LIMIT :limit`.

### Passo 8.3 — comando `cf score-sample --segment <slug> --limit 50 --out <path>.csv`

Grava CSV com cabeçalho `video_id,url,total,performance,velocity,engagement,relevance,recency,metrics_available,avaliacao_manual`
— a última coluna fica vazia, para preenchimento manual.

### Passo 8.4 — comando `cf score-correlation --csv <path>`

Lê o CSV já anotado (`avaliacao_manual` preenchida), calcula
Spearman entre `total` (rank do sistema) e `avaliacao_manual` (rank
humano) via `scipy.stats.spearmanr` (dependência nova — checar
pip-audit/licença antes de adicionar ao `pyproject.toml`), devolve no
envelope `{"ok": true, "n": N, "spearman": r, "p_value": p}`.

**Critério de aceitação desta task:** o comando existe, testado (gerar
CSV de um segmento fake, reler, calcular correlação de uma amostra
sintética com correlação conhecida). A VALIDAÇÃO EM SI — rodar contra
dados reais e decidir se o ranking é bom — é trabalho humano
pós-plano, não uma asserção de teste.

- [x] RED → GREEN (testes do use-case puro com dados sintéticos)
- [x] `pytest tests/app/test_score_sample.py -v`
- [x] Commit: `feat(score): cf score-sample + cf score-correlation para validacao empirica do ranking`

## Gates finais (todas as tasks)

- [x] `pytest` completo (unit + `@requires_infra` com `TEST_DATABASE_URL`)
- [x] `ruff check` / `ruff format --check`
- [x] `pyright` strict — 0 erros
- [x] `alembic check` — sem operações pendentes
- [x] `bandit` — 0
- [x] `pip-audit` — 0 vulnerabilidades
- [x] `mkdocs build --strict`
- [x] teste de privacidade (spec §10)
- [x] Atualizar `docs/HANDOFF.md` (seção "Fase H'") e marcar os itens
  correspondentes no roadmap (`docs/ai-plans/2026-10-12-content-factory-melhorias-roadmap.md`)
