# Fase A — Fundação do pipeline de pesquisa — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir a fundação do pipeline de pesquisa multi-plataforma
(web → trends → social) desenhado na sessão anterior — novos Protocols,
modelos de domínio, guard-rail anti-explosão combinatória e o use-case
orquestrador `run_research_cycle` — tudo testado com fakes/stubs, **sem
nenhum provider real de rede ainda** (Google Trends real, robustez do
Instagram por keyword, orquestração no worker ficam para as Fases B-E,
planos futuros — ver `docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-c.md`,
`-fase-d.md`, `-fase-e.md`, `-fase-b.md`). Esta fase só prepara o terreno:
o `collect()`/`score()` atuais continuam 100% intocados.

**Architecture:** Hexagonal, igual ao resto do projeto. `domain/` ganha 3
modelos puros + 1 classe pura (`FanoutGuard`); `app/ports.py` ganha 4
Protocols novos; `app/research_pipeline.py` é um novo use-case (não
substitui `collect()`); `infra/db/` ganha 1 migração Alembic + 1 ORM row +
1 repositório, seguindo exatamente o padrão de `CollectRunRepositoryImpl`/
`VideoRepositoryImpl` já existentes.

**Tech Stack:** Python 3.14, Pydantic (domain models), SQLAlchemy 2.0
async + Alembic (persistência), pytest + pytest-asyncio (TDD com fakes,
sem Docker necessário nesta fase), pyright strict.

## Global Constraints

- TDD sempre: todo passo de código começa pelo teste falhando (CLAUDE.md).
- `pyright strict` 0 erros.
- `domain/` **puro, sem I/O** — `KeywordSignal`/`TrendPoint`/`DepthConfig`/
  `FanoutGuard` não importam `httpx`/SQLAlchemy/nada de infra.
- `app/` nunca importa `infra/` diretamente (só via Protocols de `ports.py`).
- Nenhuma mudança nos Protocols/use-cases existentes (`Collector`,
  `collect()`, `score()`) — são aditivos, não modificados.
- CLI devolve só envelope `{"ok": ...}` — fora de escopo nesta fase (não
  há comando CLI novo ainda, só a camada `app`/`infra`).
- Gates antes de "pronto": pytest, ruff, pyright, alembic check, bandit,
  pip-audit, mkdocs --strict, `scripts/check_privacy.py`.
- Reaproveitar padrões já existentes: `CollectRunRepositoryImpl`/
  `VideoRepositoryImpl` em `infra/db/repositories.py` (upsert com
  `pg_insert(...).on_conflict_do_update`, savepoint por item quando
  aplicável), `CollectDeps`/`collect()` em `app/collect.py` (lock +
  acquire do `RateLimiter` por chamada externa, try/except por stage sem
  abortar as demais).

---

### Task 1: Modelos de domínio novos (`KeywordSignal`, `TrendPoint`, `DepthConfig`)

**Files:**
- Modify: `src/content_factory/domain/models.py`
- Test: `tests/domain/test_models.py` (novo arquivo — não existe ainda)

**Interfaces:**
- Produces: 3 novos modelos Pydantic + um campo opcional novo em
  `SegmentConfig`, consumidos pelas Tasks 2, 3, 4, 5.

- [ ] **Step 1: Escrever os testes falhando**

