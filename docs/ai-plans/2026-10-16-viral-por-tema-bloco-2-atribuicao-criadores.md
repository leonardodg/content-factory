# Bloco 2 — Atribuição por termo e criadores (R6+R8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gravar **qual termo trouxe cada vídeo** (e em que posição), em coleta e pesquisa, e preencher `creators` ligando `videos.creator_id` — sem isso não existe ranking de temas.

**Architecture:** Duas mudanças de base: (1) os coletores passam a buscar **por termo** (`search_terms(queries, limit)`) porque atribuir é impossível com a busca única `"a" OR "b" OR ...` de hoje; (2) cada descoberta vira uma linha de `video_discoveries`. O **plano** de quais termos buscar (quotas por origem, rodízio) é o Bloco 4; aqui o YouTube é chamado termo a termo e o plano provisório é "cada keyword do segmento". Criadores (R8) saem do `channelId` que o YouTube já recebe e descarta.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy 2 async, Alembic, httpx, pytest (asyncio), ruff, pyright strict.

## Global Constraints

(repetidas do Bloco 1 — valem para todo o épico)

- `domain/` puro; `app/` → portas; `infra/` implementa portas. Dependência unidirecional.
- `pyright` strict 0 erros; `ruff` linha 100, aspas duplas; `ruff format` limpo.
- TDD; envelope só com contagens (dados no banco); sem segredo nem caminho absoluto de home em arquivo rastreado (`scripts/check_privacy.py`).
- `make gates` antes de "pronto" (inclui `alembic check`).
- Migração aditiva: `0012` segue `0011` (Bloco 1).
- Não tocar `video_scores`/`viral_score`/`FORMULA_VERSION`.

## Referência

Especificação §4 R6 e R8; §2 (criadores vazios; sem atribuição). **Depende do Bloco 1** apenas para existirem os modelos/migração-base (o pool não é usado aqui).

> **Nota de reordenação (aprovada):** §7 sugere R6 antes de R5. Aqui entra a **capacidade** de busca por termo (necessária para R6); o **plano** de busca por origem/cota (R5) fica no Bloco 4, substituindo o plano provisório `keywords→queries`.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/models.py` (editar) | `SearchQuery`, `Discovery`; `CandidateVideo.creator_platform_id`. |
| `src/content_factory/app/ports.py` (editar) | `Collector.search_terms`; `DiscoveryRepository`. |
| `src/content_factory/infra/collectors/youtube.py` (editar) | `search_terms` (1 busca por termo, `videos.list`+`channels.list`); `_map` grava `creator_platform_id`. |
| `src/content_factory/infra/collectors/instagram.py`, `tiktok.py` (editar) | `search_terms` (atribuição best-effort sobre `discover_by_keywords`). |
| `src/content_factory/infra/db/orm.py` (editar) | `VideoDiscoveryRow`. |
| `alembic/versions/0012_video_discoveries.py` (novo) | Tabela `video_discoveries`. |
| `src/content_factory/infra/db/repositories.py` (editar) | `DiscoveryRepositoryImpl`; criadores no `VideoRepositoryImpl.upsert_candidates`. |
| `src/content_factory/app/collect.py` (editar) | Construir queries; chamar `search_terms`; gravar descobertas. |
| `src/content_factory/app/research_pipeline.py` (editar) | Chamar `search_terms` e gravar descobertas `run_kind="research"`. |
| `src/content_factory/infra/queue/composition.py` (editar) | Injetar `discoveries`/`terms` onde couber. |
| `tests/...` | TDD por arquivo. |

---

### Task 1: DTOs `SearchQuery` e `Discovery`

**Files:**
- Modify: `src/content_factory/domain/models.py`
- Test: `tests/domain/test_models.py` (editar)

**Interfaces:**
- Produces:
  - `class SearchQuery(BaseModel): term: str; origin: str; query_id: str`
  - `class Discovery(BaseModel): candidate: CandidateVideo; term: str; origin: str; query_id: str | None = None; result_position: int; run_kind: str`
  - `CandidateVideo.creator_platform_id: str | None = None`

- [ ] **Step 1: Teste que falha**

```python
# tests/domain/test_models.py  (adicionar)
from content_factory.domain.models import CandidateVideo, Discovery, SearchQuery


