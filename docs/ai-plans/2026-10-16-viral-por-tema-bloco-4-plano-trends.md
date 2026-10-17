# Bloco 4 — Plano de consulta por coleta e Trends aplicado (R5+R3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** As buscas passam a sair do **pool** (Bloco 1), com cota fixa e pré-registrada por origem, e o Trends passa a medir os **melhores termos** do pool (não as 5 primeiras frases), sempre relativo a um termo-âncora.

**Architecture:** Duas peças puras em `domain/`: `search_plan.py` (cota + rodízio determinístico sem estado, mesmo padrão de `run_index_for`) e `trends_rank.py` (lotes com âncora, interesse relativo, inclinação). `collect()` deixa de montar queries das keywords e passa a ler o pool; `research_pipeline` troca as 5 primeiras frases por lotes de top-N do pool.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy 2 async, Alembic, pytrends, pytest (asyncio), ruff, pyright strict.

## Global Constraints

- `domain/` puro; `app/` → portas; `infra/` implementa portas; `cli/worker → app → domain`.
- `pyright` strict 0 erros; `ruff` linha 100/aspas duplas; `ruff format` limpo; TDD.
- Envelope só com contagens; sem segredo nem caminho absoluto de home rastreado.
- `make gates` antes de "pronto" (inclui `alembic check`).
- Migração aditiva: `0014` segue `0013` (Bloco 3). *(Bloco 5 passa a usar `0015`.)*
- Não tocar `video_scores`/`viral_score`/`FORMULA_VERSION`. A web segue o próprio protocolo `q1` — fora deste plano.

## Referência

Especificação §4 R5 e R3; §5 (cota), §6 (divisão fixa pré-registrada). **Depende do Bloco 1** (pool `segment_terms`) e do Bloco 2 (`search_terms`).

## Decisões deste bloco