```python
from content_factory.domain.models import DepthConfig, KeywordSignal, SegmentConfig, TrendPoint
from datetime import UTC, datetime


def test_keyword_signal_defaults() -> None:
    s = KeywordSignal(keyword="vela aromatica", source="web_suggest")
    assert s.weight == 0.0
    assert s.depth == 0


def test_trend_point_fields() -> None:
    p = TrendPoint(
        keyword="vela aromatica",
        period_start=datetime(2026, 10, 1, tzinfo=UTC),
        period_granularity="week",
        interest=42.5,
    )
    assert p.period_granularity == "week"


def test_depth_config_defaults() -> None:
    cfg = DepthConfig()
    assert cfg.max_depth == 2
    assert cfg.fanout_per_stage == {}
    assert cfg.max_nodes_per_cycle == 200
    assert cfg.recursion_speed_factor == 1.0


def test_segment_config_research_is_optional() -> None:
    seg = SegmentConfig(slug="candles", name="Velas", platforms=["web"], keywords=["vela"])
    assert seg.research is None
    seg2 = SegmentConfig(
        slug="candles",
        name="Velas",
        platforms=["web"],
        keywords=["vela"],
        research=DepthConfig(max_depth=3),
    )
    assert seg2.research is not None and seg2.research.max_depth == 3
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: FAIL — `ImportError`/`AttributeError` (`DepthConfig`/`KeywordSignal`/
`TrendPoint` não existem; `SegmentConfig` não tem `research`).

- [ ] **Step 3: Implementar**

Em `src/content_factory/domain/models.py`, adicionar (perto de
`CandidateVideo`, antes de `CollectRun`):

```python
class KeywordSignal(BaseModel):
    """Descoberta de keyword/hashtag de uma etapa do pipeline de pesquisa."""

    keyword: str
    source: str  # "web_suggest" | "trends" | "instagram_hashtag" | ...
    weight: float = 0.0
    depth: int = 0


class TrendPoint(BaseModel):
    """Métrica de série temporal de uma keyword (Google Trends ou equivalente)."""

    keyword: str
    period_start: datetime
    period_granularity: str  # "week" | "month"
    interest: float


class DepthConfig(BaseModel):
    """Guard-rail de profundidade/fan-out do ciclo de pesquisa."""

    max_depth: int = 2
    fanout_per_stage: dict[str, int] = Field(default_factory=dict)
    max_nodes_per_cycle: int = 200
    recursion_speed_factor: float = 1.0
```

E em `SegmentConfig`, adicionar o campo (mantém backward-compat — default
`None`):

```python
    research: "DepthConfig | None" = None
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `uv run pytest tests/domain/test_models.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: `pyright strict`**

Run: `uv run pyright src/content_factory/domain/models.py`
Expected: 0 erros.

- [ ] **Step 6: Rodar a suíte completa (garantir que nada quebrou)**

Run: `uv run pytest -q`
Expected: mesmo baseline de antes (120 passed, 1 failed ambiental
`test_postgres_up`, 14 skipped) + 4 novos testes passando.

- [ ] **Step 7: Commit**

```bash
git add src/content_factory/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): adiciona KeywordSignal/TrendPoint/DepthConfig"
```

---

### Task 2: `FanoutGuard` — guard-rail anti-explosão combinatória

**Files:**
- Create: `src/content_factory/domain/fanout_guard.py`
- Test: `tests/domain/test_fanout_guard.py`

**Interfaces:**
- Consumes: `KeywordSignal`, `DepthConfig` (Task 1).
- Produces: `FanoutGuard(cfg).admit(keyword, depth) -> bool`,
  `FanoutGuard(cfg).truncate(signals, stage) -> list[KeywordSignal]` —
  consumidos pela Task 5.

- [ ] **Step 1: Escrever os testes falhando**

```python
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, KeywordSignal


def test_admit_rejects_depth_beyond_max() -> None:
    guard = FanoutGuard(DepthConfig(max_depth=1))
    assert guard.admit("vela", depth=0) is True
    assert guard.admit("outra", depth=2) is False


def test_admit_dedupes_same_keyword_casefold() -> None:
    guard = FanoutGuard(DepthConfig())
    assert guard.admit("Vela Aromatica", depth=0) is True
    assert guard.admit("vela aromatica", depth=0) is False  # mesma kw, case/espaço


def test_admit_rejects_after_node_cap() -> None:
    guard = FanoutGuard(DepthConfig(max_nodes_per_cycle=2))
    assert guard.admit("a", depth=0) is True
    assert guard.admit("b", depth=0) is True
    assert guard.admit("c", depth=0) is False  # cap de 2 nós batido


def test_truncate_keeps_top_n_by_weight() -> None:
    guard = FanoutGuard(DepthConfig(fanout_per_stage={"web": 2}))
    signals = [
        KeywordSignal(keyword="a", source="web_suggest", weight=0.1),
        KeywordSignal(keyword="b", source="web_suggest", weight=0.9),
        KeywordSignal(keyword="c", source="web_suggest", weight=0.5),
    ]
    out = guard.truncate(signals, "web")
    assert [s.keyword for s in out] == ["b", "c"]