def test_search_query_e_discovery() -> None:
    q = SearchQuery(term="vela artesanal", origin="seed", query_id="seed:00")
    cand = CandidateVideo(
        platform="youtube",
        platform_video_id="v1",
        url="u",
        creator_handle="c",
        creator_platform_id="ch1",
    )
    d = Discovery(
        candidate=cand,
        term=q.term,
        origin=q.origin,
        query_id=q.query_id,
        result_position=1,
        run_kind="collect",
    )
    assert d.candidate.creator_platform_id == "ch1"
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/domain/test_models.py -q` → FAIL.

- [ ] **Step 3: Implementar** em `models.py`: adicionar `creator_platform_id: str | None = None` em `CandidateVideo`, e ao fim:

```python
class SearchQuery(BaseModel):
    """Uma busca a executar: termo, origem (seed|suggest|trends|hashtag:*) e id."""

    term: str
    origin: str
    query_id: str


class Discovery(BaseModel):
    """Atribuição (R6): um vídeo encontrado por um termo, com posição."""

    candidate: CandidateVideo
    term: str
    origin: str
    query_id: str | None = None
    result_position: int
    run_kind: str  # collect | research
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): SearchQuery, Discovery e creator_platform_id (R6/R8)"
```

---

### Task 2: YouTube busca por termo e grava `channelId` (R6/R8)

**Files:**
- Modify: `src/content_factory/infra/collectors/youtube.py`
- Modify: `src/content_factory/app/ports.py` (só o Protocol; implementações nas próximas tasks)
- Test: `tests/infra/collectors/test_youtube.py` (editar)

**Interfaces:**
- Consumes: `SearchQuery`, `Discovery`.
- Produces:
  - `Collector.search_terms(self, queries: Sequence[SearchQuery], limit: int) -> list[Discovery]` (add ao Protocol; mantém `discover` para compatibilidade/uso avulso).
  - `YoutubeCollector._map` grava `creator_platform_id = snippet.channelId`.

- [ ] **Step 1: Teste que falha**

```python
# tests/infra/collectors/test_youtube.py  (adicionar, com o transport fake do arquivo)
def test_search_terms_atribui_termo_e_posicao() -> None:
    col = _collector_with_transport(_youtube_transport())  # helper do próprio teste
    queries = [
        SearchQuery(term="vela artesanal", origin="seed", query_id="seed:00"),
        SearchQuery(term="como fazer vela", origin="suggest", query_id="suggest:00"),
    ]
    discoveries = asyncio.run(col.search_terms(queries, limit=25))
    assert {d.term for d in discoveries} == {"vela artesanal", "como fazer vela"}
    assert all(d.run_kind == "collect" and d.result_position >= 1 for d in discoveries)


def test_map_grava_creator_platform_id() -> None:
    cand = _collector_with_transport(_youtube_transport())._map(
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

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/infra/collectors/test_youtube.py -q -k "search_terms or creator_platform"` → FAIL.

- [ ] **Step 3: Implementar**

Em `ports.py`:

```python
class Collector(Protocol):
    platform: ClassVar[str]

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]: ...

    async def search_terms(self, queries: Sequence[SearchQuery], limit: int) -> list[Discovery]: ...
```

Em `youtube.py`:

```python
from collections.abc import Sequence
from content_factory.domain.models import Discovery, SearchQuery

    async def search_terms(self, queries: Sequence[SearchQuery], limit: int) -> list[Discovery]:
        """1 busca por termo → atribuição exata (R6). Cada termo custa 100 unidades."""
        out: list[Discovery] = []
        for q in queries:
            candidates = await self._search(or_query([q.term]), limit, self.default_search)
            for position, cand in enumerate(candidates, start=1):
                out.append(Discovery(candidate=cand, term=q.term, origin=q.origin,
                                     query_id=q.query_id, result_position=position,
                                     run_kind="collect"))
        return out
```

> `search_terms` do YouTube usa o `default_search` do coletor. No worker o YouTube é o singleton com o padrão global; no research o `SearchBoundYoutube` amarra o `search` do segmento (Task 6). A Task 6 ajusta `SearchBoundYoutube.search_terms` para repassar o locale.

Em `_map`, adicione ao `CandidateVideo(...)`:

```python
creator_platform_id = (sn.get("channelId"),)
```

- [ ] **Step 4: Rodar e ver passar** — os dois testes PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/collectors/youtube.py src/content_factory/app/ports.py \
        tests/infra/collectors/test_youtube.py
git commit -m "feat(youtube): busca por termo com atribuição e channelId (R6/R8)"
```