- **Cota dividida por origem, fixa durante o teste** (sem realocação adaptativa — fora de escopo). YouTube: 3 buscas/coleta = `seed`, `suggest_trends`, `hashtag:youtube`. Enquanto o pool de hashtags estiver vazio, a 3ª vaga vai para `suggest_trends` (2 termos diferentes).
- **Rodízio determinístico e sem estado** por `(dia, slot)`: `run_index_for(now, coletas_por_dia=4)`. Mesmo conceito do web (`domain/web_queries.run_index_for`), adaptado a 4 coletas/dia.
- `suggest_trends` = pool `suggest` + `trends` ordenado por `kw_rank`/`trend_score` (o que existir).
- **Trends com âncora fixa** = primeira keyword do segmento; lotes de `[âncora] + 4 termos`; `N = TRENDS_TERMS_PER_DAY` (16).
- **`trend_score`** (versionado `trends-anchor-v1`) = 0,7 × interesse relativo médio (7 dias) + 0,3 × inclinação normalizada; gravado em `segment_terms.trend_score` **e** em `term_trend_days` (append por termo/dia — satisfaz "por termo e dia").
- Envs: `TRENDS_TERMS_PER_DAY=16`, `SEARCHES_PER_COLLECT_<PLATAFORMA>` (ex.: `..._YOUTUBE=3`).

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/search_plan.py` (novo) | Cota por origem + rodízio + `plan_queries` por plataforma. |
| `src/content_factory/domain/trends_rank.py` (novo) | Lotes com âncora, interesse relativo, `trend_score`. |
| `src/content_factory/app/ports.py` (editar) | `SegmentTermRepository.set_trend_scores`; `TermTrendRepository`; `TrendsProvider.interest_over_time` inalterada (lotes são do app). |
| `src/content_factory/infra/db/orm.py` (editar) | `TermTrendDayRow`. |
| `alembic/versions/0014_term_trend_days.py` (novo) | Tabela `term_trend_days`. |
| `src/content_factory/infra/db/repositories.py` (editar) | `TermTrendRepositoryImpl`; `set_trend_scores` no pool. |
| `src/content_factory/app/collect.py` (editar) | Queries do pool: `plan_queries` (substitui `queries_for_platform` do Bloco 2). |
| `src/content_factory/app/research_pipeline.py` (editar) | Lotes de Trends dos top-N do pool. |
| `src/content_factory/infra/queue/composition.py` (editar) | `CollectDeps.terms`, `ResearchDeps.terms`, `trend_days`. |
| `tests/...` | TDD por arquivo. |

---

### Task 1: Plano de consulta puro (`search-plan-v1`)

**Files:**
- Create: `src/content_factory/domain/search_plan.py`
- Test: `tests/domain/test_search_plan.py`

**Interfaces:**
- Produces:
  - `SEARCH_PLAN_VERSION = "search-plan-v1"`
  - `@dataclass(frozen=True) class PoolSnapshot: seeds: tuple[str, ...] = (); suggest: tuple[str, ...] = (); trends: tuple[str, ...] = (); hashtags: tuple[str, ...] = ()`
  - `def run_index_for(now: datetime, collections_per_day: int = 4) -> int`
  - `def searches_per_collect(platform: str, env: Mapping[str, str]) -> int`
  - `def plan_queries(pool: PoolSnapshot, platform: str, run_index: int, searches: int) -> list[SearchQuery]`

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_search_plan.py
from datetime import UTC, datetime
from content_factory.domain.search_plan import (
    SEARCH_PLAN_VERSION,
    PoolSnapshot,
    plan_queries,
    run_index_for,
    searches_per_collect,
)

POOL = PoolSnapshot(
    seeds=("vela artesanal", "vela de soja", "vela aromática"),
    suggest=("como fazer vela", "vela em pote"),
    trends=("vela de natal",),
    hashtags=("velasmagicas",),
)
NOW = datetime(2026, 10, 20, 8, 5, tzinfo=UTC)


def test_version_e_rodizio_de_4_coletas_por_dia() -> None:
    assert SEARCH_PLAN_VERSION == "search-plan-v1"
    a = run_index_for(datetime(2026, 10, 20, 2, 0, tzinfo=UTC), 4)
    b = run_index_for(datetime(2026, 10, 20, 8, 0, tzinfo=UTC), 4)
    c = run_index_for(datetime(2026, 10, 21, 2, 0, tzinfo=UTC), 4)
    assert a != b and (c - a) >= 4  # 4 slots por dia


def test_cota_por_env_com_default_youtube_3() -> None:
    assert searches_per_collect("youtube", {}) == 3
    assert searches_per_collect("youtube", {"SEARCHES_PER_COLLECT_YOUTUBE": "5"}) == 5
    assert searches_per_collect("instagram", {}) == 1


def test_plano_divide_por_origem() -> None:
    qs = plan_queries(POOL, "youtube", run_index=7, searches=3)
    assert [q.origin for q in qs] == ["seed", "suggest_trends", "hashtag:youtube"]


def test_rodizio_de_seed_varia_com_o_run_index() -> None:
    def seed_of(idx: int) -> str:
        return next(q.term for q in plan_queries(POOL, "youtube", idx, 3) if q.origin == "seed")

    assert seed_of(0) != seed_of(1)
    assert seed_of(0) == seed_of(3)  # len(seeds) == 3 → ciclo


def test_hashtag_vazia_manda_a_vaga_para_suggest() -> None:
    pool = PoolSnapshot(seeds=("vela",), suggest=("a", "b", "c"), hashtags=())
    qs = plan_queries(pool, "youtube", run_index=0, searches=3)
    assert [q.origin for q in qs].count("suggest_trends") == 2
    assert "hashtag:youtube" not in [q.origin for q in qs]
    assert len({q.term for q in qs}) == len(qs)  # sem repetir termo


def test_query_ids_sao_unicos() -> None:
    qs = plan_queries(POOL, "youtube", run_index=1, searches=3)
    assert len({q.query_id for q in qs}) == len(qs)
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/domain/test_search_plan.py -q` → FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/search_plan.py
"""Plano de consulta por coleta (R5) — puro, determinístico e sem estado.

Cota fixa por origem, pré-registrada para o teste de 4 dias: NÃO há
realocação adaptativa (só depois do teste, com os dados dele). A divisão por
origem é o que permite comparar `seed` × `suggest` × `trends` × `hashtag`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from content_factory.domain.models import SearchQuery

SEARCH_PLAN_VERSION = "search-plan-v1"
DEFAULT_SEARCHES_PER_COLLECT = {"youtube": 3, "instagram": 1, "tiktok": 1}
_SUGGEST_TRENDS = "suggest_trends"


@dataclass(frozen=True)
class PoolSnapshot:
    """Pool do segmento já filtrado e ranqueado (o que existe hoje)."""

    seeds: tuple[str, ...] = ()
    suggest: tuple[str, ...] = ()
    trends: tuple[str, ...] = ()
    hashtags: tuple[str, ...] = ()


def run_index_for(now: datetime, collections_per_day: int = 4) -> int:
    """Índice do rodízio sem estado: nº de slots desde a data ordinal.

    Determinístico por `(dia, slot do dia)`; dois workers que rodem o mesmo
    slot escolhem os MESMOS termos (reprodutibilidade do pré-registro).
    """
    slot = min(now.hour * collections_per_day // 24, collections_per_day - 1)
    return now.date().toordinal() * collections_per_day + slot


def searches_per_collect(platform: str, env: Mapping[str, str]) -> int:
    """Cota de buscas por coleta (env `SEARCHES_PER_COLLECT_<PLATAFORMA>`)."""
    raw = env.get(f"SEARCHES_PER_COLLECT_{platform.upper()}")
    if raw is not None:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value >= 1:
            return value
    return DEFAULT_SEARCHES_PER_COLLECT.get(platform, 1)


def _pick(items: tuple[str, ...], index: int) -> str | None:
    return items[index % len(items)] if items else None


def plan_queries(
    pool: PoolSnapshot, platform: str, run_index: int, searches: int
) -> list[SearchQuery]:
    """`searches` consultas: 1 por origem, em rodízio, sem repetir termo.

    Ordem das origens: `seed` → `suggest_trends` → `hashtag:<plataforma>`;
    vaga de hashtag sem pool vai para `suggest_trends` (termo diferente).
    """
    ordered = list(pool.suggest) + list(pool.trends)
    chosen: list[tuple[str, str]] = []

    seed = _pick(pool.seeds, run_index)
    if seed:
        chosen.append(("seed", seed))

    suggest_cursor = 0
    if searches >= 2 and ordered:
        chosen.append((_SUGGEST_TRENDS, ordered[run_index % len(ordered)]))
        suggest_cursor = 1

    hashtag_slot = searches >= 3
    if hashtag_slot and pool.hashtags:
        chosen.append((f"hashtag:{platform}", pool.hashtags[run_index % len(pool.hashtags)]))
    elif hashtag_slot and ordered:
        # fallback: vaga da hashtag vai para suggest_trends (outro termo)
        extra = ordered[(run_index + suggest_cursor) % len(ordered)]
        if extra not in {t for _, t in chosen}:
            chosen.append((_SUGGEST_TRENDS, extra))

    seen: set[str] = set()
    out: list[SearchQuery] = []
    for i, (origin, term) in enumerate(chosen):
        if term in seen:
            continue
        seen.add(term)
        out.append(
            SearchQuery(term=term, origin=origin, query_id=f"{SEARCH_PLAN_VERSION}:{origin}:{i}")
        )
    return out
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/search_plan.py tests/domain/test_search_plan.py
git commit -m "feat(domain): search-plan-v1 — cota por origem e rodízio sem estado (R5)"
```

---

### Task 2: Coluna/serviço de `trend_score` no pool + `term_trend_days` (migração `0014`)

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`
- Create: `alembic/versions/0014_term_trend_days.py`
- Test: `tests/infra/test_orm.py` (editar)

**Interfaces:**
- Produces: `TermTrendDayRow` (`segment_id`, `term`, `day`, `trend_score`, `interest_mean`, `slope`, `anchor`).

- [ ] **Step 1: Teste que falha**

```python
def test_tabela_term_trend_days() -> None:
    from content_factory.infra.db.orm import TermTrendDayRow

    assert {c.name for c in TermTrendDayRow.__table__.columns} >= {
        "segment_id",
        "term",
        "day",
        "trend_score",
        "interest_mean",
        "slope",
        "anchor",
    }
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar ORM + migração**

```python
class TermTrendDayRow(Base):
    """R3: `trend_score` por termo e dia (append; a média/inclinação de 7 dias
    é derivável do histórico). O valor corrente do pool fica em
    `segment_terms.trend_score`."""

    __tablename__ = "term_trend_days"
    __table_args__ = (UniqueConstraint("segment_id", "term", "day", name="uq_term_trend_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    term: Mapped[str] = mapped_column(String(200))
    day: Mapped[date] = mapped_column(Date)
    trend_score: Mapped[float] = mapped_column(Float)
    interest_mean: Mapped[float | None] = mapped_column(Float)
    slope: Mapped[float | None] = mapped_column(Float)
    anchor: Mapped[str] = mapped_column(String(200))
```

Migração `0014` (revision `0014`, down `0013`) criando a tabela + unique. `from datetime import date` e `Date` no ORM.

- [ ] **Step 4: Rodar e ver passar + `uv run alembic check`** → PASS, sem drift.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/orm.py alembic/versions/0014_term_trend_days.py tests/infra/test_orm.py
git commit -m "feat(db): migração 0014 — term_trend_days (R3)"
```

---

### Task 3: Ranking de Trends puro (`trends-anchor-v1`)

**Files:**
- Create: `src/content_factory/domain/trends_rank.py`
- Test: `tests/domain/test_trends_rank.py`

**Interfaces:**
- Produces:
  - `TRENDS_RANK_VERSION = "trends-anchor-v1"`
  - `@dataclass(frozen=True) class Batch: anchor: str; terms: tuple[str, ...]`
  - `def plan_trend_batches(terms: Sequence[str], anchor: str, per_batch: int = 4, max_terms: int = 16) -> list[Batch]`
  - `def relative_interest(points: Sequence[TrendPoint], anchor_points: Sequence[TrendPoint]) -> tuple[float | None, float | None]` — `(média relativa, inclinação)`
  - `def trend_score(interest_mean: float | None, slope: float | None) -> float`

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_trends_rank.py
from datetime import UTC, datetime, timedelta
from content_factory.domain.models import TrendPoint
from content_factory.domain.trends_rank import (
    TRENDS_RANK_VERSION,
    plan_trend_batches,
    relative_interest,
    trend_score,
)

ANCHOR, NOW = "vela artesanal", datetime(2026, 10, 20, tzinfo=UTC)


def _series(kw: str, values: list[float]) -> list[TrendPoint]:
    return [
        TrendPoint(
            keyword=kw, period_start=NOW - timedelta(weeks=i), period_granularity="week", interest=v
        )
        for i, v in enumerate(reversed(values))
    ]


def test_version() -> None:
    assert TRENDS_RANK_VERSION == "trends-anchor-v1"


def test_lotes_de_5_com_ancora_fixa() -> None:
    batches = plan_trend_batches(tuple(f"t{i}" for i in range(16)), ANCHOR)
    assert all(b.anchor == ANCHOR for b in batches)
    assert all(len(b.terms) == 4 for b in batches)
    assert len(batches) == 4  # 16 termos / 4


def test_lote_sobra_menos_de_4() -> None:
    batches = plan_trend_batches(("a", "b", "c", "d", "e"), ANCHOR, max_terms=5)
    assert [len(b.terms) for b in batches] == [4, 1]


def test_interesse_relativo_normaliza_pela_ancora() -> None:
    kw = _series("vela de natal", [50, 50])
    anchor = _series(ANCHOR, [100, 100])
    mean, slope = relative_interest(kw, anchor)
    assert mean == 0.5
    assert slope == 0.0


def test_ancora_zero_devolve_none() -> None:
    mean, slope = relative_interest(_series("x", [10, 20]), _series(ANCHOR, [0, 0]))
    assert mean is None and slope is None


def test_inclinacao_positiva_quando_sobe() -> None:
    kw = _series("subindo", [10, 40])
    anchor = _series(ANCHOR, [100, 100])
    _, slope = relative_interest(kw, anchor)
    assert slope is not None and slope > 0


def test_trend_score_combina_media_e_inclinacao() -> None:
    assert trend_score(None, None) == 0.0
    a = trend_score(0.5, 0.0)
    b = trend_score(0.5, 1.0)
    assert b > a > 0.0
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/trends_rank.py
"""Trends aplicado ao pool (R3) — puro e versionado.