def test_truncate_without_configured_fanout_returns_all() -> None:
    guard = FanoutGuard(DepthConfig())
    signals = [KeywordSignal(keyword="a", source="web_suggest")]
    assert guard.truncate(signals, "web") == signals
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `uv run pytest tests/domain/test_fanout_guard.py -v`
Expected: FAIL — `ModuleNotFoundError` (`fanout_guard.py` não existe).

- [ ] **Step 3: Implementar**

Criar `src/content_factory/domain/fanout_guard.py`:

```python
from content_factory.domain.models import DepthConfig, KeywordSignal


class FanoutGuard:
    """Guard-rail anti-explosão combinatória de um ciclo de pesquisa.

    Três camadas: dedupe global do ciclo, cap duro de nós visitados,
    cap de fan-out por etapa (pelos de maior peso). Instância NOVA por
    ciclo — não persiste entre execuções (tendências mudam dia a dia).
    """

    def __init__(self, cfg: DepthConfig) -> None:
        self._cfg = cfg
        self._seen: set[str] = set()
        self._visited_count = 0

    def admit(self, keyword: str, depth: int) -> bool:
        if depth > self._cfg.max_depth:
            return False
        if self._visited_count >= self._cfg.max_nodes_per_cycle:
            return False
        key = keyword.casefold().strip()
        if key in self._seen:
            return False
        self._seen.add(key)
        self._visited_count += 1
        return True

    def truncate(self, signals: list[KeywordSignal], stage: str) -> list[KeywordSignal]:
        limit = self._cfg.fanout_per_stage.get(stage)
        if limit is None:
            return list(signals)
        return sorted(signals, key=lambda s: s.weight, reverse=True)[:limit]
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `uv run pytest tests/domain/test_fanout_guard.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: `pyright strict`**

Run: `uv run pyright src/content_factory/domain/fanout_guard.py`
Expected: 0 erros.

- [ ] **Step 6: Commit**

```bash
git add src/content_factory/domain/fanout_guard.py tests/domain/test_fanout_guard.py
git commit -m "feat(domain): adiciona FanoutGuard (anti-explosao combinatoria)"
```

---

### Task 3: Novos Protocols em `app/ports.py`

**Files:**
- Modify: `src/content_factory/app/ports.py`
- Test: `tests/app/test_ports.py` (arquivo já existe — ler antes de editar
  para seguir o padrão de teste de Protocol já usado lá, provavelmente
  `isinstance`/fakes mínimos comprovando conformidade estrutural)

**Interfaces:**
- Consumes: `KeywordSignal`, `TrendPoint` (Task 1), `CandidateVideo`
  (já existe).
- Produces: 4 Protocols novos — `KeywordExpander`, `TrendsProvider`,
  `SocialDiscoverer`, `KeywordSignalRepository` — consumidos pela Task 5
  (`research_pipeline.py`) e pela Task 4 (`KeywordSignalRepositoryImpl`
  implementa `KeywordSignalRepository`).

- [ ] **Step 1: Ler `tests/app/test_ports.py` para entender o padrão de teste**

Antes de escrever, rode `cat tests/app/test_ports.py` e confirme: o arquivo
testa conformidade estrutural dos Protocols existentes (`Collector`,
`VideoRepository`, etc.) via fakes mínimos (`class Fake: ...` + checagem de
tipo/chamada), não via `isinstance` com `@runtime_checkable` (os Protocols
atuais em `ports.py` não usam `@runtime_checkable` — siga o mesmo estilo,
sem adicionar o decorator nos novos).

- [ ] **Step 2: Escrever os testes falhando**

Seguindo o padrão encontrado no Step 1, adicionar em
`tests/app/test_ports.py` (adaptar à sintaxe exata do arquivo real):

