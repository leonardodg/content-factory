# Fase F — Integridade do pipeline (métricas, persistência de pesquisa, Trends histórico)

**Goal:** fechar o buraco mais crítico encontrado na auditoria desta
sessão — hoje nenhum fluxo grava linha em `video_metrics`, então todo
score roda sobre métricas zeradas — e corrigir os três problemas P0
apontados na revisão técnica externa ao `content-factory`: candidatos
descobertos pela pesquisa não são persistidos, a série temporal do
Google Trends é descartada, e os contadores operacionais da pesquisa são
ambíguos. Mapa geral: `docs/ai-plans/2026-10-12-content-factory-melhorias-roadmap.md`.

**Pré-requisito:** nenhum — Fases A-E já mergeadas em `main`. Não
depende de Fase G (scoring v2); só prepara o terreno para ela (sem dados
reais em `video_metrics`, nenhuma calibração de peso é possível).

> **Nota**: plano de intenção detalhada (mesmo padrão das Fases D/E) — leia
> os arquivos reais citados antes de executar; a arquitetura (assinaturas,
> nomes, pontos de integração) é o que está fixado aqui.

### Task F1: `upsert_candidates` grava snapshot em `video_metrics`

**Files:**
- Modify: `src/content_factory/infra/db/repositories.py`
  (`VideoRepositoryImpl.upsert_candidates`, linhas 69-124)
- Test: `tests/infra/test_repositories.py`

**Contexto do bug:** dentro do `async with self._s.begin_nested():` de
`upsert_candidates`, o código já resolve `video_id` (via `RETURNING
VideoRow.id`) e tem em mãos o `CandidateVideo` completo (`c.views`,
`c.likes`, `c.comments`, `c.shares`, `c.saves`, `c.creator_followers`).
Nenhuma dessas métricas chega a `video_metrics` — só vão para a coluna
`raw` (JSONB), inacessíveis ao `score()`/`latest_for_segment()`. Resultado:
`VideoMetricsRow` nunca tem uma linha para nenhum vídeo; `latest_for_segment`
sempre cai no ramo `m is None` de `_snapshot()` e devolve `VideoMetrics()`
(tudo zero).

**Correção:** inserir em `VideoMetricsRow` dentro do MESMO `begin_nested()`
(mesma atomicidade do precedente documentado no código — um INSERT
quebrado não deve envenenar a transação do lote), **preservando `None`
quando o coletor não reportou a métrica** (YouTube não expõe `shares`;
TikTok não expõe `saves`; Instagram pode não expor `views` de terceiros —
ADR-0003). A coluna já é nullable (`orm.py`: `views: Mapped[int | None]`
etc.) — o bug está só na ausência do INSERT, não no schema.

```python
# infra/db/repositories.py — dentro do try/begin_nested, depois do link
# (VideoSegmentRow), ainda dentro do "async with self._s.begin_nested():"
has_metric = any(v is not None for v in (c.views, c.likes, c.comments, c.shares, c.saves))
if has_metric:
    self._s.add(
        VideoMetricsRow(
            video_id=video_id,
            views=c.views,
            likes=c.likes,
            comments=c.comments,
            shares=c.shares,
            saves=c.saves,
            followers_at_capture=c.creator_followers,
        )
    )
```

Import `VideoMetricsRow` já existe no topo do arquivo (lista de imports de
`infra.db.orm`) — só falta usar.

**Por que não escrever métrica zero quando `has_metric` é falso:** um
candidato sem NENHUMA métrica reportada (ex.: resultado de busca que só
trouxe metadado, sem stats) não deve gerar uma linha de métricas toda
`NULL` — isso só poluiria `latest_for_segment` sem ganho; o LEFT JOIN já
trata "sem linha" como "sem métrica disponível ainda".

