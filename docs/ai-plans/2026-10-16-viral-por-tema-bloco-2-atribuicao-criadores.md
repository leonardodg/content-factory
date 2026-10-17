# Bloco 2 — Atribuição por termo, log de buscas e criadores (R6+R8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gravar **qual termo trouxe cada vídeo** (e em que posição), registrar **cada busca executada** (inclusive as que voltam vazias) e preencher `creators` ligando `videos.creator_id`. Sem isso não existe ranking de temas nem comparação justa entre origens.

**Architecture:**
- **Busca por termo** é uma capacidade separada do `Collector`: porta `TermSearcher.search_term(segment, query, limit) -> list[Discovery]`, implementada **só pelo YouTube**. A busca única `"a" OR "b"` de hoje torna a atribuição impossível.
- `collect()` chama o `TermSearcher` **uma busca por vez**, cada uma dentro do seu próprio `counting()`. Assim a cota e os resultados de cada busca vão para `search_queries`.
- Plataformas sem `TermSearcher` seguem o `discover()` legado. Instagram é mídia própria; TikTok está fora (`msToken`). Os vídeos delas são gravados com `origin="unattributed"` e ficam fora do ranking.
- A fase social do **research não atribui**. No teste, ela fica desligada por env (`RESEARCH_SOCIAL_PLATFORMS=none`): atribuir no research exigiria 1 busca por termo × 30–60 termos, de 3 mil a 18 mil unidades às 07h, o que esgotaria a cota do dia (revisão QA).
- Criadores (R8) saem do `channelId` que o YouTube já recebe e hoje descarta.
- O **plano** de quais termos buscar (vagas por origem, rodízio) é o Bloco 4. Aqui o plano provisório (`provisional_queries`) existe só para os testes. **Não fazer deploy sem o Bloco 4.**

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy 2 async, Alembic, httpx, pytest (asyncio), ruff, pyright strict.

## Global Constraints

(repetidas do Bloco 1 — valem para todo o épico)

- `domain/` puro; `app/` → portas; `infra/` implementa portas. Dependência unidirecional.
- `pyright` strict 0 erros; `ruff` linha 100, aspas duplas; `ruff format` limpo.
- TDD; envelope só com contagens (dados no banco); sem segredo nem caminho absoluto de home em arquivo rastreado (`scripts/check_privacy.py`).
- `make gates` antes de "pronto" (inclui `alembic check`). Testes de integração só no Postgres/Redis descartáveis (`TEST_DATABASE_URL`/`TEST_REDIS_URL`).
- Migração aditiva: `0012` segue `0011` (Bloco 1).
- Não tocar `video_scores`/`viral_score`/`FORMULA_VERSION`.
- Sem `assert` em código de produção (bandit B101): invariantes viram erro explícito.

## Referência

Especificação §4 R6 e R8; §2 (criadores vazios; sem atribuição); §5 (cota). **Depende do Bloco 1** (modelos/migração-base).

## Decisões deste bloco (revisão QA)