```python
class _FakeKeywordExpander:
    platform = "web_suggest"

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
        return [KeywordSignal(keyword=seeds[0], source=self.platform, depth=depth)]


class _FakeTrendsProvider:
    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]:
        return []


class _FakeSocialDiscoverer:
    platform = "instagram"

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        return []


class _FakeKeywordSignalRepository:
    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int:
        return len(signals)

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]:
        return []


def test_new_protocols_accept_structural_fakes() -> None:
    expander: KeywordExpander = _FakeKeywordExpander()
    trends: TrendsProvider = _FakeTrendsProvider()
    social: SocialDiscoverer = _FakeSocialDiscoverer()
    signals: KeywordSignalRepository = _FakeKeywordSignalRepository()
    assert expander.platform == "web_suggest"
    assert social.platform == "instagram"
    assert trends is not None and signals is not None
```

(a asserção em si importa menos que o type-check do pyright — se os fakes
não satisfizerem os Protocols, `pyright strict` falha na atribuição
`expander: KeywordExpander = ...`, que é o teste de verdade aqui.)

- [ ] **Step 3: Rodar e confirmar falha**

Run: `uv run pytest tests/app/test_ports.py -v`
Expected: FAIL — `ImportError` (os 4 Protocols não existem em `ports.py`).

- [ ] **Step 4: Implementar**

Em `src/content_factory/app/ports.py`, atualizar o import de
`domain.models` para incluir `KeywordSignal`, `TrendPoint`, e adicionar
os 4 Protocols novos (depois de `Collector`, antes de `VideoRepository`
ou em qualquer ponto — manter agrupado por "família": expansão de
keyword/trends/social ficam juntos, repository fica perto dos outros
Repository):

```python
class KeywordExpander(Protocol):
    """Stage de descoberta de keywords relacionadas (ex. web search suggest)."""

    platform: ClassVar[str]

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]: ...


class TrendsProvider(Protocol):
    """Métrica de série temporal por keyword (ex. Google Trends)."""

    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]: ...


class SocialDiscoverer(Protocol):
    """Descoberta orientada a keyword/hashtag numa rede social (IG/YT/TikTok)."""

    platform: ClassVar[str]

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]: ...


class KeywordSignalRepository(Protocol):
    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int: ...
    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]: ...
```

- [ ] **Step 5: Rodar e confirmar sucesso**

Run: `uv run pytest tests/app/test_ports.py -v`
Expected: PASS.

- [ ] **Step 6: `pyright strict`**

Run: `uv run pyright src/content_factory/app/ports.py tests/app/test_ports.py`
Expected: 0 erros.

- [ ] **Step 7: Commit**

```bash
git add src/content_factory/app/ports.py tests/app/test_ports.py
git commit -m "feat(ports): adiciona KeywordExpander/TrendsProvider/SocialDiscoverer/KeywordSignalRepository"
```

---

### Task 4: Persistência — migração Alembic + ORM + `KeywordSignalRepositoryImpl`

**Files:**
- Create: `alembic/versions/0002_keyword_signals.py`
- Modify: `src/content_factory/infra/db/orm.py` (nova `KeywordSignalRow`)
- Modify: `src/content_factory/infra/db/repositories.py` (nova
  `KeywordSignalRepositoryImpl`)
- Test: `tests/infra/test_repositories.py` (arquivo já existe — **anexar**
  os testes novos nele, não criar arquivo separado; usa
  `@requires_infra` de `tests/conftest.py` — skip automático sem
  `TEST_DATABASE_URL` — e `create_async_engine(os.environ["TEST_DATABASE_URL"])`
  + `async_sessionmaker` por teste, igual a `test_upsert_is_idempotent`
  no mesmo arquivo; reaproveitar `SegmentRepositoryImpl(s).upsert(...)`
  para satisfazer a FK `keyword_signals.segment_id → segments.id`)

**Interfaces:**
- Consumes: `KeywordSignal` (Task 1), `KeywordSignalRepository` (Task 3).
- Produces: `KeywordSignalRepositoryImpl` implementando o Protocol —
  consumido pela Task 5.

- [ ] **Step 1: Ler o padrão de teste de repositório existente**

Abrir `tests/infra/test_repositories.py` inteiro — note o marker
`pytestmark = [pytest.mark.integration, pytest.mark.asyncio]` no topo do
arquivo, a função `@requires_infra` (importada de `tests/conftest.py`) e
o teste `test_upsert_is_idempotent` como referência exata de como criar
engine/session e satisfazer a FK de segmento via `SegmentRepositoryImpl`.

