# Bloco 4 — Plano de consulta por vagas, Trends aplicado e pré-registro (R5+R3+§6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** As buscas passam a sair do **pool** (Bloco 1), com **uma vaga fixa por origem**, pré-registrada. O Trends passa a medir os **melhores termos** do pool, sempre em relação a uma âncora comum. Este bloco fecha a **Fase A** (coleta): ao terminar, o pré-registro é commitado e é feito o deploy que abre o teste de 4 dias.

**Architecture:**
- Duas peças puras em `domain/`: `search_plan.py` (vagas + rodízio determinístico sem estado) e `trends_rank.py` (lotes com âncora, razão diária, inclinação).
- `collect()` troca `provisional_queries` (Bloco 2) por `plan_queries` sobre o pool.
- O `research_pipeline` troca "os 5 de maior peso" por lotes do top-N do pool. O provider do Trends passa a gravar a granularidade real da série.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy 2 async, Alembic, pytrends, pytest (asyncio), ruff, pyright strict.

## Global Constraints

- `domain/` puro; `app/` → portas; `infra/` implementa portas; `cli/worker → app → domain`.
- `pyright` strict 0 erros; `ruff` linha 100/aspas duplas; `ruff format` limpo; TDD; sem `assert` em produção (B101).
- Envelope só com contagens; sem segredo nem caminho absoluto de home rastreado.
- `make gates` antes de "pronto" (inclui `alembic check`).
- Migração aditiva: `0014` segue `0013` (Bloco 3). *(Bloco 5 usa `0015`.)*
- Não tocar `video_scores`/`viral_score`/`FORMULA_VERSION`. A web segue o próprio protocolo `q1`, fora deste plano.

## Referência

Especificação §4 R5 e R3; §5 (cota); §6 (pré-registro e divisão fixa por origem). **Depende dos Blocos 1** (pool) **e 2** (`TermSearcher`, `search_queries`).

## Decisões deste bloco (revisão QA)

- **Uma vaga por origem.** A 1ª versão juntava `suggest` e `trends` numa vaga `suggest_trends`, e isso impedia a comparação seed × suggest × trends × hashtag que o pré-registro pede. Mas Trends **não traz termo novo**: ele reordena o pool do Suggest. Por isso:

  | Vaga (YouTube, 4 buscas por coleta e segmento) | Como escolhe o termo | `origin` gravada |
  |---|---|---|
  | `seed` | rodízio das keywords do segmento | `seed` |
  | `suggest` | pool `suggest` ativo por `kw_rank` desc, em rodízio | `suggest` |
  | `trends` | o mesmo pool, só termos com `trend_score`, por `trend_score` desc, em rodízio, **sem repetir** o termo da vaga `suggest` | `trends` |
  | `hashtag` | pool `hashtag:youtube` ativo por `engagement_score` desc, buscada como `#tag` | `hashtag:youtube` |