O Trends normaliza DENTRO de cada consulta: comparar lotes exige âncora
comum. Cada lote leva a âncora fixa (1ª keyword do segmento) + 4 termos; o
interesse de cada termo vira razão sobre a âncora, e aí lotes diferentes são
comparáveis.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from content_factory.domain.models import TrendPoint

TRENDS_RANK_VERSION = "trends-anchor-v1"
W_INTEREST = 0.7
W_SLOPE = 0.3
PER_BATCH = 4  # + 1 âncora = 5 (limite do Trends por consulta)


@dataclass(frozen=True)
class Batch:
    anchor: str
    terms: tuple[str, ...]


def plan_trend_batches(
    terms: Sequence[str], anchor: str, per_batch: int = PER_BATCH, max_terms: int = 16
) -> list[Batch]:
    """Top-`max_terms` em lotes de `per_batch`, todos com a MESMA âncora."""
    selected = [t for t in dict.fromkeys(terms) if t != anchor][:max_terms]
    return [
        Batch(anchor=anchor, terms=tuple(selected[i : i + per_batch]))
        for i in range(0, len(selected), per_batch)
    ]


def _by_period(points: Sequence[TrendPoint]) -> list[float]:
    ordered = sorted(points, key=lambda p: p.period_start)
    return [p.interest for p in ordered]