**TDD:** teste real (`@requires_infra`, mesmo padrão dos outros testes de
`test_repositories.py`) que faz `upsert_candidates` com um
`CandidateVideo(views=1000, likes=50, shares=None, ...)` e verifica, via
`latest_for_segment`, que o snapshot devolvido tem `views=1000, likes=50`
— **e** um segundo teste que confirma que a coluna `shares` ficou `NULL`
no banco (query direta, não via `VideoSnapshot`, porque `VideoMetrics`
hoje ainda colapsa `None` em `0` — isso só é corrigido na Fase G; o teste
desta fase deve verificar o dado crú em `video_metrics`, não o DTO).

- [x] RED: escrever os dois testes acima, confirmar que falham
  (`video_metrics` vazio após `upsert_candidates`).
- [x] GREEN: implementar o INSERT condicional acima.
- [x] Gates locais (`pytest`, `ruff`, `pyright`) verdes.
- [x] Commit: `fix(db): upsert_candidates grava snapshot em video_metrics (bug: scoring sempre rodava com métricas zeradas)`

### Task F2: `research_pipeline` persiste candidatos sociais pelo contrato do `collect()`

**Files:**
- Modify: `src/content_factory/app/research_pipeline.py`
- Test: `tests/app/test_research_pipeline.py`, `tests/app/test_research_pipeline_e2e.py`

**Contexto do bug:** em `run_research_cycle` (linhas 74-86), o loop sobre
`deps.socials.items()` chama `discoverer.discover_by_keywords(...)` e só
faz `social_found += len(candidates)` — os candidatos nunca chegam a
`deps.videos.upsert_candidates`. `ResearchDeps` **já** tem `videos:
VideoRepository` e `runs: CollectRunRepository` (campos existentes,
olhar `app/research_pipeline.py:19-29`) — a correção é usar o que já está
injetado, não adicionar dependência nova.

**Correção:** espelhar o loop de `app/collect.py:52-91` (reaproveitar o
mesmo vocabulário de contadores — `found`/`inserted`/`updated`/`skipped`/
`failed` — em vez de inventar `social_found`), e registrar um `CollectRun`
por plataforma social, com `collector` prefixado para diferenciar de
`collect()` no audit (`research:instagram`, por exemplo, já que
`CollectRun.collector` é uma `String` livre e `cf status` agrupa por
`status`, não por nome exato do collector).

```python
# app/research_pipeline.py — substituir o bloco atual do loop de socials
from content_factory.domain.models import CollectRun  # novo import

counts: dict[str, int | bool] = {
    "web_found": 0,
    "trends_found": 0,
    "signals_saved": 0,
    "social_found": 0,
    "social_inserted": 0,
    "social_updated": 0,
    "social_skipped": 0,
}
...
social_found = social_inserted = social_updated = social_skipped = 0
for platform, discoverer in deps.socials.items():
    if platform not in segment.platforms:
        continue
    started = datetime.now(UTC)
    run = CollectRun(collector=f"research:{platform}", segment_id=segment.id, started_at=started)
    try:
        await deps.limiter.acquire(platform)
        candidates = await discoverer.discover_by_keywords(keywords_for_social, [], 1)
        stats = await deps.videos.upsert_candidates(segment.id, platform, candidates)
        run.status = "ok"
        run.found, run.inserted, run.updated, run.failed = (
            len(candidates),
            stats.inserted,
            stats.updated,
            stats.skipped,
        )
        social_found += len(candidates)
        social_inserted += stats.inserted
        social_updated += stats.updated
        social_skipped += stats.skipped
    except Exception as exc:
        run.status = "failed"
        run.error_code = getattr(exc, "code", "internal_error")
        counts[f"{platform}_failed"] = int(counts.get(f"{platform}_failed", 0)) + 1
    finally:
        run.finished_at = datetime.now(UTC)
        await deps.runs.record(run)
counts["social_found"] = social_found
counts["social_inserted"] = social_inserted
counts["social_updated"] = social_updated
counts["social_skipped"] = social_skipped
```

Precisa de `from datetime import UTC, datetime` no topo do arquivo (hoje
só importa `dataclass`/`field` — conferir antes de duplicar import).

**Idempotência:** já garantida de graça — `upsert_candidates` já faz
`ON CONFLICT DO UPDATE` por `(platform, platform_video_id)` (mesmo código
usado por `collect()`); reexecutar o ciclo de pesquisa não duplica vídeo.