- **Vaga vazia vai para `suggest`** e é gravada como `suggest`: a origem é sempre quem **escolheu** o termo, nunca o nome da vaga. Até o Bloco 5 entrar em produção, a vaga de hashtag cai em `suggest`; isso fica pré-registrado.
- **No pool, a origem continua `suggest`** e o `trend_score` é um atributo (chave `(segment_id, term, origin)` do Bloco 1). Não se cria origem `trends` no pool.
- **Cota:** 4 buscas × (100 + 2) × 4 coletas × 3 segmentos ≈ 4.900 unidades/dia, mais remedição (~120) e research sem fase social (~30): cerca de **5.100 de 10.000**, abaixo do teto de 8.000 da §5. O valor sai de `SEARCHES_PER_COLLECT_YOUTUBE=4`.
- **Rodízio determinístico e sem estado** por `(dia UTC, slot)`: `run_index_for(now, 4)`. As coletas 02, 08, 14 e 20 BRT (05, 11, 17 e 23 UTC) caem em 4 slots distintos. Catch-up ou execução manual no mesmo slot repete as mesmas buscas, o que é reprodutível.
- **Trends diário:** `TRENDS_TIMEFRAME = "today 3-m"` (pontos diários). Com a série de 12 meses (pontos semanais), "média dos últimos 7 dias" e "inclinação" eram, na prática, 1 ponto e 1 ano. O provider passa a gravar a **granularidade real**, deduzida do passo entre os pontos, em vez do `"week"` fixo.
- **Âncora:** `SegmentConfig.trends_anchor` (opcional no YAML) ou `segment.keywords[0]`. Não usar `guard.admit` para escolher a âncora: ele muda estado e já viu as seeds.
- **Razão por dia, alinhada por `period_start`**. Dia em que a âncora vale 0 é descartado (não vira 0). A razão tem teto `RATIO_CAP=5`. Âncora sem nenhum dia válido devolve `None`, nunca 0. O `trend_score` fica em [0, 1].
- **Falha isolada por lote:** um lote que falha não descarta os outros. Se todos falharem, a run `research:trends` fica `failed`, como hoje.
- **`trend_observations`:** a âncora aparece em todos os lotes, cada um normalizado à sua maneira. Antes de gravar, deduplicar os pontos por `(keyword, period_start)`, ficando com o primeiro lote. Um único `INSERT ... ON CONFLICT DO UPDATE` com chave repetida falha no Postgres. O valor gravado é relativo ao lote; o normalizado vai para `term_trend_days`.
- Envs: `TRENDS_TERMS_PER_DAY=16`, `SEARCHES_PER_COLLECT_YOUTUBE=4`.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/search_plan.py` (novo) | Vagas por origem + rodízio + `plan_queries`. |
| `src/content_factory/domain/trends_rank.py` (novo) | Lotes com âncora, razão diária, `trend_score`. |
| `src/content_factory/domain/models.py` (editar) | `TermTrendDay`; `SegmentConfig.trends_anchor`. |
| `src/content_factory/app/ports.py` (editar) | `SegmentTermRepository.set_trend_scores`; `TermTrendRepository`. |
| `src/content_factory/infra/db/orm.py` (editar) | `TermTrendDayRow`. |
| `alembic/versions/0014_term_trend_days.py` (novo) | Tabela `term_trend_days`. |
| `src/content_factory/infra/db/repositories.py` (editar) | `TermTrendRepositoryImpl`; `set_trend_scores`. |
| `src/content_factory/infra/collectors/trends_google.py` (editar) | Granularidade real da série. |
| `src/content_factory/app/collect.py` (editar) | `plan_queries` sobre o pool (substitui `provisional_queries`). |
| `src/content_factory/app/research_pipeline.py` (editar) | Lotes de Trends do top-N do pool, falha por lote. |
| `src/content_factory/infra/queue/composition.py` (editar) | `CollectDeps.terms`, `ResearchDeps.trend_days`. |
| `docs/manual/pre-registro-viral-por-tema.md` (novo) | Pré-registro (§6), **antes** do deploy da Fase A. |
| `tests/...` | TDD por arquivo. |

---

### Task 1: Plano de consulta puro (`search-plan-v1`)

**Files:**
- Create: `src/content_factory/domain/search_plan.py`
- Test: `tests/domain/test_search_plan.py`

**Interfaces:**
- Produces:
  - `SEARCH_PLAN_VERSION = "search-plan-v1"`; `SLOT_ORDER = ("seed", "suggest", "trends", "hashtag")`; `DEFAULT_SEARCHES_PER_COLLECT = {"youtube": 4, "instagram": 1, "tiktok": 1}`
  - `@dataclass(frozen=True) class PoolSnapshot: seeds; suggest_by_rank; suggest_by_trend; hashtags` (todas `tuple[str, ...] = ()`)
  - `def run_index_for(now: datetime, collections_per_day: int = 4) -> int`
  - `def searches_per_collect(platform: str, env: Mapping[str, str]) -> int`
  - `def plan_queries(pool: PoolSnapshot, platform: str, run_index: int, searches: int) -> list[SearchQuery]`

> O código abaixo foi executado contra estes testes (pytest, pyright strict e ruff limpos) na revisão QA.

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
    seeds=("a", "b", "c"),
    suggest_by_rank=("s1", "s2", "s3"),
    suggest_by_trend=("s2", "s9"),
    hashtags=("velasdecor",),
)


def test_uma_vaga_por_origem_e_hashtag_com_cerquilha() -> None:
    qs = plan_queries(POOL, "youtube", run_index=0, searches=4)
    assert [(q.origin, q.term) for q in qs] == [
        ("seed", "a"),
        ("suggest", "s1"),
        ("trends", "s2"),
        ("hashtag:youtube", "#velasdecor"),
    ]
    assert len({q.query_id for q in qs}) == 4
    assert all(q.query_id.startswith(f"{SEARCH_PLAN_VERSION}:0:") for q in qs)


def test_vaga_vazia_vai_para_suggest_e_e_gravada_como_suggest() -> None:
    pool = PoolSnapshot(seeds=("a",), suggest_by_rank=("s1", "s2", "s3"))
    qs = plan_queries(pool, "youtube", run_index=0, searches=4)
    assert [q.origin for q in qs] == ["seed", "suggest", "suggest", "suggest"]
    assert len({q.term for q in qs}) == 4


def test_trends_nunca_repete_o_termo_da_vaga_suggest() -> None:
    pool = PoolSnapshot(seeds=("a",), suggest_by_rank=("s1",), suggest_by_trend=("s1", "t2"))
    qs = plan_queries(pool, "youtube", run_index=0, searches=3)
    assert [(q.origin, q.term) for q in qs] == [("seed", "a"), ("suggest", "s1"), ("trends", "t2")]


def test_deterministico_e_rodizio_varia_com_o_indice() -> None:
    assert plan_queries(POOL, "youtube", 5, 4) == plan_queries(POOL, "youtube", 5, 4)
    seed_of = [plan_queries(POOL, "youtube", i, 1)[0].term for i in range(4)]
    assert seed_of == ["a", "b", "c", "a"]


def test_pool_vazio_nao_gera_busca() -> None:
    assert plan_queries(PoolSnapshot(), "youtube", 0, 4) == []


def test_4_coletas_por_dia_caem_em_4_slots() -> None:
    idx = [run_index_for(datetime(2026, 10, 20, h, tzinfo=UTC)) for h in (5, 11, 17, 23)]
    assert len(set(idx)) == 4
    assert run_index_for(datetime(2026, 10, 21, 5, tzinfo=UTC)) - idx[0] == 4


def test_cota_por_env_com_default_youtube_4() -> None:
    assert searches_per_collect("youtube", {}) == 4
    assert searches_per_collect("youtube", {"SEARCHES_PER_COLLECT_YOUTUBE": "3"}) == 3
    assert searches_per_collect("youtube", {"SEARCHES_PER_COLLECT_YOUTUBE": "x"}) == 4
    assert searches_per_collect("instagram", {}) == 1
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/domain/test_search_plan.py -q` → FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/search_plan.py
"""Plano de consulta por coleta (R5) — puro, determinístico e sem estado.

Uma VAGA por origem (braço do experimento), fixa e pré-registrada:
`seed` → `suggest` → `trends` → `hashtag:<plataforma>`. A origem gravada na
descoberta é a vaga que ESCOLHEU o termo:

- `suggest`: pool `suggest` ordenado por `kw_rank`;
- `trends`: o mesmo pool ordenado por `trend_score` (Trends não traz termo
  novo, só reordena) — nunca repete o termo já escolhido pela vaga `suggest`;
- `hashtag:<plataforma>`: hashtags mineradas (R11), buscadas como `#tag`.

Vaga sem candidato vai para `suggest` (próximo termo) e é gravada como
`suggest` — a origem reflete quem escolheu, nunca a vaga nominal. Sem
realocação adaptativa durante o teste.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from content_factory.domain.models import SearchQuery

SEARCH_PLAN_VERSION = "search-plan-v1"
SLOT_ORDER: tuple[str, ...] = ("seed", "suggest", "trends", "hashtag")
DEFAULT_SEARCHES_PER_COLLECT: dict[str, int] = {"youtube": 4, "instagram": 1, "tiktok": 1}


@dataclass(frozen=True)
class PoolSnapshot:
    seeds: tuple[str, ...] = ()
    suggest_by_rank: tuple[str, ...] = ()  # pool suggest ativo, kw_rank desc
    suggest_by_trend: tuple[str, ...] = ()  # só termos com trend_score, desc
    hashtags: tuple[str, ...] = ()  # hashtag:<plataforma> ativo, engagement desc


def run_index_for(now: datetime, collections_per_day: int = 4) -> int:
    """Índice sem estado: nº de slots desde a data ordinal (UTC)."""
    slot = min(now.hour * collections_per_day // 24, collections_per_day - 1)
    return now.date().toordinal() * collections_per_day + slot


def searches_per_collect(platform: str, env: Mapping[str, str]) -> int:
    raw = env.get(f"SEARCHES_PER_COLLECT_{platform.upper()}")
    if raw is not None and raw.strip().isdigit() and int(raw) >= 1:
        return int(raw)
    return DEFAULT_SEARCHES_PER_COLLECT.get(platform, 1)


def _rotate(items: tuple[str, ...], index: int, taken: set[str]) -> str | None:
    """Primeiro item a partir de `index % len` que ainda não foi usado."""
    n = len(items)
    for k in range(n):
        item = items[(index + k) % n]
        if item not in taken:
            return item
    return None


def plan_queries(
    pool: PoolSnapshot, platform: str, run_index: int, searches: int
) -> list[SearchQuery]:
    taken: set[str] = set()
    out: list[SearchQuery] = []

    def add(origin: str, term: str, query_term: str) -> None:
        taken.add(term)
        out.append(
            SearchQuery(
                term=query_term,
                origin=origin,
                query_id=f"{SEARCH_PLAN_VERSION}:{run_index}:{len(out)}:{origin}",
            )
        )

    for slot in SLOT_ORDER[: max(searches, 0)]:
        if slot == "seed":
            term = _rotate(pool.seeds, run_index, taken)
            if term is not None:
                add("seed", term, term)
            continue
        if slot == "trends":
            term = _rotate(pool.suggest_by_trend, run_index, taken)
            if term is not None:
                add("trends", term, term)
                continue
        if slot == "hashtag":
            tag = _rotate(pool.hashtags, run_index, taken)
            if tag is not None:
                add(f"hashtag:{platform}", tag, f"#{tag}" if platform == "youtube" else tag)
                continue
        # vaga `suggest` ou fallback de vaga vazia
        term = _rotate(pool.suggest_by_rank, run_index, taken)
        if term is not None:
            add("suggest", term, term)
    # vagas extra além das 4 (env) vão para `suggest`
    for _ in range(max(searches - len(SLOT_ORDER), 0)):
        term = _rotate(pool.suggest_by_rank, run_index, taken)
        if term is None:
            break
        add("suggest", term, term)
    return out
```

- [ ] **Step 4: Rodar e ver passar** — PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/search_plan.py tests/domain/test_search_plan.py
git commit -m "feat(domain): search-plan-v1 — uma vaga por origem e rodízio sem estado (R5)"
```

---

### Task 2: Tabela `term_trend_days` (migração `0014`) e `trends_anchor`

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`, `src/content_factory/domain/models.py`
- Create: `alembic/versions/0014_term_trend_days.py`
- Test: `tests/infra/test_orm.py`, `tests/domain/test_models.py` (editar)

**Interfaces:**
- Produces: `TermTrendDayRow` (`segment_id`, `term`, `day`, `trend_score`, `interest_mean`, `slope`, `anchor`, `rules_version`); `class TermTrendDay(BaseModel)` com os mesmos campos (sem `segment_id`); `SegmentConfig.trends_anchor: str | None = None`.

- [ ] **Step 1: Testes que falham** — colunas da tabela; `SegmentConfig(...).trends_anchor is None` por padrão.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar ORM + migração**

```python
class TermTrendDayRow(Base):
    """R3: `trend_score` por termo e dia, já normalizado pela âncora.

    O valor corrente fica em `segment_terms.trend_score`; aqui fica o
    histórico (um por dia — refazer o research no mesmo dia atualiza).
    """

    __tablename__ = "term_trend_days"
    __table_args__ = (UniqueConstraint("segment_id", "term", "day", name="uq_term_trend_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    term: Mapped[str] = mapped_column(String(200))
    day: Mapped[date] = mapped_column(Date)
    trend_score: Mapped[float | None] = mapped_column(Float)  # None = sem âncora comparável
    interest_mean: Mapped[float | None] = mapped_column(Float)
    slope: Mapped[float | None] = mapped_column(Float)
    anchor: Mapped[str] = mapped_column(String(200))
    rules_version: Mapped[str] = mapped_column(String(32))
```

Migração `0014` (revision `0014`, down `0013`) criando a tabela e a unique.

- [ ] **Step 4: Rodar e ver passar + `uv run alembic check`** → PASS, sem drift.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/orm.py src/content_factory/domain/models.py \
        alembic/versions/0014_term_trend_days.py tests/infra/test_orm.py tests/domain/test_models.py
git commit -m "feat(db): migração 0014 — term_trend_days e trends_anchor (R3)"
```

---

### Task 3: Ranking de Trends puro (`trends-anchor-v1`)

**Files:**
- Create: `src/content_factory/domain/trends_rank.py`
- Test: `tests/domain/test_trends_rank.py`

**Interfaces:**
- Produces: `TRENDS_RANK_VERSION`, `RATIO_CAP`, `Batch`, `plan_trend_batches(terms, anchor, per_batch=4, max_terms=16)`, `relative_interest(points, anchor_points) -> tuple[float | None, float | None]`, `trend_score(mean, slope) -> float | None`.

> Na 1ª versão do plano, o teste `test_ancora_zero_devolve_none` falhava contra o próprio código, que devolvia `(0.0, 0.0)`; além disso a razão não tinha teto e o alinhamento era por índice. O código abaixo foi executado contra estes testes.

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_trends_rank.py
from datetime import UTC, datetime, timedelta

from content_factory.domain.models import TrendPoint
from content_factory.domain.trends_rank import (
    RATIO_CAP,
    TRENDS_RANK_VERSION,
    plan_trend_batches,
    relative_interest,
    trend_score,
)

DAY = datetime(2026, 10, 20, tzinfo=UTC)


def _daily(kw: str, values: list[float]) -> list[TrendPoint]:
    """Série diária terminando em DAY (último valor = DAY)."""
    n = len(values)
    return [
        TrendPoint(
            keyword=kw,
            period_start=DAY - timedelta(days=n - 1 - i),
            period_granularity="day",
            interest=v,
        )
        for i, v in enumerate(values)
    ]


def test_version() -> None:
    assert TRENDS_RANK_VERSION == "trends-anchor-v1"


def test_lotes_de_4_com_ancora_fixa_e_sem_a_propria_ancora() -> None:
    batches = plan_trend_batches([f"t{i}" for i in range(18)] + ["Vela"], "vela")
    assert len(batches) == 4 and all(len(b.terms) == 4 for b in batches)
    assert all(b.anchor == "vela" for b in batches)


def test_razao_estavel_sem_inclinacao() -> None:
    assert relative_interest(_daily("x", [50] * 28), _daily("a", [100] * 28)) == (0.5, 0.0)


def test_ancora_zero_devolve_none() -> None:
    assert relative_interest(_daily("x", [10, 20]), _daily("a", [0, 0])) == (None, None)
    assert trend_score(None, None) is None


def test_inclinacao_7d_contra_21d_anteriores_limitada() -> None:
    mean, slope = relative_interest(_daily("x", [10] * 21 + [40] * 7), _daily("a", [100] * 28))
    assert mean == 0.4 and slope == 1.0  # quadruplicou → teto 1


def test_razao_tem_teto() -> None:
    mean, _ = relative_interest(_daily("x", [100] * 7), _daily("a", [1] * 7))
    assert mean == RATIO_CAP


def test_alinha_por_data_nao_por_indice() -> None:
    # a âncora não tem o 1º dia: só os 2 dias em comum entram
    mean, _ = relative_interest(_daily("x", [50, 50, 50]), _daily("a", [100, 100]))
    assert mean == 0.5


def test_trend_score_no_intervalo_unitario_e_monotono() -> None:
    assert 0.0 <= (trend_score(RATIO_CAP, 1.0) or 0.0) <= 1.0
    assert (trend_score(0.5, 1.0) or 0.0) > (trend_score(0.5, 0.0) or 0.0)
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/trends_rank.py
"""Trends aplicado ao pool (R3) — puro e versionado.