| Decisão | Por quê |
|---|---|
| `TermSearcher` separado de `Collector`, injetado como `dict[str, TermSearcher]` (como `refreshables` no Bloco 3) | Instagram/TikTok/web e seus fakes não precisam implementar busca por termo. |
| 1 busca por chamada, `counting()` por busca | Cota e nº de resultados **por busca** → `search_queries` (R13: busca vazia também conta na cota e no rendimento da origem). |
| Termo enviado **sem** `or_query` (sem aspas de frase exata; `#tag` preservado) | `or_query` remove o `#` e força frase exata; com 1 termo por busca não há `OR`. Decisão registrada no docstring e coberta por teste. |
| Posição = ordem do `search.list` **antes** do filtro de idioma | A posição é a do ranking do YouTube; o filtro `_language_matches` só remove itens (a posição dos restantes não muda). |
| Candidatos **deduplicados** por `platform_video_id` antes do `upsert_candidates`; **todas** as descobertas gravadas | O mesmo vídeo achado por 2 termos gerava 2 linhas de `video_metrics` na mesma transação (mesmo `captured_at`). |
| Sem atribuição best-effort no Instagram/TikTok: `origin="unattributed"`, `term=""` | Atribuir ao `queries[0]` ou por substring contaminaria o ranking de temas. |
| `RESEARCH_SOCIAL_PLATFORMS` (unset = todas as do segmento, como hoje; `none` = nenhuma) | Orçamento da §5 (~330 unidades/dia no research) só fecha sem a fase social do YouTube. |

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/models.py` (editar) | `SearchQuery`, `Discovery`, `SearchQueryRun`; `CandidateVideo.creator_platform_id`. |
| `src/content_factory/app/ports.py` (editar) | `TermSearcher`; `DiscoveryRepository`, `SearchQueryRepository` (+ NoOps). |
| `src/content_factory/infra/collectors/youtube.py` (editar) | `_search_ranked` (posição), `search_term`, `_map` com `creator_platform_id`. |
| `src/content_factory/infra/db/orm.py` (editar) | `VideoDiscoveryRow`, `SearchQueryRow`. |
| `alembic/versions/0012_video_discoveries_search_queries.py` (novo) | As duas tabelas. |
| `src/content_factory/infra/db/repositories.py` (editar) | `DiscoveryRepositoryImpl`, `SearchQueryRepositoryImpl`; criadores no `upsert_candidates`. |
| `src/content_factory/app/collect.py` (editar) | Fase de rede por busca; dedupe; gravação de descobertas e do log. |
| `src/content_factory/app/research_pipeline.py` (editar) | `RESEARCH_SOCIAL_PLATFORMS`. |
| `src/content_factory/infra/queue/composition.py` (editar) | Injeção de `term_searchers`, `discoveries`, `search_log`. |
| `tests/...` | TDD por arquivo. |

---

### Task 1: DTOs `SearchQuery`, `Discovery`, `SearchQueryRun`

**Files:**
- Modify: `src/content_factory/domain/models.py`
- Test: `tests/domain/test_models.py` (editar)

**Interfaces:**
- Produces:
  - `class SearchQuery(BaseModel): term: str; origin: str; query_id: str` (`term` é o texto **enviado** à API — hashtag do YouTube vai como `#tag`)
  - `class Discovery(BaseModel): candidate: CandidateVideo; term: str; origin: str; query_id: str | None = None; result_position: int | None; run_kind: str` (`run_kind`: `collect`)
  - `class SearchQueryRun(BaseModel): platform: str; query_id: str; term: str; origin: str; results: int; kept: int; quota_used: int; error_code: str | None = None; started_at: datetime; finished_at: datetime`
  - `CandidateVideo.creator_platform_id: str | None = None`

- [ ] **Step 1: Teste que falha**

```python
# tests/domain/test_models.py  (adicionar)
from datetime import UTC, datetime

from content_factory.domain.models import CandidateVideo, Discovery, SearchQuery, SearchQueryRun


def test_search_query_discovery_e_run() -> None:
    q = SearchQuery(term="vela artesanal", origin="seed", query_id="search-plan-v1:0:0:seed")
    cand = CandidateVideo(
        platform="youtube",
        platform_video_id="v1",
        url="u",
        creator_handle="c",
        creator_platform_id="UC1",
    )
    d = Discovery(
        candidate=cand,
        term=q.term,
        origin=q.origin,
        query_id=q.query_id,
        result_position=1,
        run_kind="collect",
    )
    now = datetime(2026, 10, 20, tzinfo=UTC)
    run = SearchQueryRun(
        platform="youtube",
        query_id=q.query_id,
        term=q.term,
        origin=q.origin,
        results=0,
        kept=0,
        quota_used=100,
        started_at=now,
        finished_at=now,
    )
    assert d.candidate.creator_platform_id == "UC1"
    assert run.results == 0 and run.error_code is None  # busca vazia é registrada
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/domain/test_models.py -q` → FAIL.