- [ ] **Step 2: Escrever os testes falhando** — anexar ao FINAL de
  `tests/infra/test_repositories.py` (mesmo arquivo, mesmo estilo):

```python
from content_factory.domain.models import KeywordSignal
from content_factory.infra.db.repositories import KeywordSignalRepositoryImpl


@requires_infra
async def test_keyword_signal_upsert_many_inserts_new_signals() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ks-{uuid4().hex[:8]}",
                name="Teste KS",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = KeywordSignalRepositoryImpl(s)
        signals = [
            KeywordSignal(keyword="vela aromatica", source="web_suggest", weight=0.8, depth=0),
            KeywordSignal(keyword="vela de soja", source="web_suggest", weight=0.6, depth=0),
        ]
        saved = await repo.upsert_many(seg.id, signals)
        assert saved == 2


@requires_infra
async def test_keyword_signal_upsert_many_dedupes_by_segment_keyword_source() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ks-{uuid4().hex[:8]}",
                name="Teste KS",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = KeywordSignalRepositoryImpl(s)
        sig = KeywordSignal(keyword="vela aromatica", source="web_suggest", weight=0.5, depth=0)
        await repo.upsert_many(seg.id, [sig])
        sig_updated = KeywordSignal(
            keyword="vela aromatica", source="web_suggest", weight=0.9, depth=0
        )
        await repo.upsert_many(seg.id, [sig_updated])  # mesmo (segment,keyword,source)
        top = await repo.top_for_segment(seg.id, source="web_suggest", limit=10)
        assert len(top) == 1  # não duplicou
        assert top[0].weight == 0.9  # peso foi atualizado


@requires_infra
async def test_keyword_signal_top_for_segment_orders_by_weight_desc() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        seg = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ks-{uuid4().hex[:8]}",
                name="Teste KS",
                platforms=["web"],
                keywords=["k"],
            )
        )
        repo = KeywordSignalRepositoryImpl(s)
        await repo.upsert_many(
            seg.id,
            [
                KeywordSignal(keyword="a", source="web_suggest", weight=0.1, depth=0),
                KeywordSignal(keyword="b", source="web_suggest", weight=0.9, depth=0),
            ],
        )
        top = await repo.top_for_segment(seg.id, source=None, limit=10)
        assert [s.keyword for s in top] == ["b", "a"]
```

(adicionar `KeywordSignal`/`KeywordSignalRepositoryImpl` aos imports do
topo do arquivo — slug com `uuid4().hex[:8]` evita colisão entre reruns
da suíte, mesmo precedente de `candidate()` no mesmo arquivo.)

- [ ] **Step 3: Rodar e confirmar falha**

Run: `TEST_DATABASE_URL=<...> uv run pytest tests/infra/test_repositories.py -k keyword_signal -v`
Expected: FAIL — `ModuleNotFoundError`/tabela inexistente (se
`TEST_DATABASE_URL` não estiver setada, os testes são SKIPPED por
`@requires_infra`, não FAIL — nesse caso, rodar dentro do
`docker exec cf-python` ou exportar a var localmente antes de prosseguir).

- [ ] **Step 4: Criar a migração Alembic**

```bash
cd <repo>
docker exec cf-python uv run alembic revision -m "keyword_signals"
```

(ou gerar manualmente se o container não estiver de pé — nomear o arquivo
`alembic/versions/0002_keyword_signals.py`, `down_revision = '0001'`).
Editar o arquivo gerado:

```python
def upgrade() -> None:
    op.create_table(
        "keyword_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "keyword", "source", name="uq_keyword_signal"),
    )


def downgrade() -> None:
    op.drop_table("keyword_signals")
```

- [ ] **Step 5: Adicionar `KeywordSignalRow` em `orm.py`**

Em `src/content_factory/infra/db/orm.py`, seguindo o padrão exato de
`CollectRunRow` (mesmos imports `Mapped`/`mapped_column`, mesmo estilo):