**TDD:**
- `tests/app/test_research_pipeline.py`: trocar `_FakeSocial` (hoje
  devolve `[]` sempre) para devolver 2 `CandidateVideo` num teste novo, e
  usar `FakeRepos` (já importa `_FakeVideos` que registra
  `self.upserts`) para verificar que `deps.videos.upsert_candidates` foi
  chamado com os candidatos certos e que `counts["social_inserted"]`
  reflete o retorno do fake.
- `tests/app/test_research_pipeline_e2e.py`: o teste já tem `_SpyInstagram`
  que registra `received_keywords` — adaptar para também devolver
  `CandidateVideo` reais e confirmar no final que
  `deps.videos` (trocar por `_FakeVideos` ou spy equivalente) recebeu os
  candidatos.
- Caso de falha: um social que levanta `CollectorError` não deve impedir
  os outros nem quebrar o ciclo — teste já existe parcialmente no padrão
  de `except Exception` atual; ajustar para verificar que um `CollectRun`
  com `status="failed"` foi gravado (`deps.runs` — usar `_FakeRuns`, já
  existente em `tests/stubs.py`).

- [x] RED → GREEN nos dois arquivos de teste.
- [x] Gates locais verdes.
- [x] Commit: `fix(research): persiste candidatos sociais via upsert_candidates (mesmo contrato do collect) + CollectRun por plataforma`

### Task F3: observação histórica do Google Trends (`trend_observations`)

**Files:**
- Create: `alembic/versions/0004_trend_observations.py`
- Modify: `src/content_factory/infra/db/orm.py` (`TrendObservationRow`)
- Modify: `src/content_factory/app/ports.py` (`TrendObservationRepository`)
- Modify: `src/content_factory/infra/db/repositories.py`
  (`TrendObservationRepositoryImpl`)
- Modify: `src/content_factory/app/research_pipeline.py` (`ResearchDeps`,
  loop de trends)
- Modify: `src/content_factory/infra/queue/worker.py` (`create_app_deps`)
- Test: `tests/infra/test_repositories.py`, `tests/app/test_research_pipeline.py`

**Contexto do problema:** `run_research_cycle` converte cada `TrendPoint`
(que já tem `period_start`, `period_granularity`, `interest`) num
`KeywordSignal` reduzido a um peso único (`weight=p.interest/100`) e só
isso é persistido (`deps.signals.upsert_many`). A série temporal é
perdida — não dá pra reconstruir crescimento/queda de uma keyword ao
longo do tempo, só o último peso observado (e `upsert_many` faz
`ON CONFLICT DO UPDATE` por `(segment_id, keyword, source)`, ou seja,
**sobrescreve** a observação anterior).