- [ ] **Step 3: Implementar** em `models.py`: `creator_platform_id: str | None = None` em `CandidateVideo` e os 3 modelos ao fim, com docstring de 1 linha cada (padrão do arquivo).

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): SearchQuery, Discovery, SearchQueryRun e creator_platform_id (R6/R8)"
```

---

### Task 2: YouTube — posição, busca por termo e `channelId`

**Files:**
- Modify: `src/content_factory/infra/collectors/youtube.py`
- Modify: `src/content_factory/app/ports.py` (Protocol `TermSearcher`)
- Test: `tests/infra/collectors/test_youtube.py` (editar)

**Interfaces:**
- Produces:
  - `class TermSearcher(Protocol): platform: ClassVar[str]; async def search_term(self, segment: SegmentConfig, query: SearchQuery, limit: int) -> list[Discovery]: ...`
  - `YoutubeCollector._search_ranked(q, limit, search) -> list[tuple[int, CandidateVideo]]` — `(posição 1-based no search.list, candidato)`, já com o filtro de idioma aplicado.
  - `YoutubeCollector._search` passa a ser `[c for _, c in await self._search_ranked(...)]` (comportamento inalterado para `discover`/`discover_by_keywords`).
  - `YoutubeCollector.search_term` — **locale do segmento** (`segment.search or self.default_search`), como o `discover()`. A 1ª versão do plano usava só o `default_search` e perdia `relevance_language=pt`/`order=relevance`.
  - `_map` grava `creator_platform_id=sn.get("channelId")`.

- [ ] **Step 1: Testes que falham** (com o transport fake já existente no arquivo; estenda-o para registrar as requisições e aceitar idiomas por vídeo)

```python
def test_search_term_atribui_termo_posicao_e_locale() -> None:
    # search.list devolve [v1, v2, v3]; v2 declara idioma "en" → cai no filtro
    seen: list[httpx.Request] = []
    col = _collector(_transport(search_ids=["v1", "v2", "v3"], langs={"v2": "en"}, log=seen))
    seg = _segment(
        search=SearchConfig(relevance_language="pt", region_code="BR", order="relevance")
    )
    q = SearchQuery(term="vela artesanal", origin="seed", query_id="p:0:0:seed")
    out = asyncio.run(col.search_term(seg, q, limit=25))
    assert [(d.candidate.platform_video_id, d.result_position) for d in out] == [
        ("v1", 1),
        ("v3", 3),
    ]
    assert all(d.term == "vela artesanal" and d.origin == "seed" for d in out)
    search_req = next(r for r in seen if r.url.path.endswith("/search"))
    assert search_req.url.params["relevanceLanguage"] == "pt"
    assert search_req.url.params["q"] == "vela artesanal"  # sem aspas de or_query


def test_search_term_preserva_hashtag() -> None:
    seen: list[httpx.Request] = []
    col = _collector(_transport(search_ids=["v1"], log=seen))
    q = SearchQuery(term="#velaaromatica", origin="hashtag:youtube", query_id="p:0:3:h")
    asyncio.run(col.search_term(_segment(), q, limit=25))
    assert (
        next(r for r in seen if r.url.path.endswith("/search")).url.params["q"] == "#velaaromatica"
    )


def test_map_grava_creator_platform_id() -> None:
    cand = _collector(_transport())._map(
        {
            "id": "v1",
            "snippet": {
                "channelId": "UC1",
                "channelTitle": "Canal",
                "title": "t",
                "publishedAt": "2026-10-01T00:00:00Z",
            },
            "statistics": {"viewCount": "10"},
        },
        {"UC1": 100},
    )
    assert cand.creator_platform_id == "UC1"
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/infra/collectors/test_youtube.py -q -k "search_term or creator_platform"` → FAIL.

- [ ] **Step 3: Implementar**

```python
async def _search_ranked(
    self, q: str, limit: int, search: SearchConfig | None = None
) -> list[tuple[int, CandidateVideo]]:
    """Corpo do `_search` atual; a posição vem da ordem de `ids` (search.list).

    O `videos.list` não garante a ordem pedida: ordenar pelos índices de
    `ids`. O filtro de idioma remove itens sem renumerar os restantes.
    """
    ...  # mesmo corpo do _search atual até montar `followers`
    position = {vid: i for i, vid in enumerate(ids, start=1)}
    ranked = sorted(
        ((position[str(v["id"])], self._map(v, followers)) for v in stats_by_id.values()),
        key=lambda pc: pc[0],
    )
    wanted = search.relevance_language if search is not None else None
    return [(p, c) for p, c in ranked if _language_matches(c.language, wanted)]