def relative_interest(
    points: Sequence[TrendPoint], anchor_points: Sequence[TrendPoint]
) -> tuple[float | None, float | None]:
    """(média do interesse relativo, inclinação normalizada) — `None` sem âncora.

    Relativo = série do termo / série da âncora, ponto a ponto (mesmos
    períodos). Inclinação = (último − primeiro) / média, na série relativa.
    """
    series = _by_period(points)
    anchor = _by_period(anchor_points)
    n = min(len(series), len(anchor))
    if n == 0:
        return None, None
    ratios: list[float] = []
    for i in range(n):
        base = anchor[i]
        ratios.append(series[i] / base if base > 0 else 0.0)
    mean = sum(ratios) / len(ratios)
    if mean <= 0:
        return 0.0, 0.0
    slope = (ratios[-1] - ratios[0]) / mean
    return round(mean, 6), round(slope, 6)


def trend_score(interest_mean: float | None, slope: float | None) -> float:
    """Combinação versionada; termo sem âncora comparável vale 0,0."""
    if interest_mean is None:
        return 0.0
    normalized_slope = max(-1.0, min(1.0, slope or 0.0))
    return round(W_INTEREST * interest_mean + W_SLOPE * normalized_slope, 6)
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/trends_rank.py tests/domain/test_trends_rank.py
git commit -m "feat(domain): trends-anchor-v1 — lotes com âncora e interesse relativo (R3)"
```

---

### Task 4: Portas e repositório do trend por termo/dia

**Files:**
- Modify: `src/content_factory/app/ports.py`
- Modify: `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Produces:
  - `SegmentTermRepository.set_trend_scores(segment_id: int, scores: Mapping[str, float]) -> int` (atualiza `segment_terms.trend_score`)
  - `TermTrendRepository.record_many(segment_id: int, rows: Sequence[TermTrendDay]) -> int`
  - Modelo `TermTrendDay` em `domain/models.py`: `term: str; day: date; trend_score: float; interest_mean: float | None; slope: float | None; anchor: str`