O Trends normaliza DENTRO de cada consulta: lotes só se comparam com uma
âncora comum (mesma em todos os lotes). Série DIÁRIA (`today 3-m`):

- razão termo/âncora por DIA (alinhada por `period_start`; dia com âncora 0
  é descartado, não vira 0);
- `interest_mean` = média das razões nos últimos 7 dias (com teto
  `RATIO_CAP`, para um termo 100x maior não dominar);
- `slope` = (média 7 d − média dos 21 d anteriores) / média dos 21 d,
  limitada a [−1, 1];
- `trend_score` = 0,7 × mean/RATIO_CAP + 0,3 × (slope + 1)/2 ∈ [0, 1].
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from content_factory.domain.models import TrendPoint

TRENDS_RANK_VERSION = "trends-anchor-v1"
W_INTEREST = 0.7
W_SLOPE = 0.3
PER_BATCH = 4  # + 1 âncora = 5 (limite do Trends por consulta)
RATIO_CAP = 5.0
RECENT_DAYS = 7
BASE_DAYS = 21


@dataclass(frozen=True)
class Batch:
    anchor: str
    terms: tuple[str, ...]


def plan_trend_batches(
    terms: Sequence[str], anchor: str, per_batch: int = PER_BATCH, max_terms: int = 16
) -> list[Batch]:
    selected = [t for t in dict.fromkeys(terms) if t.casefold() != anchor.casefold()][:max_terms]
    return [
        Batch(anchor=anchor, terms=tuple(selected[i : i + per_batch]))
        for i in range(0, len(selected), per_batch)
    ]