async def _search(
    self, q: str, limit: int, search: SearchConfig | None = None
) -> list[CandidateVideo]:
    return [c for _, c in await self._search_ranked(q, limit, search)]


async def search_term(
    self, segment: SegmentConfig, query: SearchQuery, limit: int
) -> list[Discovery]:
    """R6: 1 busca = 1 termo → atribuição exata. Custa 100 + 2 unidades.

    O termo vai como está (sem `or_query`): sem aspas de frase exata e com
    `#` preservado para as hashtags (R11).
    """
    locale = segment.search or self.default_search
    ranked = await self._search_ranked(query.term, limit, locale)
    return [
        Discovery(
            candidate=cand,
            term=query.term,
            origin=query.origin,
            query_id=query.query_id,
            result_position=position,
            run_kind="collect",
        )
        for position, cand in ranked
    ]
```

E no `_map`: `creator_platform_id=sn.get("channelId"),` (argumento nomeado, **sem** parênteses: a 1ª versão do plano tinha `x = (valor,)`, que cria uma tupla e o pydantic rejeita).

- [ ] **Step 4: Rodar e ver passar** — PASS (e os testes antigos do YouTube).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/collectors/youtube.py src/content_factory/app/ports.py \
        tests/infra/collectors/test_youtube.py
git commit -m "feat(youtube): busca por termo com posição, locale do segmento e channelId (R6/R8)"
```

---

### Task 3: Tabelas `video_discoveries` e `search_queries` (migração `0012`)

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`
- Create: `alembic/versions/0012_video_discoveries_search_queries.py`
- Test: `tests/infra/test_orm.py` (editar)

**Interfaces:**
- Produces: `VideoDiscoveryRow`, `SearchQueryRow`.

- [ ] **Step 1: Teste que falha**

```python
def test_tabelas_de_atribuicao_e_log_de_buscas() -> None:
    from content_factory.infra.db.orm import SearchQueryRow, VideoDiscoveryRow

    assert {
        "video_id",
        "segment_id",
        "platform",
        "term",
        "term_origin",
        "query_id",
        "result_position",
        "run_kind",
        "discovered_at",
    } <= {c.name for c in VideoDiscoveryRow.__table__.columns}
    assert {
        "segment_id",
        "platform",
        "query_id",
        "term",
        "origin",
        "results",
        "kept",
        "quota_used",
        "error_code",
        "started_at",
        "finished_at",
    } <= {c.name for c in SearchQueryRow.__table__.columns}
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar ORM + migração**

```python
class VideoDiscoveryRow(Base):
    """R6: qual termo (e posição) trouxe cada vídeo, por busca executada.

    Uma linha por (busca, vídeo): o mesmo vídeo achado em 16 coletas tem 16
    linhas — a deduplicação por vídeo é do relatório (Bloco 5), não daqui.
    """

    __tablename__ = "video_discoveries"
    __table_args__ = (
        Index("ix_video_discoveries_segment_discovered", "segment_id", "discovered_at"),
        Index("ix_video_discoveries_video_id", "video_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    platform: Mapped[str] = mapped_column(String(32))
    term: Mapped[str] = mapped_column(String(200))
    term_origin: Mapped[str] = mapped_column(String(32))
    query_id: Mapped[str | None] = mapped_column(String(96))
    result_position: Mapped[int | None] = mapped_column(Integer)
    run_kind: Mapped[str] = mapped_column(String(16))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SearchQueryRow(Base):
    """R5/R13: cada busca executada — inclusive as vazias e as que falharam."""

    __tablename__ = "search_queries"
    __table_args__ = (Index("ix_search_queries_segment_started", "segment_id", "started_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    platform: Mapped[str] = mapped_column(String(32))
    query_id: Mapped[str] = mapped_column(String(96))
    term: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(32))
    results: Mapped[int] = mapped_column(Integer)  # devolvidos pela API (pós-idioma)
    kept: Mapped[int] = mapped_column(Integer)  # únicos novos nesta coleta (pós-dedupe)
    quota_used: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

Migração `0012` (revision `0012`, down `0011`), mesmo molde da `0011`, criando as duas tabelas e os 3 índices.

- [ ] **Step 4: Rodar e ver passar + `uv run alembic check`** → PASS, sem drift.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/orm.py \
        alembic/versions/0012_video_discoveries_search_queries.py tests/infra/test_orm.py
git commit -m "feat(db): migração 0012 — video_discoveries e search_queries (R6/R13)"
```