- [ ] **Step 1: Teste que falha** — integração: `set_trend_scores` grava `trend_score` nos termos do pool; `record_many` insere 2 linhas e refazendo o mesmo dia não duplica (upsert).

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# repositories.py
    async def set_trend_scores(self, segment_id: int, scores: Mapping[str, float]) -> int:
        for term, value in scores.items():
            await self._s.execute(
                update(SegmentTermRow)
                .where(SegmentTermRow.segment_id == segment_id, SegmentTermRow.term == term)
                .values(trend_score=value, last_seen_at=datetime.now(UTC))
            )
        return len(scores)


class TermTrendRepositoryImpl(TermTrendRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record_many(self, segment_id: int, rows: Sequence[TermTrendDay]) -> int:
        for r in rows:
            stmt = pg_insert(TermTrendDayRow).values(
                segment_id=segment_id, term=r.term, day=r.day, trend_score=r.trend_score,
                interest_mean=r.interest_mean, slope=r.slope, anchor=r.anchor,
            ).on_conflict_do_update(
                index_elements=["segment_id", "term", "day"],
                set_={"trend_score": r.trend_score, "interest_mean": r.interest_mean,
                      "slope": r.slope, "anchor": r.anchor},
            )
            await self._s.execute(stmt)
        return len(rows)
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ports.py src/content_factory/domain/models.py \
        src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(db): trend_score no pool e term_trend_days (R3)"
```

---

### Task 5: Coleta usa o plano do pool (R5)

**Files:**
- Modify: `src/content_factory/app/collect.py`
- Modify: `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_collect.py` (editar)

**Interfaces:**
- Consumes: `plan_queries`, `PoolSnapshot`, `SegmentTermRepository`, `run_index_for`, `searches_per_collect`.
- Produces:
  - `CollectDeps.terms: SegmentTermRepository` (default `NoOpSegmentTermRepository`).
  - `CollectDeps.env: Mapping[str, str]` (default `os.environ`) — cota por env, injetável em teste.
  - `queries_for_platform(...)` **removido**; substituído por `_pool_snapshot(segment_id, deps)` + `plan_queries`.

- [ ] **Step 1: Teste que falha**

```python
async def test_collect_usa_cota_e_rodizio_do_pool() -> None:
    deps = _collect_deps_com_pool(
        seeds=("vela artesanal", "vela de soja", "vela aromática"),
        suggest=("como fazer vela",),
        env={"SEARCHES_PER_COLLECT_YOUTUBE": "3"},
    )
    out = await collect("velas-artesanais", ["youtube"], 25, deps)
    origens = [d.origin for d in deps.discoveries.saved]
    assert "seed" in origens and "suggest_trends" in origens
    assert out.counts["searches_planned"] == 3
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`searches_planned`/`terms` inexistentes).