def _daily_ratios(
    points: Sequence[TrendPoint], anchor_points: Sequence[TrendPoint]
) -> list[tuple[datetime, float]]:
    anchor = {p.period_start: p.interest for p in anchor_points}
    out: list[tuple[datetime, float]] = []
    for p in sorted(points, key=lambda q: q.period_start):
        base = anchor.get(p.period_start)
        if base is not None and base > 0:
            out.append((p.period_start, min(p.interest / base, RATIO_CAP)))
    return out


def relative_interest(
    points: Sequence[TrendPoint], anchor_points: Sequence[TrendPoint]
) -> tuple[float | None, float | None]:
    """(média 7 d da razão, inclinação 7 d × 21 d anteriores) — `None` sem âncora."""
    ratios = [r for _, r in _daily_ratios(points, anchor_points)]
    if not ratios:
        return None, None
    recent = ratios[-RECENT_DAYS:]
    base = ratios[-(RECENT_DAYS + BASE_DAYS) : -RECENT_DAYS]
    mean = sum(recent) / len(recent)
    slope: float | None = None
    if base:
        base_mean = sum(base) / len(base)
        slope = (
            0.0
            if base_mean == 0 and mean == 0
            else (1.0 if base_mean == 0 else max(-1.0, min(1.0, (mean - base_mean) / base_mean)))
        )
    return round(mean, 6), (round(slope, 6) if slope is not None else None)