---

### Task 4: Criadores preenchidos e ligados aos vídeos (R8)

**Files:**
- Modify: `src/content_factory/infra/db/repositories.py` (`VideoRepositoryImpl.upsert_candidates`)
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Consumes: `CandidateVideo.creator_platform_id`, `creator_handle`, `creator_followers`.
- Produces: `creators` upsertado; `videos.creator_id` preenchido/atualizado.

- [ ] **Step 1: Testes que falham** (integração)
  - candidato YouTube com `creator_platform_id="UC1"`, `creator_handle="Canal X"`, `creator_followers=1234` → `creators` tem `("youtube","UC1","Canal X",1234)` e `videos.creator_id` aponta para ele;
  - 2º upsert com `creator_followers=None` **não apaga** o `followers_count` (COALESCE);
  - candidato sem `creator_platform_id` (Instagram/web) → `creator_id` NULL e nenhuma linha em `creators`.

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/infra/test_repositories.py -q -k creator` → FAIL.

- [ ] **Step 3: Implementar** dentro do `begin_nested` por candidato, **antes** do `pg_insert(VideoRow)`:

```python
creator_id: int | None = None
if c.creator_platform_id:
    cstmt = pg_insert(CreatorRow).values(
        platform=c.platform,
        platform_creator_id=c.creator_platform_id,
        username=c.creator_handle,
        followers_count=c.creator_followers,
        raw={},  # o raw do candidato é do VÍDEO; não duplicar no criador
    )
    cstmt = cstmt.on_conflict_do_update(
        index_elements=["platform", "platform_creator_id"],
        set_={
            "username": cstmt.excluded.username,
            "followers_count": func.coalesce(
                cstmt.excluded.followers_count, CreatorRow.followers_count
            ),
        },
    )
    creator_id = (await self._s.execute(cstmt.returning(CreatorRow.id))).scalar_one()
```

No `values(...)` do `VideoRow`: `creator_id=creator_id`; no `set_`: `"creator_id": func.coalesce(stmt.excluded.creator_id, VideoRow.creator_id)`.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(db): upsert de creators e link videos.creator_id (R8)"
```

---

### Task 5: Repositórios de descobertas e do log de buscas

**Files:**
- Modify: `src/content_factory/app/ports.py`, `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Produces:
  - `DiscoveryRepository.record(segment_id: int, discoveries: Sequence[Discovery]) -> int` — devolve as linhas **gravadas** (não `len(discoveries)`); descoberta cujo vídeo não persistiu (savepoint) é pulada.
  - `SearchQueryRepository.record_many(segment_id: int, runs: Sequence[SearchQueryRun]) -> int`
  - `NoOpDiscoveryRepository`, `NoOpSearchQueryRepository` (defaults para testes/uso avulso).

- [ ] **Step 1: Teste que falha** — integração: 2 descobertas, uma de vídeo inexistente → `record` devolve 1; `record_many` grava uma busca com `results=0`.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — resolver `video_id` em **uma** consulta (`tuple_(...).in_(...)`), não uma por descoberta:

```python
class DiscoveryRepositoryImpl(DiscoveryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, segment_id: int, discoveries: Sequence[Discovery]) -> int:
        if not discoveries:
            return 0
        keys = {(d.candidate.platform, d.candidate.platform_video_id) for d in discoveries}
        rows = await self._s.execute(
            select(VideoRow.platform, VideoRow.platform_video_id, VideoRow.id).where(
                tuple_(VideoRow.platform, VideoRow.platform_video_id).in_(list(keys))
            )
        )
        ids = {(p, pid): vid for p, pid, vid in rows.all()}
        written = 0
        for d in discoveries:
            video_id = ids.get((d.candidate.platform, d.candidate.platform_video_id))
            if video_id is None:
                continue  # candidato não persistiu (savepoint): sem linha órfã
            self._s.add(
                VideoDiscoveryRow(
                    video_id=video_id,
                    segment_id=segment_id,
                    platform=d.candidate.platform,
                    term=d.term,
                    term_origin=d.origin,
                    query_id=d.query_id,
                    result_position=d.result_position,
                    run_kind=d.run_kind,
                )
            )
            written += 1
        await self._s.flush()
        return written