- [ ] **Step 3: Implementar**

```python
async def _pool_snapshot(segment_id: int, deps: CollectDeps) -> PoolSnapshot:
    return PoolSnapshot(
        seeds=tuple(t.term for t in await deps.terms.top_by_origin(segment_id, "seed", 50)),
        suggest=tuple(t.term for t in await deps.terms.top_by_origin(segment_id, "suggest", 50)),
        trends=tuple(t.term for t in await deps.terms.top_by_origin(segment_id, "trends", 50)),
        hashtags=tuple(
            t.term for t in await deps.terms.top_by_origin(segment_id, "hashtag:youtube", 50)
        ),
    )
```

Em `collect()`, antes da fase de rede:

```python
run_index = run_index_for(deps.clock())
pool = await _pool_snapshot(segment.id, deps)
queries_by_platform = {
    c.platform: plan_queries(
        pool,
        c.platform,
        run_index,
        searches_per_collect(c.platform, deps.env),
    )
    for c in wanted
}
counts["searches_planned"] = sum(len(q) for q in queries_by_platform.values())
```

`_discover_one` recebe `queries` (não `segment`): `found = await collector.search_terms(queries, limit)` e devolve `DiscoverResult` com `discoveries`.

Em `composition.py`: `CollectDeps(..., terms=SegmentTermRepositoryImpl(session), discoveries=DiscoveryRepositoryImpl(session))`.

> `PoolSnapshot` fixa `hashtag:youtube` — no Bloco 6 o `platform` passa a compor a chave; para YouTube/Instagram/TikTok o snapshot usa `f"hashtag:{platform}"`. Ajuste `_pool_snapshot` para receber `platform`.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_collect.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/collect.py src/content_factory/infra/queue/composition.py tests/app/test_collect.py
git commit -m "feat(collect): cota e rodízio por origem a partir do pool (R5)"
```

---

### Task 6: Research chama o Trends nos top-N do pool

**Files:**
- Modify: `src/content_factory/app/research_pipeline.py`
- Modify: `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_research_pipeline.py` (editar)

**Interfaces:**
- Consumes: `plan_trend_batches`, `relative_interest`, `trend_score`, `TermTrendDay`, `SegmentTermRepository.set_trend_scores`, `TermTrendRepository.record_many`.
- Produces:
  - `ResearchDeps.terms: SegmentTermRepository`, `ResearchDeps.trend_days: TermTrendRepository`.
  - Counts novos: `trend_batches`, `trend_terms_scored`.
  - Env `TRENDS_TERMS_PER_DAY` (16).

- [ ] **Step 1: Teste que falha**

```python
async def test_research_pontua_trends_por_ancora_do_pool() -> None:
    deps = _research_deps_com_pool(
        suggest=("a", "b", "c", "d", "e"), trends_points=_fake_trends(...)
    )
    out = await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    assert out.counts["trend_batches"] >= 1
    assert deps.terms.trend_scores  # set_trend_scores recebeu termos
    assert deps.trend_days.saved  # term_trend_days gravado
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