def trend_score(interest_mean: float | None, slope: float | None) -> float | None:
    """[0, 1]; `None` quando não há âncora comparável (não é zero)."""
    if interest_mean is None:
        return None
    s = 0.0 if slope is None else slope
    return round(W_INTEREST * interest_mean / RATIO_CAP + W_SLOPE * (s + 1) / 2, 6)
```

- [ ] **Step 4: Rodar e ver passar** — PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/trends_rank.py tests/domain/test_trends_rank.py
git commit -m "feat(domain): trends-anchor-v1 — razão diária pela âncora, teto e inclinação (R3)"
```

---

### Task 4: Provider do Trends grava a granularidade real

**Files:**
- Modify: `src/content_factory/infra/collectors/trends_google.py`
- Test: `tests/infra/collectors/test_trends_google.py` (editar)

**Interfaces:**
- Produces: `_map` deduz `period_granularity` do passo entre os índices do DataFrame: `"hour"` (< 1 dia), `"day"` (1 dia), `"week"` (7 dias), `"month"` (outros). Com um único ponto, usa `"week"`, o comportamento atual.

- [ ] **Step 1: Teste que falha** — DataFrame diário (`pd.date_range(..., freq="D")`) → todos os pontos com `period_granularity == "day"`; o teste semanal existente continua `"week"`.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — helper puro `_granularity(index) -> str` no módulo, chamado uma vez por DataFrame.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/collectors/trends_google.py tests/infra/collectors/test_trends_google.py
git commit -m "fix(trends): granularidade real da série em vez de 'week' fixo (R3)"
```

---

### Task 5: Repositórios do trend por termo/dia

**Files:**
- Modify: `src/content_factory/app/ports.py`, `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Produces:
  - `SegmentTermRepository.set_trend_scores(segment_id: int, scores: Mapping[str, float | None]) -> int`: atualiza `trend_score` **só** das linhas `origin='suggest'`, sem mexer em `last_seen_at` (que significa "visto no Suggest").
  - `TermTrendRepository.record_many(segment_id: int, rows: Sequence[TermTrendDay]) -> int`: upsert por `(segment_id, term, day)`.