```

`discovered_at` fica com o `server_default` (`now()` da transação = hora da coleta). `SearchQueryRepositoryImpl.record_many` é `add_all` + `flush`.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ports.py src/content_factory/infra/db/repositories.py \
        tests/infra/test_repositories.py
git commit -m "feat(db): repositórios de video_discoveries e search_queries (R6/R13)"
```

---

### Task 6: Coleta por busca com atribuição, dedupe e log

**Files:**
- Modify: `src/content_factory/app/collect.py`
- Modify: `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_collect.py` (editar), `tests/stubs.py` (fakes `FakeTermSearcher`, `FakeDiscoveries`, `FakeSearchLog` — completos e tipados)

**Interfaces:**
- Consumes: `TermSearcher`, `SearchQuery`, `Discovery`, `SearchQueryRun`, repositórios da Task 5.
- Produces:
  - `CollectDeps.term_searchers: dict[str, TermSearcher] = {}`, `CollectDeps.discoveries`, `CollectDeps.search_log` (defaults NoOp).
  - `DiscoverResult` ganha `discoveries: list[Discovery]` e `query_runs: list[SearchQueryRun]`.
  - Helper **provisório** `provisional_queries(segment) -> list[SearchQuery]`: as 3 primeiras keywords como `seed` (`query_id=f"provisional:{i}"`). Substituído no Bloco 4 por `plan_queries`; **não fazer deploy com ele**.
  - Counts: `discoveries`, `searches`, `searches_empty`, `unattributed`.

- [ ] **Step 1: Testes que falham**