```python
class KeywordSignalRow(Base):
    __tablename__ = "keyword_signals"
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), index=True)
    keyword: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32))
    weight: Mapped[float] = mapped_column(default=0.0)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 6: Implementar `KeywordSignalRepositoryImpl`**

Em `src/content_factory/infra/db/repositories.py`, seguindo o padrão de
`VideoRepositoryImpl.upsert_candidates` (`pg_insert(...).on_conflict_do_update`):

```python
class KeywordSignalRepositoryImpl(KeywordSignalRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int:
        saved = 0
        for sig in signals:
            stmt = pg_insert(KeywordSignalRow).values(
                segment_id=segment_id,
                keyword=sig.keyword,
                source=sig.source,
                weight=sig.weight,
                depth=sig.depth,
                created_at=datetime.now(UTC),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["segment_id", "keyword", "source"],
                set_={"weight": stmt.excluded.weight, "depth": stmt.excluded.depth},
            )
            await self._s.execute(stmt)
            saved += 1
        return saved

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]:
        stmt = select(KeywordSignalRow).where(KeywordSignalRow.segment_id == segment_id)
        if source is not None:
            stmt = stmt.where(KeywordSignalRow.source == source)
        stmt = stmt.order_by(KeywordSignalRow.weight.desc()).limit(limit)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            KeywordSignal(keyword=r.keyword, source=r.source, weight=r.weight, depth=r.depth)
            for r in rows
        ]
```

(adicionar `datetime`/`UTC` ao import do topo do arquivo se não estiverem
já importados — conferir antes de duplicar import.)

- [ ] **Step 7: Rodar e confirmar sucesso**

```bash
docker exec cf-python uv run alembic upgrade head
```

Run: `uv run pytest tests/infra/test_repositories.py -k keyword_signal -v`
Expected: PASS (3 testes).

- [ ] **Step 8: `alembic check` + `pyright strict`**

```bash
docker exec cf-python uv run alembic check
uv run pyright src/content_factory/infra/db/orm.py src/content_factory/infra/db/repositories.py
```

Expected: ambos limpos.

- [ ] **Step 9: Commit**

```bash
git add alembic/versions/0002_keyword_signals.py src/content_factory/infra/db/orm.py src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(db): tabela keyword_signals + KeywordSignalRepositoryImpl"
```

---

### Task 5: Use-case `run_research_cycle` (`app/research_pipeline.py`)

**Files:**
- Create: `src/content_factory/app/research_pipeline.py`
- Test: `tests/app/test_research_pipeline.py`

**Interfaces:**
- Consumes: `KeywordExpander`/`TrendsProvider`/`SocialDiscoverer`/
  `KeywordSignalRepository` (Task 3), `FanoutGuard`/`DepthConfig`/
  `KeywordSignal` (Tasks 1-2), e os Protocols já existentes
  `SegmentRepository`/`VideoRepository`/`CollectRunRepository`/`Lock`/
  `RateLimiter` (`app/ports.py`, sem mudança).
- Produces: `ResearchDeps` (dataclass), `ResearchOutcome` (dataclass),
  `run_research_cycle(segment_slug, depth_cfg, deps) -> ResearchOutcome` —
  topo da pirâmide desta fase; consumido pela Fase D (worker) no futuro,
  não nesta fase.

- [ ] **Step 1: Escrever os testes falhando**

Reaproveitar de `tests/stubs.py` (NÃO recriar): `FakeLock`, `FakeLimiter`,
`FakeRepos().videos`, `FakeRepos().runs`. **Atenção**: `FakeRepos().segments`
(`_FakeSegments.get_by_slug`) sempre devolve `keywords=[]` e
`platforms=["youtube", "tiktok"]` fixos (feito sob medida pro `collect()`,
não serve pro teste de orquestração desta task, que precisa de keywords
reais pra `guard.admit` ter o que filtrar e de `platforms=["instagram"]`
pra bater com `deps.socials`). Para esta task, escrever um fake de
`SegmentRepository` local e mínimo no próprio arquivo de teste novo (igual
ao espírito de `_FakeGatedCollector` em `tests/app/test_collect.py`),
retornando `Segment(id=1, slug="candles", name="candles",
platforms=["instagram"], keywords=["vela"])`. Os fakes
`_FakeWeb`/`_FakeTrends`/`_FakeSocial`/`_FakeSignalRepo` também ficam
inline no novo arquivo de teste (não existem em `stubs.py` — só
adicioná-los lá se decidir que serão reaproveitados nas Fases B-E,
decisão do implementador).

```python
import pytest