- [ ] **Step 1: Teste que falha** — integração: `set_trend_scores` grava nos termos `suggest` e não cria linha nova para termo ausente do pool; `record_many` duas vezes no mesmo dia não duplica.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — `UPDATE segment_terms SET trend_score = :v WHERE segment_id = :s AND origin = 'suggest' AND term = :t`, e `pg_insert(...).on_conflict_do_update(index_elements=["segment_id", "term", "day"], set_={...excluded...})`.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ports.py src/content_factory/infra/db/repositories.py \
        tests/infra/test_repositories.py
git commit -m "feat(db): trend_score no pool e term_trend_days (R3)"
```

---

### Task 6: Coleta usa o plano do pool (R5)

**Files:**
- Modify: `src/content_factory/app/collect.py`, `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_collect.py` (editar)

**Interfaces:**
- Consumes: `plan_queries`, `PoolSnapshot`, `run_index_for`, `searches_per_collect`, `SegmentTermRepository.top_by_origin(..., order_by=...)`.
- Produces:
  - `CollectDeps.terms: SegmentTermRepository` (default NoOp) e `CollectDeps.env: Mapping[str, str]` (default `os.environ`, injetável em teste).
  - `provisional_queries` **removido**.
  - Counts: `searches_planned` (soma das plataformas com `TermSearcher`).

- [ ] **Step 1: Testes que falham**

```python
async def test_collect_usa_as_vagas_do_pool() -> None:
    deps = collect_deps(
        pool={"seed": ["vela artesanal", "vela de soja"], "suggest": ["como fazer vela"]},
        trend_scores={"como fazer vela": 0.4},
        env={"SEARCHES_PER_COLLECT_YOUTUBE": "4"},
        term_searchers={"youtube": FakeTermSearcher({})},
    )
    out = await collect("velas-artesanais", ["youtube"], 25, deps)
    # 1 só termo suggest: a vaga trends não pode repeti-lo e cai para suggest,
    # que também não tem outro; hashtag vazia idem → 2 buscas (seed + suggest)
    assert [r.origin for r in deps.search_log.saved] == ["seed", "suggest"]
    assert out.counts["searches_planned"] == 2


async def test_pool_sem_seed_ainda_usa_as_keywords_do_segmento() -> None:
    # 1ª coleta antes do 1º research com pool: as seeds vêm do segmento
    deps = collect_deps(pool={}, term_searchers={"youtube": FakeTermSearcher({})})
    await collect("velas-artesanais", ["youtube"], 25, deps)
    assert deps.search_log.saved and deps.search_log.saved[0].origin == "seed"
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
async def _pool_snapshot(segment: Segment, platform: str, deps: CollectDeps) -> PoolSnapshot:
    terms = deps.terms
    seeds = [t.term for t in await terms.top_by_origin(segment.id, "seed", 200)]
    by_trend = await terms.top_by_origin(segment.id, "suggest", 50, order_by="trend_score")
    return PoolSnapshot(
        seeds=tuple(seeds or segment.keywords),  # pool vazio: keywords do segmento
        suggest_by_rank=tuple(t.term for t in await terms.top_by_origin(segment.id, "suggest", 50)),
        suggest_by_trend=tuple(t.term for t in by_trend if t.trend_score is not None),
        hashtags=tuple(
            t.term
            for t in await terms.top_by_origin(
                segment.id, f"hashtag:{platform}", 50, order_by="engagement_score"
            )
        ),
    )
```

As seeds do pool vêm na ordem `term` (o `kw_rank` delas é `NULL`). A ordem do rodízio das seeds fica **alfabética e estável**; isso vai para o pré-registro. Antes da fase de rede: `run_index = run_index_for(deps.clock())`; para cada plataforma com `TermSearcher`, `queries = plan_queries(await _pool_snapshot(...), platform, run_index, searches_per_collect(platform, deps.env))`.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_collect.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/collect.py src/content_factory/infra/queue/composition.py tests/app/test_collect.py
git commit -m "feat(collect): uma vaga por origem a partir do pool (R5)"
```

---

### Task 7: Research mede o top-N do pool em lotes com âncora (R3)

**Files:**
- Modify: `src/content_factory/app/research_pipeline.py`, `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_research_pipeline.py` (editar)

**Interfaces:**
- Consumes: `plan_trend_batches`, `relative_interest`, `trend_score`, `TermTrendDay`, `set_trend_scores`, `TermTrendRepository.record_many`.
- Produces:
  - `TRENDS_TIMEFRAME = "today 3-m"`; `ResearchDeps.trend_days: TermTrendRepository` (default NoOp); `ResearchDeps.trends_terms_per_day: int = 16` (o worker lê `TRENDS_TERMS_PER_DAY`).
  - Counts: `trend_batches`, `trend_batches_failed`, `trend_terms_scored` (termos com `trend_score` não nulo).

- [ ] **Step 1: Testes que falham**