```python
async def test_collect_atribui_por_busca_e_deduplica_candidatos() -> None:
    # "vela artesanal" → [v1, v2]; "vela de soja" → [v2, v3]  (v2 nos dois)
    deps = collect_deps(
        term_searchers={
            "youtube": FakeTermSearcher(
                {"vela artesanal": ["v1", "v2"], "vela de soja": ["v2", "v3"]}
            )
        },
        segment=_segment(keywords=["vela artesanal", "vela de soja"]),
    )
    out = await collect("velas-artesanais", ["youtube"], 25, deps)
    assert sorted(c.platform_video_id for c in deps.videos.upserted) == ["v1", "v2", "v3"]
    assert out.counts["discoveries"] == 4  # v2 atribuído aos dois termos
    assert {(d.term, d.candidate.platform_video_id) for d in deps.discoveries.saved} >= {
        ("vela artesanal", "v2"),
        ("vela de soja", "v2"),
    }


async def test_collect_registra_busca_vazia_com_cota() -> None:
    deps = collect_deps(term_searchers={"youtube": FakeTermSearcher({}, quota_per_call=102)})
    out = await collect("velas-artesanais", ["youtube"], 25, deps)
    assert out.counts["searches_empty"] == out.counts["searches"] >= 1
    assert all(r.results == 0 and r.quota_used == 102 for r in deps.search_log.saved)


async def test_erro_numa_busca_para_as_seguintes_e_grava_o_que_veio() -> None:
    erro = RateLimited("youtube", "quota")
    searcher = FakeTermSearcher({"vela artesanal": ["v1"]}, fail_on="vela de soja", error=erro)
    deps = collect_deps(
        term_searchers={"youtube": searcher},
        segment=_segment(keywords=["vela artesanal", "vela de soja", "vela x"]),
    )
    out = await collect("velas-artesanais", ["youtube"], 25, deps)
    assert [c.platform_video_id for c in deps.videos.upserted] == ["v1"]
    assert [r.error_code for r in deps.search_log.saved] == [None, erro.code]  # "vela x" não roda
    assert out.partial is True  # falhou depois de inserir


async def test_plataforma_sem_term_searcher_usa_discover_legado_sem_atribuicao() -> None:
    deps = collect_deps(term_searchers={}, collectors=[FakeCollector("instagram", ["i1"])])
    out = await collect("velas-artesanais", ["instagram"], 25, deps)
    assert out.counts["unattributed"] == 1
    assert all(d.origin == "unattributed" and d.term == "" for d in deps.discoveries.saved)
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

Fase de rede, em `_discover_one`, quando `platform in deps.term_searchers` (as demais seguem o caminho atual):

```python
async def _search_queries(
    searcher: TermSearcher,
    segment: SegmentConfig,
    queries: list[SearchQuery],
    limit: int,
    deps: CollectDeps,
) -> tuple[list[Discovery], list[SearchQueryRun], CollectorError | None, int]:
    """Executa as buscas EM SÉRIE; cada uma com counting() próprio.

    Erro de contrato (cota, rede) interrompe as buscas seguintes da mesma
    plataforma — 429/403 de cota não melhora na próxima chamada — e o que
    já veio é persistido. Devolve (descobertas, log, erro, cota total).
    """
    discoveries: list[Discovery] = []
    runs: list[SearchQueryRun] = []
    quota = 0
    for query in queries:
        started = deps.clock()
        with counting() as counters:
            try:
                await deps.limiter.acquire(searcher.platform)
                found = await searcher.search_term(segment, query, limit)
            except CollectorError as exc:
                quota += counters.quota_used
                runs.append(
                    _query_run(
                        query,
                        searcher.platform,
                        0,
                        counters.quota_used,
                        started,
                        deps.clock(),
                        exc.code,
                    )
                )
                return discoveries, runs, exc, quota
        quota += counters.quota_used
        discoveries += found
        runs.append(
            _query_run(
                query,
                searcher.platform,
                len(found),
                counters.quota_used,
                started,
                deps.clock(),
                None,
            )
        )
    return discoveries, runs, None, quota
```

- Exceção **fora** do contrato segue a regra atual de `_discover_one` (`internal_error` + `logger.exception`), sem perder o que já veio.
- `found` da plataforma = candidatos **deduplicados** por `platform_video_id` (o primeiro vence), para o `upsert_candidates`; `discoveries` vão **todas** para `deps.discoveries.record`.
- `kept` de cada `SearchQueryRun` = quantos candidatos daquela busca foram os primeiros no dedupe da coleta (preencher antes de gravar o log).
- `report_discover` (breaker): sucesso se nenhuma busca falhou; senão o `code` do erro, como hoje.
- `DiscoverResult.quota_used` = soma das buscas (o `collect_runs.quota_used` por plataforma continua igual).
- Plataformas **sem** `TermSearcher`: `discover()` atual; as descobertas viram `Discovery(origin="unattributed", term="", query_id=None, result_position=None, run_kind="collect")`. A web (`partition_web`) segue intocada.
- Persistência (sequencial, como hoje): `upsert_candidates(found_dedup)` → `discoveries.record(...)` → `search_log.record_many(...)`, antes de `runs.record(run)`. Erro do upsert segue o contrato atual.
- `partial`: erro numa busca depois de inserir/atualizar ⇒ `ok=False, partial=True` (mesma regra atual).

Em `composition.py`: `term_searchers={"youtube": youtube_collector}` (mesma instância singleton), `discoveries=DiscoveryRepositoryImpl(session)`, `search_log=SearchQueryRepositoryImpl(session)`.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_collect.py tests/infra/queue/ -q` → PASS (novos + antigos, inclusive web).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/collect.py src/content_factory/infra/queue/composition.py \
        tests/app/test_collect.py tests/stubs.py