**Correção:** manter o `KeywordSignal` como está (ele serve à descoberta/
expansão — "esta keyword é relevante o bastante pra alimentar a próxima
etapa") e ADICIONAR uma tabela de observação append-only para o histórico.

```python
# app/ports.py
class TrendObservationRepository(Protocol):
    async def record_many(self, segment_id: int, points: Sequence[TrendPoint]) -> int: ...
    async def history_for_keyword(
        self, segment_id: int, keyword: str, limit: int
    ) -> list[TrendPoint]:
        """Mais recentes primeiro — base para calcular crescimento na Fase G."""
        ...
```

```python
# infra/db/orm.py
class TrendObservationRow(Base):
    __tablename__ = "trend_observations"
    __table_args__ = (
        Index("ix_trend_obs_segment_keyword_period", "segment_id", "keyword", "period_start"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), index=True)
    keyword: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_granularity: Mapped[str] = mapped_column(String(8))
    interest: Mapped[float] = mapped_column(Float)
    collected_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

Migração 0004 (seguir exatamente o estilo de `0003_research_campaigns.py`):
`create_table` com essas colunas + FK `segment_id` + índice composto
`(segment_id, keyword, period_start)` (consulta mais comum: histórico de
uma keyword num segmento, ordenado por tempo). **Sem unique constraint** —
é série temporal append-only; o mesmo `(segment_id, keyword, period_start)`
pode legitimamente ser regravado pelo pytrends numa reexecução sem que
isso seja "duplicação" no sentido de erro (decisão: aceitar potencial
sobreposição aqui; deduplicar na leitura/Fase G se necessário, não gravar
`ON CONFLICT DO NOTHING` que mascararia mudança de valor retroativa do
próprio Google Trends).

`TrendObservationRepositoryImpl.record_many`: insert simples em loop
(sem `ON CONFLICT`, ao contrário de `KeywordSignalRepositoryImpl` —
motivo explicado acima).

```python
# app/research_pipeline.py — ResearchDeps ganha o campo
@dataclass
class ResearchDeps:
    ...
    trend_observations: TrendObservationRepository  # novo


# no corpo de run_research_cycle, depois de computar trend_points:
await deps.trend_observations.record_many(segment.id, trend_points)
```

`create_app_deps` (worker) e a composição da CLI (se `research start`
vier a precisar) ganham `TrendObservationRepositoryImpl(session)` na
montagem de `ResearchDeps` — mesmo padrão dos outros repositórios.

**TDD:**
- Teste de domain/repo real: `record_many` grava N linhas, `history_for_keyword`
  devolve em ordem (mais recente primeiro), duas chamadas com o mesmo
  `period_start` não se sobrescrevem (append real, não upsert).
- Teste de `research_pipeline`: com `_FakeTrends` devolvendo 2
  `TrendPoint`, `run_research_cycle` chama `trend_observations.record_many`
  com exatamente esses pontos (fake nova em `tests/stubs.py` ou inline no
  teste, seguindo o padrão de `_FakeSignalRepo` já existente no arquivo).

- [x] RED → GREEN (migração, ORM, porta, repo, wiring, testes).
- [x] `alembic upgrade head` + `alembic check` verdes. **Atenção**:
  `alembic/env.py` lê `get_settings().database_url` (env `DATABASE_URL`),
  não `TEST_DATABASE_URL` — dentro do devcontainer/compose `DATABASE_URL`
  já está setado pelo `docker-compose.yml`; rodar via
  `docker compose -f .devcontainer/docker-compose.yml exec python uv run alembic upgrade head`
  (os testes de repositório, por outro lado, criam a própria engine a
  partir de `TEST_DATABASE_URL` — ver `tests/infra/test_repositories.py` —
  são mecanismos independentes, não confundir os dois ao depurar).
- [x] Gates locais verdes (`pytest` precisa de `TEST_DATABASE_URL` exportado
  no ambiente onde rodar — exportar dentro do container, não no host, se o
  Postgres do compose só expuser a porta em `127.0.0.1`).
- [x] Commit: `feat(trends): trend_observations — série temporal do Google Trends separada do sinal de descoberta`

### Task F4: gates completos + docs + HANDOFF

- [x] Gates completos: `pytest` (com `TEST_DATABASE_URL` exportado no shell
  onde o comando roda — subir o compose, ver
  `docs/dev/dev-environment.md`), `ruff check`, `pyright`, `alembic check`
  (via `docker compose ... exec python`, que já tem `DATABASE_URL` setado
  — ver nota da Task F3), `bandit -r src`, `pip-audit`,
  `mkdocs build --strict`, teste de privacidade.
- [x] `docs/manual/operacao.md` ganha uma nota sobre o que mudou no
  envelope de `cf research status`/no `CollectRun` audit (novo prefixo
  `research:<platform>` nos registros de `collect_runs`).
- [x] `docs/HANDOFF.md`: nova entrada "Fase F — integridade do pipeline
  (2026-10-12)" descrevendo o bug de `video_metrics` nunca escrito como o
  achado principal, e os 3 problemas da revisão externa corrigidos.
  Atualizar a classificação do roadmap
  (`docs/ai-plans/2026-10-12-content-factory-melhorias-roadmap.md`) movendo
  Fase F de "a fazer" para concluída.
- [x] Commit final: `docs: Fase F completa — métricas persistidas, pesquisa social idempotente, Trends histórico`