```python
async def test_research_pontua_top_n_do_pool_por_ancora() -> None:
    deps = research_deps(
        suggest_hits=_hits(["vela a", "vela b", "vela c", "vela d", "vela e"]),
        trends=FakeTrends(per_keyword_daily=_flat(28)),
    )
    out = await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    assert out.counts["trend_batches"] == 2  # 5 termos → [4, 1]
    assert all(call[0] == deps.segment.keywords[0] for call in deps.trends.calls)  # âncora 1º
    assert deps.trend_days.saved and out.counts["trend_terms_scored"] == 5


async def test_lote_que_falha_nao_descarta_os_outros() -> None:
    deps = research_deps(
        suggest_hits=_hits([f"vela {i}" for i in range(8)]),
        trends=FakeTrends(per_keyword_daily=_flat(28), fail_on_call=2),
    )
    out = await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    assert out.counts["trend_batches_failed"] == 1
    assert out.counts["trend_terms_scored"] == 4
    assert out.counts["trends_failed"] == 0  # só vira failed se TODOS os lotes falharem


async def test_ancora_repetida_nos_lotes_e_gravada_uma_vez_em_trend_observations() -> None:
    deps = research_deps(
        suggest_hits=_hits([f"vela {i}" for i in range(8)]),
        trends=FakeTrends(per_keyword_daily=_flat(28)),
    )
    await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    keys = [(p.keyword, p.period_start) for p in deps.trend_observations.saved]
    assert len(keys) == len(set(keys))
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — substitui o bloco "5 de maior peso":

```python
anchor = segment.trends_anchor or segment.keywords[0]
ranked = [s.keyword for s in web_signals]  # já aceitos pelo R1 e ordenados por kw_rank
batches = plan_trend_batches(ranked, anchor, max_terms=deps.trends_terms_per_day)
counts["trend_batches"] = len(batches)

points: list[TrendPoint] = []
batch_points: list[tuple[Batch, list[TrendPoint]]] = []
failed_batches = 0
for batch in batches:

    async def _one(b: Batch = batch) -> list[TrendPoint]:
        await deps.limiter.acquire(TRENDS_SOURCE)  # pausa entre lotes
        return await deps.trends.interest_over_time([b.anchor, *b.terms], TRENDS_TIMEFRAME)

    got, failed = await _run_source(TRENDS_SOURCE, segment, deps, _one)  # 1 run por lote
    failed_batches += int(failed)
    batch_points.append((batch, got))
    points += got
counts["trend_batches_failed"] = failed_batches
trends_failed = bool(batches) and failed_batches == len(batches)
```

- O score é calculado **dentro de cada lote**: a âncora daquele lote contra os termos daquele lote. A âncora tem escala diferente em cada lote, por isso não se misturam lotes.
- `TermTrendDay` com `rules_version=TRENDS_RANK_VERSION` vai para `deps.trend_days.record_many`, e `{termo: score}` para `deps.terms.set_trend_scores`.
- `trend_observations.record_many` recebe os pontos **deduplicados por `(keyword, period_start)`**, ficando com o primeiro lote.
- `trend_signals` (para `keyword_signals`) passam a usar `weight = trend_score` (0 quando `None`), em vez de `interest/100` de um ponto solto.
- O envelope de runs muda: agora há **uma `research:trends` por lote**. Ajuste os testes do `ops-report` que contam essas runs.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_research_pipeline.py tests/app/test_research_pipeline_e2e.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/research_pipeline.py src/content_factory/infra/queue/composition.py \
        tests/app/test_research_pipeline.py
git commit -m "feat(research): Trends mede o top-N do pool em lotes com âncora, falha por lote (R3)"
```

---

### Task 8: Pré-registro (§6) — antes do deploy da Fase A

**Files:**
- Create: `docs/manual/pre-registro-viral-por-tema.md`
- Modify: `mkdocs.yml` (só se o `--strict` reclamar), `docs/HANDOFF.md`

O pré-registro tem de estar commitado **antes** de qualquer dado do teste. Por isso sai do Bloco 6 e vem para o fim da Fase A.

- [ ] **Step 1: Escrever o pré-registro** com, no mínimo:

```markdown
# Pré-registro — descoberta de conteúdo viral por tema (teste de 4 dias)

Data: <commit>. Especificação: `docs/ai-plans/2026-10-16-especificacao-viral-por-tema.md`.

## Pergunta
Os termos vindos da web (Suggest, Trends e hashtags) trazem conteúdo com mais
engajamento do que as keywords fixas do segmento? Quais temas e quais vídeos
lideram em cada segmento?

## Plataforma da comparação
YouTube (única com busca por termo). Instagram e TikTok: `unattributed`, fora.

## Versões congeladas
`term-filter-v1` (listas EN/ES, âncoras e bloqueios dos YAMLs no commit X),
`kw-rank-v1` (0,5/0,3/0,2; cap 3; 7 d), `search-plan-v1`, `trends-anchor-v1`
(`today 3-m`, razão diária, teto 5, 7 d × 21 d, 0,7/0,3), `viral-v2`
(0,35/0,25/0,25/0,15; janela 30 d; n mínimo 5 por plataforma e segmento),
`term-rank-v1` (0,4/0,3/0,3; bootstrap seed 20261016, 1.000 reamostragens),
`hashtag-mine-v1` (top 25%, top 10 por plataforma).

## Divisão fixa de buscas (R5)
YouTube, 4 buscas por coleta e segmento, 4 coletas por dia (02/08/14/20 BRT):
`seed` (rodízio alfabético das keywords), `suggest` (kw_rank), `trends`
(trend_score, sem repetir o da vaga suggest), `hashtag:youtube`. Vaga vazia
vai para `suggest` e é gravada como `suggest`. A vaga `hashtag` é
EXPLORATÓRIA: entra quando a Fase B (viral-v2 + mineração) for ao ar, em
<data>; antes disso, cai em `suggest`. Sem realocação adaptativa.

## Regra de decisão por origem (YouTube, por segmento)
Unidade: vídeo único por origem. Origem X vs `seed`: diferença das medianas
do `viral-v2` no corte, IC 95% por bootstrap (seed 20261016, 1.000).
n < 30 em X ou em `seed` ⇒ insuficiente; limite inferior > 0 ⇒ melhor;
limite superior < 0 ⇒ pior; senão ⇒ direcional. Vídeo achado pelas duas
origens conta nas duas (sobreposição reportada).

## Top temas e top vídeos
Tema: `engagement_score` com n ≥ 5 vídeos únicos. Vídeos: top 20 do
`viral-v2` por segmento (YouTube), no corte.

## Corte e checkpoints
Início: deploy da Fase A (<data/hora BRT>). Corte: início + 96 h.
D+0 fumaça; D+1 filtro (amostra cega de 13 termos/segmento), remedição,
1º `cf term-report`; D+2 velocidade não nula, hashtags no pool (se Fase B);
D+4 corte, backup, relatório final com `--as-of` = corte.

## Envs
`COLLECT_CRON_HOURS=02,08,14,20`, `SCORE_CRON_HOURS=02,08,14,20`,
`COLLECT_LIMIT=25`, `SEARCHES_PER_COLLECT_YOUTUBE=4`, `TRENDS_TERMS_PER_DAY=16`,
`RESEARCH_SOCIAL_PLATFORMS=none`, `REMEDIATION_WINDOW_DAYS=7`,
`REMEDIATION_LIMIT=500`, `REMEDIATION_MIN_GAP_HOURS=3`,
`VIRAL_V2_WINDOW_DAYS=30`, `VIRAL_V2_MIN_POPULATION=5`.
```

- [ ] **Step 2: Publicar** — `uv run --only-group docs mkdocs build --strict`; se acusar arquivo fora do `nav`, acrescentar a página ao `nav` do manual e repetir.

- [ ] **Step 3: HANDOFF** — estado (Fase A pronta) e o checklist da Task 9.

- [ ] **Step 4: Commit**

```bash
git add docs/manual/pre-registro-viral-por-tema.md mkdocs.yml docs/HANDOFF.md
git commit -m "docs(preregistro): congela versões, vagas e regra de decisão do teste de 4 dias (§6)"
```

---

### Task 9: Encerrar a Fase A — gates, aceite e deploy (QA/orquestrador)

- [ ] **Step 1: Gates** — `make gates` verde. Rodar o smoke da imagem (`scripts/smoke_worker_segments.sh`).

- [ ] **Step 2: Aceite R5 e R3 com dados reais (QA, antes de abrir as campanhas)** — um research e uma coleta manuais de 1 segmento:

```sql
SELECT origin, term, results, quota_used FROM search_queries ORDER BY id DESC LIMIT 4;
-- esperado: seed, suggest, trends (ou suggest), suggest; ~102 de cota cada
SELECT count(*) FROM segment_terms WHERE origin = 'suggest' AND trend_score IS NOT NULL;
-- esperado: >= 12 por segmento (aceite R3)
SELECT count(DISTINCT term) FROM term_trend_days WHERE day = current_date;
```

- [ ] **Step 3: Deploy da Fase A** (orquestrador; janela sem cron e com a fila vazia):
  1. Backup do banco.
  2. Encerrar as campanhas 155–157 (segmentos 856–858).
  3. Imagem nova e migrações `0011`–`0014`.
  4. `cf segment load` dos 3 segmentos (sobe `probe_terms`, `anchor_terms`, `blocked_terms` e `trends_anchor`).
  5. Envs do pré-registro.
  6. Abrir as campanhas (`cf research start --weeks 1`; a CLI só aceita semanas) e encerrá-las no D+4 com `cf research stop` — o corte do teste é o `--as-of`.
  7. Fumaça: `research`, depois `collect`, depois `score`.

- [ ] **Step 4: Commit de encerramento** — `git commit -am "docs(plans): Fase A concluída — R5/R3 verificados" || true`

---

## Auto-revisão da spec (R5/R3/§6)

- **R5** ✓ uma vaga por origem, fixa e pré-registrada (Tasks 1, 6, 8); rodízio determinístico sem estado (4 coletas/dia); fallback de vaga vazia com a origem real; `SEARCHES_PER_COLLECT_<PLATAFORMA>`; `COLLECT_LIMIT=25`; idioma e região do segmento (Bloco 2). Web fora.
- **R3** ✓ top-N do pool pós-filtro por `kw_rank` (Task 7); lotes com âncora fixa e configurável; razão diária alinhada por data, com teto; `trend_score` 7 d + inclinação gravado por termo e dia (Tasks 2-5, 7); falha isolada por lote; breaker e limitador inalterados.
- **§6** ✓ pré-registro commitado antes do deploy da Fase A (Task 8).
- Fora deste bloco: `hashtag:<plataforma>` no pool vem do R11 (Bloco 5); até lá, a vaga cai em `suggest`, o que está pré-registrado.