git commit -m "feat(collect): busca por termo com atribuição, dedupe e log de buscas (R6)"
```

---

### Task 7: `RESEARCH_SOCIAL_PLATFORMS` — fase social do research configurável

**Files:**
- Modify: `src/content_factory/app/research_pipeline.py`, `src/content_factory/infra/queue/composition.py`
- Modify: `.devcontainer/env/app.env.example` (documentar a env)
- Test: `tests/app/test_research_pipeline.py` (editar)

**Interfaces:**
- Produces: `ResearchDeps.social_platforms: frozenset[str] | None = None` (`None` = todas as do segmento, como hoje). A composição lê `RESEARCH_SOCIAL_PLATFORMS`: unset → `None`; `none` → `frozenset()`; lista → `frozenset(lista)`.

- [ ] **Step 1: Teste que falha**

```python
async def test_research_sem_fase_social_quando_desligada() -> None:
    deps = research_deps(social_platforms=frozenset())
    out = await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    assert out.counts["social_platforms_attempted"] == 0
    assert deps.socials["youtube"].calls == 0  # nenhuma cota de busca no research
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — filtrar `wanted` por `deps.social_platforms` quando não for `None`. Suggest, Trends e o pool seguem rodando.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/research_pipeline.py src/content_factory/infra/queue/composition.py \
        .devcontainer/env/app.env.example tests/app/test_research_pipeline.py
git commit -m "feat(research): RESEARCH_SOCIAL_PLATFORMS — fase social configurável (orçamento R5)"
```

---

### Task 8: Encerrar o bloco — gates e aceite

- [ ] **Step 1: Gates** — `make gates` verde.

- [ ] **Step 2: Aceite R6 (QA, D+0)** — 100% dos vídeos YouTube novos com atribuição:

```sql
SELECT count(*) FROM video_segments vs JOIN videos v ON v.id = vs.video_id
WHERE v.platform = 'youtube' AND vs.discovered_at > now() - interval '1 day'
  AND NOT EXISTS (SELECT 1 FROM video_discoveries vd
                  WHERE vd.video_id = v.id AND vd.term_origin <> 'unattributed');
-- esperado: 0
SELECT origin, count(*), sum(results), sum(quota_used), count(*) FILTER (WHERE results = 0)
FROM search_queries WHERE started_at > now() - interval '1 day' GROUP BY 1;
-- esperado: 1 linha por busca executada, inclusive vazias
SELECT count(*) FROM (SELECT video_id, captured_at FROM video_metrics
  GROUP BY 1, 2 HAVING count(*) > 1) d;  -- esperado: 0 (dedupe)
```

- [ ] **Step 3: Aceite R8**

```sql
SELECT count(*) AS youtube, count(creator_id) AS com_creator
FROM videos WHERE platform = 'youtube' AND first_collected_at > now() - interval '1 day';
-- esperado: com_creator = youtube
SELECT count(*), max(followers_count) FROM creators;  -- esperado: > 0
```

- [ ] **Step 4: Commit de encerramento** (se sobrou doc) — `git commit -am "docs(plans): bloco 2 concluído — R6/R8 verificados" || true`

---

## Auto-revisão da spec (R6/R8)

- **R6** ✓ `video_discoveries` com termo, origem, posição real do YouTube e tipo de execução (Tasks 2-3, 5-6); vídeo achado por vários termos ganha várias linhas e é gravado uma vez em `videos`/`video_metrics` (Task 6). Instagram/TikTok: `unattributed`, fora do ranking. Research: sem atribuição; a fase social é desligável (Task 7).
- **R8** ✓ `creators` upsertado e `videos.creator_id` ligado (Task 4); `subscriberCount` em `creators.followers_count`; o histórico segue em `video_metrics.followers_at_capture`.
- **R13 (base)** ✓ `search_queries` registra cada busca (cota, resultados, erro); o Bloco 6 lê daqui.
- Fora deste bloco: vagas por origem e rodízio (R5, Bloco 4) substituem `provisional_queries`.