---

### Task 3: `search_terms` no Instagram e no TikTok (atribuição best-effort)

**Files:**
- Modify: `src/content_factory/infra/collectors/instagram.py`, `src/content_factory/infra/collectors/tiktok.py`
- Test: `tests/infra/collectors/test_instagram.py`, `tests/infra/collectors/test_tiktok.py` (editar)

**Interfaces:**
- Consumes: `SearchQuery`, `Discovery`.
- Produces: `InstagramCollector.search_terms` e `TikTokCollector.search_terms`.

- [ ] **Step 1: Teste que falha**

```python
# tests/infra/collectors/test_instagram.py  (adicionar)
def test_instagram_search_terms_atribui_ao_termo_casado() -> None:
    col = _insta_with_transport(_own_media_transport(captions=["como fazer vela"]))
    qs = [SearchQuery("vela", "seed", "seed:00"), SearchQuery("sabonete", "suggest", "suggest:00")]
    discoveries = asyncio.run(col.search_terms(qs, limit=25))
    assert discoveries and discoveries[0].term == "vela"
    assert discoveries[0].run_kind == "collect"
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`AttributeError: search_terms`).

- [ ] **Step 3: Implementar** (padrão idêntico nos dois)

```python
async def search_terms(self, queries: Sequence[SearchQuery], limit: int) -> list[Discovery]:
    """Sem busca real por hashtag (App Review/token): reusa discover_by_keywords
    e atribui cada vídeo ao primeiro termo presente na sua descrição."""
    terms = [q.term for q in queries]
    candidates = await self.discover_by_keywords(terms, [], 1)
    out: list[Discovery] = []
    for position, cand in enumerate(candidates, start=1):
        text = f"{cand.title} {cand.description}".casefold()
        matched = next((q for q in queries if q.term.casefold() in text), queries[0])
        out.append(
            Discovery(
                candidate=cand,
                term=matched.term,
                origin=matched.origin,
                query_id=matched.query_id,
                result_position=position,
                run_kind="collect",
            )
        )
    return out
```

> TikTok: o corpo é o mesmo, delegando ao seu `discover_by_keywords` (que hoje falha com `msToken` vazio — comportamento inalterado e fora de escopo). Instagram: `terms` vazio → `queries[0]` jamais é acessado porque `candidates` vem vazio; garanta `queries` não-vazio no caller.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/collectors/instagram.py src/content_factory/infra/collectors/tiktok.py \
        tests/infra/collectors/test_instagram.py tests/infra/collectors/test_tiktok.py
git commit -m "feat(ig,tiktok): search_terms com atribuição best-effort (R6)"
```

---

### Task 4: Tabela `video_discoveries` (migração `0012`)

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`
- Create: `alembic/versions/0012_video_discoveries.py`
- Test: `tests/infra/test_orm.py` (editar)

**Interfaces:**
- Produces: `VideoDiscoveryRow` com colunas de R6.

- [ ] **Step 1: Teste que falha**

```python
def test_tabela_video_discoveries() -> None:
    from content_factory.infra.db.orm import VideoDiscoveryRow

    cols = {c.name for c in VideoDiscoveryRow.__table__.columns}
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
    } <= cols
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar ORM + migração**

```python
class VideoDiscoveryRow(Base):
    """R6: qual termo (e posição) trouxe cada vídeo, por coleta/pesquisa."""

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
    query_id: Mapped[str | None] = mapped_column(String(64))
    result_position: Mapped[int] = mapped_column(Integer)
    run_kind: Mapped[str] = mapped_column(String(16))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

Migração `0012` no mesmo molde de `0011` (revision `0012`, down `0011`), criando a tabela + os dois índices.

- [ ] **Step 4: Rodar e ver passar + `uv run alembic check`** → PASS, sem drift.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/orm.py alembic/versions/0012_video_discoveries.py tests/infra/test_orm.py
git commit -m "feat(db): migração 0012 — video_discoveries (R6)"
```