from content_factory.app.ports import KeywordSignalRepository
from content_factory.app.research_pipeline import ResearchDeps, run_research_cycle
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import (
    CandidateVideo,
    DepthConfig,
    KeywordSignal,
    Segment,
    TrendPoint,
)


class _FakeWeb:
    platform = "web_suggest"

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
        return [
            KeywordSignal(
                keyword=f"{seeds[0]} relacionada", source=self.platform, weight=0.7, depth=depth + 1
            )
        ]


class _FakeTrends:
    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]:
        from datetime import UTC, datetime

        return [
            TrendPoint(
                keyword=k,
                period_start=datetime(2026, 10, 1, tzinfo=UTC),
                period_granularity="week",
                interest=50.0,
            )
            for k in keywords
        ]


class _FakeSocial:
    platform = "instagram"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def discover_by_keywords(self, keywords, hashtags, depth) -> list[CandidateVideo]:
        self.calls.append(keywords)
        return []


class _FakeSignalRepo(KeywordSignalRepository):
    def __init__(self) -> None:
        self.saved: list[KeywordSignal] = []

    async def upsert_many(self, segment_id, signals) -> int:
        self.saved.extend(signals)
        return len(signals)

    async def top_for_segment(self, segment_id, source, limit):
        return []


class _FakeSegmentsWithKeywords:
    """Diferente de FakeRepos().segments: devolve keywords/platforms reais
    pro guard.admit ter o que filtrar e pro loop social bater com `socials`."""

    async def get_by_slug(self, slug: str) -> Segment:
        return Segment(id=1, slug=slug, name=slug, platforms=["instagram"], keywords=["vela"])

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        raise NotImplementedError


def _make_deps(lock: FakeLock | None = None) -> ResearchDeps:
    repos = FakeRepos()
    return ResearchDeps(
        web=_FakeWeb(),
        trends=_FakeTrends(),
        socials={"instagram": _FakeSocial()},
        signals=_FakeSignalRepo(),
        segments=_FakeSegmentsWithKeywords(),
        videos=repos.videos,
        runs=repos.runs,
        lock=lock or FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(DepthConfig()),
    )


@pytest.mark.asyncio
async def test_run_research_cycle_orchestrates_web_trends_social() -> None:
    social = _FakeSocial()
    deps = _make_deps()
    deps.socials["instagram"] = social
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is True
    assert out.counts["web_found"] == 1
    assert out.counts["trends_found"] == 1
    assert social.calls  # social foi chamado com as keywords acumuladas


@pytest.mark.asyncio
async def test_run_research_cycle_respects_lock() -> None:
    deps = _make_deps(lock=FakeLock(acquired=False))
    out = await run_research_cycle("candles", DepthConfig(), deps)
    assert out.ok is False
    assert out.counts.get("ja_em_execucao") is True
```

(ajustar `FakeLock(acquired=False)` ao assinatura real encontrada em
`tests/stubs.py:74` no Step 1 — o parâmetro já existe lá, confirme o
nome exato antes de usar. Adicionar também um teste de `guard.admit`
realmente filtrando — ex. seed repetida não gera 2 chamadas a
`web.expand`.)

- [ ] **Step 2: Rodar e confirmar falha**

Run: `uv run pytest tests/app/test_research_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError` (`research_pipeline.py` não existe).

- [ ] **Step 3: Implementar**

Criar `src/content_factory/app/research_pipeline.py`:

```python
from dataclasses import dataclass, field

from content_factory.app.ports import (
    CollectRunRepository,
    KeywordExpander,
    KeywordSignalRepository,
    Lock,
    RateLimiter,
    SegmentRepository,
    SocialDiscoverer,
    TrendsProvider,
    VideoRepository,
)
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import DepthConfig, KeywordSignal


@dataclass
class ResearchDeps:
    web: KeywordExpander
    trends: TrendsProvider
    socials: dict[str, SocialDiscoverer]
    signals: KeywordSignalRepository
    segments: SegmentRepository
    videos: VideoRepository
    runs: CollectRunRepository
    lock: Lock
    limiter: RateLimiter
    guard: FanoutGuard