Substituir o bloco `trend_keywords` (5 primeiras frases) por:

```python
anchor = next((k for k in segment.keywords if deps.guard.admit(k, 0)), segment.keywords[0])
per_day = int(os.getenv("TRENDS_TERMS_PER_DAY", "16"))
pool = await deps.terms.top_by_origin(segment.id, "suggest", 200)
ranked = [t.term for t in sorted(pool, key=lambda t: t.kw_rank or 0.0, reverse=True)]
batches = plan_trend_batches(ranked, anchor, max_terms=per_day)
counts["trend_batches"] = len(batches)


async def _trends_all() -> list[TrendPoint]:
    points: list[TrendPoint] = []
    for batch in batches:
        await deps.limiter.acquire(TRENDS_SOURCE)  # pausa entre lotes
        points += await deps.trends.interest_over_time(
            [batch.anchor, *batch.terms], TRENDS_TIMEFRAME
        )
    return points


trend_points, trends_failed = await _run_source(TRENDS_SOURCE, segment, deps, _trends_all)
```

Depois de gravar `trend_observations`, calcular e gravar o score:

```python
by_keyword: dict[str, list[TrendPoint]] = {}
for p in trend_points:
    by_keyword.setdefault(p.keyword, []).append(p)
anchor_points = by_keyword.get(anchor, [])
today = deps.clock().date()
scores: dict[str, float] = {}
rows: list[TermTrendDay] = []
for batch in batches:
    for term in batch.terms:
        mean, slope = relative_interest(by_keyword.get(term, []), anchor_points)
        score = trend_score(mean, slope)
        scores[term] = score
        rows.append(
            TermTrendDay(
                term=term,
                day=today,
                trend_score=score,
                interest_mean=mean,
                slope=slope,
                anchor=anchor,
            )
        )
if rows:
    await deps.trend_days.record_many(segment.id, rows)
    await deps.terms.set_trend_scores(segment.id, scores)
counts["trend_terms_scored"] = len(rows)
```

E adicionar os termos de Trends ao pool como `origin="trends"` (Bloco 1 já previa), reutilizando `filter_terms` para descartar fora do nicho.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_research_pipeline.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/research_pipeline.py src/content_factory/infra/queue/composition.py \
        tests/app/test_research_pipeline.py
git commit -m "feat(research): Trends mede top-N do pool com âncora e grava trend_score (R3)"
```

---

### Task 7: Encerrar o bloco — gates e aceite

- [ ] **Step 1: Gates** — `make gates` verde.

- [ ] **Step 2: Aceite R3**

```sql
SELECT count(*) FROM segment_terms st
WHERE st.trend_score IS NOT NULL AND st.last_seen_at::date = now()::date;
-- esperado: >= 12 por segmento
SELECT count(DISTINCT term) FROM term_trend_days WHERE day = now()::date AND anchor <> '';
```

- [ ] **Step 3: Aceite R5** — QA confere nos `collect_runs` a cota usada por coleta; teste do plano cobre determinismo (mesmo `run_index` ⇒ mesmas queries), divisão por origem e fallback de hashtag vazia.

- [ ] **Step 4: Commit de encerramento** — `git commit -am "docs(plans): bloco 4 concluído — R5/R3 verificados" || true`

---

## Auto-revisão da spec (R5/R3)

- **R5** ✓ `domain/search_plan.py` (Task 1): cota fixa por origem, rodízio determinístico sem estado (4 coletas/dia), fallback quando a origem está vazia, `SEARCHES_PER_COLLECT_<PLATAFORMA>` por env, `COLLECT_LIMIT=25` mantido, idioma/região do YouTube como hoje. Web fora do plano.
- **R3** ✓ top-N do pool pós-filtro por `kw_rank` (Task 6), lotes com âncora fixa (Task 3), `trend_score` = média relativa 7d + inclinação, gravado por termo/dia (Tasks 2/4/6), breaker/limitador do Trends inalterados e falha não derruba o research.
- Fora deste bloco: `hashtag:<plataforma>` no pool vem do R11 (Bloco 5); a divisão por origem só tem todos os grupos preenchidos depois dele.