---

### Task 5: Criadores preenchidos e ligados aos vídeos (R8)

**Files:**
- Modify: `src/content_factory/infra/db/repositories.py` (`VideoRepositoryImpl.upsert_candidates`)
- Test: `tests/infra/test_repositories.py` (editar)

**Interfaces:**
- Consumes: `CandidateVideo.creator_platform_id`, `creator_handle`, `creator_followers`.
- Produces: `creators` upsertado; `videos.creator_id` preenchido/atualizado.

- [ ] **Step 1: Teste que falha** (integração)

```python
async def test_upsert_candidates_cria_creator_e_liga_video() -> None:
    # cria segmento; candidato do YouTube com creator_platform_id="UC1"
    cand = CandidateVideo(
        platform="youtube",
        platform_video_id="v1",
        url="u",
        creator_handle="Canal X",
        creator_platform_id="UC1",
        creator_followers=1234,
    )
    await repo.upsert_candidates(seg_id, "youtube", [cand])
    # SELECT c.platform_creator_id, c.username, c.followers_count, v.creator_id
    # esperado: ("UC1", "Canal X", 1234, <id do creator>)
```

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
        raw=c.raw,
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

E no `values(...)` do `VideoRow`: `creator_id=creator_id`; no `set_` do `on_conflict_do_update`:

```python
                            "creator_id": func.coalesce(
                                stmt.excluded.creator_id, VideoRow.creator_id
                            ),
```

Import: `CreatorRow` de `content_factory.infra.db.orm`.

- [ ] **Step 4: Rodar e ver passar** — PASS. Confirme que candidatos sem `creator_platform_id` (Instagram/TikTok/web) continuam com `creator_id=NULL`.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(db): upsert de creators e link videos.creator_id (R8)"
```

---

### Task 6: Atribuição na coleta (`collect`) e na pesquisa (`research`)

**Files:**
- Modify: `src/content_factory/app/ports.py` (porta `DiscoveryRepository`)
- Modify: `src/content_factory/infra/db/repositories.py` (`DiscoveryRepositoryImpl`)
- Modify: `src/content_factory/app/collect.py`, `src/content_factory/app/research_pipeline.py`
- Modify: `src/content_factory/infra/queue/composition.py` (wire)
- Test: `tests/app/test_collect.py`, `tests/app/test_research_pipeline.py` (editar)

**Interfaces:**
- Consumes: `Discovery`, `SearchQuery`, `DiscoveryRepository`.
- Produces:
  - `DiscoveryRepository.record(segment_id: int, discoveries: Sequence[Discovery]) -> int`
  - `CollectDeps.discoveries: DiscoveryRepository` (default `NoOpDiscoveryRepository`); `ResearchDeps.discoveries: DiscoveryRepository`.
  - `CollectOutcome.counts["discoveries"]` (nº de linhas gravadas).
  - Helper puro `queries_for_platform(segment, platform, run_index)` — **provisório** (cada keyword vira um `SearchQuery` de origem `seed`, `query_id=f"seed:{i:02d}"`); o Bloco 4 substitui por `domain/search_plan.py`.

- [ ] **Step 1: Teste que falha**

```python
# tests/app/test_collect.py  (adicionar, com fakes do tests/stubs.py)
async def test_collect_grava_atribuicao_por_termo() -> None:
    deps = _collect_deps_com_fake(candidates_per_term={"vela artesanal": 2})
    out = await collect("velas-artesanais", ["youtube"], 25, deps)
    assert out.counts["discoveries"] == 2
    assert all(d.term == "vela artesanal" for d in deps.discoveries.saved)
    assert all(d.run_kind == "collect" for d in deps.discoveries.saved)
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

Em `ports.py`:

```python
class DiscoveryRepository(Protocol):
    async def record(self, segment_id: int, discoveries: Sequence[Discovery]) -> int: ...


class NoOpDiscoveryRepository:
    async def record(self, segment_id: int, discoveries: Sequence[Discovery]) -> int:
        _ = segment_id
        return len(discoveries)
```

`DiscoveryRepositoryImpl` (em `repositories.py`), resolvendo `video_id` por `(platform, platform_video_id)` como o `WebHitRepositoryImpl` faz:

```python
class DiscoveryRepositoryImpl(DiscoveryRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, segment_id: int, discoveries: Sequence[Discovery]) -> int:
        now = datetime.now(UTC)
        for d in discoveries:
            video_id = (
                await self._s.execute(
                    select(VideoRow.id).where(
                        VideoRow.platform == d.candidate.platform,
                        VideoRow.platform_video_id == d.candidate.platform_video_id,
                    )
                )
            ).scalar_one_or_none()
            if video_id is None:
                continue  # candidato não persistiu (savepoint) — sem linha órfã
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
                    discovered_at=now,
                )
            )
        await self._s.flush()
        return len(discoveries)
```

Em `collect.py`: `_discover_one` passa a chamar `collector.search_terms(queries, limit)`; `collect()`:

```python
        queries = queries_for_platform(segment, platform, run_index=0)
        discoveries = await collector.search_terms(queries, limit)
        found = [d.candidate for d in discoveries]
```

e, na persistência, após `upsert_candidates`, gravar a atribuição do **mesmo** `found`:

```python
                    run.discoveries = await deps.discoveries.record(segment.id, discoveries)
                    counts["discoveries"] = int(counts.get("discoveries", 0)) + run.discoveries
```

> `DiscoverResult` ganha `discoveries: list[Discovery]` transportado da fase de rede. Ajuste `_discover_one` para devolvê-lo; `found` continua sendo `[d.candidate for d in discoveries]` para não quebrar `partition_web`/contagens.

Em `research_pipeline.py`: em `_discover_social_one`, montar `queries` a partir de `keywords` (origem `seed`) e chamar `discoverer.search_terms`; transportar `discoveries` no `SocialDiscoverResult`; gravar com `run_kind="research"` após o upsert.

Em `composition.py`: adicionar `discoveries=DiscoveryRepositoryImpl(session)` em `CollectDeps` e `ResearchDeps`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/app/test_collect.py tests/app/test_research_pipeline.py -q` → PASS (novos + antigos).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ports.py src/content_factory/infra/db/repositories.py \
        src/content_factory/app/collect.py src/content_factory/app/research_pipeline.py \
        src/content_factory/infra/queue/composition.py \
        tests/app/test_collect.py tests/app/test_research_pipeline.py
git commit -m "feat(collect,research): grava video_discoveries em coleta e pesquisa (R6)"
```

---

### Task 7: Encerrar o bloco — gates e aceite

- [ ] **Step 1: Gates** — `make gates` verde.

- [ ] **Step 2: Aceite R7 (áspera) — 100% dos vídeos novos com atribuição (QA D+0, primeiro dia após deploy)**

```sql
-- vídeos do YouTube descobertos ontem sem nenhuma linha de atribuição
SELECT count(*) FROM video_segments vs
JOIN videos v ON v.id = vs.video_id
WHERE vs.discovery_source = 'youtube' AND vs.discovered_at > now() - interval '1 day'
  AND NOT EXISTS (SELECT 1 FROM video_discoveries vd WHERE vd.video_id = v.id);
-- esperado: 0
```

- [ ] **Step 3: Aceite R8**

```sql
SELECT count(*) AS videos_youtube, count(creator_id) AS com_creator
FROM videos WHERE platform = 'youtube' AND first_collected_at > now() - interval '1 day';
-- esperado: com_creator = videos_youtube
SELECT count(*), max(followers_count) FROM creators;  -- esperado: > 0
```

- [ ] **Step 4: Commit de encerramento** (se sobrou doc) — `git commit -am "docs(plans): bloco 2 concluído — R6/R8 verificados" || true`

---

## Auto-revisão da spec (R6/R8)

- **R6** ✓ `video_discoveries` (Task 4), coletores por termo (Tasks 2-3), gravação em coleta e pesquisa (Task 6); vídeo achado por vários termos ganha várias linhas (uma por query). *Plano provisório* de queries aqui; o definitivo (R5) no Bloco 4.
- **R8** ✓ `creators` upsertado e `videos.creator_id` ligado (Task 5); `subscriberCount` em `creators.followers_count`; histórico segue em `video_metrics.followers_at_capture`.
- Fora deste bloco: quotas/rodízio por origem (R5, Bloco 4) — `queries_for_platform` é substituído por `domain/search_plan.py`.