@dataclass
class ResearchOutcome:
    ok: bool
    counts: dict[str, int | bool] = field(default_factory=dict)


async def run_research_cycle(
    segment_slug: str, depth_cfg: DepthConfig, deps: ResearchDeps
) -> ResearchOutcome:
    if not await deps.lock.acquire_once(f"research:{segment_slug}"):
        return ResearchOutcome(ok=False, counts={"ja_em_execucao": True})
    try:
        segment = await deps.segments.get_by_slug(segment_slug)
        counts: dict[str, int | bool] = {
            "web_found": 0,
            "trends_found": 0,
            "signals_saved": 0,
            "social_found": 0,
        }

        seeds = [k for k in segment.keywords if deps.guard.admit(k, 0)]
        await deps.limiter.acquire(deps.web.platform)
        web_signals = deps.guard.truncate(await deps.web.expand(seeds, 0), "web")
        counts["web_found"] = len(web_signals)

        trend_keywords = [s.keyword for s in web_signals if deps.guard.admit(s.keyword, 1)]
        await deps.limiter.acquire("trends")
        trend_points = await deps.trends.interest_over_time(trend_keywords, "week")
        trend_signals = deps.guard.truncate(
            [
                KeywordSignal(keyword=p.keyword, source="trends", weight=p.interest / 100, depth=1)
                for p in trend_points
            ],
            "trends",
        )
        counts["trends_found"] = len(trend_signals)

        all_signals = web_signals + trend_signals
        counts["signals_saved"] = await deps.signals.upsert_many(segment.id, all_signals)

        keywords_for_social = [s.keyword for s in all_signals]
        social_found = 0
        for platform, discoverer in deps.socials.items():
            if platform not in segment.platforms:
                continue
            try:
                await deps.limiter.acquire(platform)
                candidates = await discoverer.discover_by_keywords(keywords_for_social, [], 1)
                social_found += len(candidates)
            except (
                Exception
            ):  # erro de 1 rede social não aborta as demais (mesmo padrão de collect())
                continue
        counts["social_found"] = social_found

        return ResearchOutcome(ok=True, counts=counts)
    finally:
        await deps.lock.release(f"research:{segment_slug}")
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `uv run pytest tests/app/test_research_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: `pyright strict`**

Run: `uv run pyright src/content_factory/app/research_pipeline.py tests/app/test_research_pipeline.py`
Expected: 0 erros.

- [ ] **Step 6: Rodar a suíte completa**

Run: `uv run pytest -q`
Expected: baseline + todos os testes novos das Tasks 1-5 passando, nenhuma
regressão nos testes existentes (`collect()`/`score()` intocados).

- [ ] **Step 7: Commit**

```bash
git add src/content_factory/app/research_pipeline.py tests/app/test_research_pipeline.py
git commit -m "feat(app): run_research_cycle orquestra web->trends->social com guard-rail"
```

---

### Task 6: Gates finais, docs e handoff

**Files:**
- Modify: `docs/HANDOFF.md`

**Interfaces:** nenhuma (fechamento da fase).

- [ ] **Step 1: Gates completos**

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
docker exec cf-python uv run alembic check
uv run bandit -r src/
uv run pip-audit
uv run mkdocs build --strict
uv run python scripts/check_privacy.py
```

Expected: todos saem com exit `0` (pytest pode manter a falha ambiental
pré-existente `test_postgres_up`, documentada desde a Fase 0).

- [ ] **Step 2: Atualizar `docs/HANDOFF.md`**

Mover o item "Fase A — fundação do pipeline" do roadmap para
"✅ CONCLUÍDO", citando os commits das Tasks 1-5 e deixando claro que
nenhum provider real existe ainda (Fases B-E são planos futuros, salvos
em `docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-{c,d,e,b}.md`, não
implementados).

- [ ] **Step 3: Commit final**

```bash
git add docs/HANDOFF.md
git commit -m "docs: fecha Fase A (fundacao do pipeline de pesquisa)"
```

## Próxima fase

`docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-c.md` (Instagram orientado
a keyword) — depende desta fase mergeada. Ordem completa do roadmap:
A → C → D → E → B (Google Trends por último, decisão do usuário).
