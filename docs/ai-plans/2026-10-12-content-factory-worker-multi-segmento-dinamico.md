# Worker multi-segmento dinâmico — plano de implementação

> **Para quem for executar:** TDD sempre (RED confirmado antes do GREEN),
> commit por task. Não aplicar durante o experimento de 7 dias em
> andamento (3 workers separados via
> `.devcontainer/docker-compose.experimento-7dias.yml`) — esse plano é
> pra DEPOIS, quando testarmos lançar segmentos novos de verdade sem
> reiniciar nada.

**Goal:** o worker (`infra/queue/worker.py`) passa a processar **todos os
segmentos com campanha de pesquisa ativa** em cada tick de cron, em vez
de 1 único segmento fixo por processo (`COLLECT_SEGMENT`). Adicionar um
segmento novo à operação automatizada vira `cf segment load` +
`cf research start` — nenhum restart de worker, nenhuma env var nova.

**Por que isso é possível sem reescrever a fila:** o `arq` (e Redis) nunca
foram o limite — eles suportam múltiplos jobs com argumentos diferentes
sem problema. O limite é que `run_collect_job`/`run_score_job`/
`run_research_job` nunca recebiam "qual segmento" como dado de execução:
liam de uma env var fixa, e `AppDeps` era montado 1 vez no startup já
amarrado a 1 `Segment` (`ScoreDeps.segment`/`.keywords`,
`ResearchDeps.guard`). A fila sempre aguentaria N segmentos; a composição
é que não foi desenhada pra isso.

**Decisão de design:** campanha ativa (`research_campaigns.active AND
ends_at > now()`) passa a ser o único sinal de "este segmento está em
operação automatizada agora" — vale pros 3 jobs (collect/score/research),
não só pra pesquisa como hoje. Isso já é exatamente o fluxo que o usuário
roda na prática (`cf research start` por segmento antes de esperar o
worker agir).

## Pré-requisito

Nenhuma migração nova — `research_campaigns` já tem `segment_id`; só
precisa de uma query nova juntando com `segments`.

## Task 1 — `ResearchCampaignRepository.active_segments()`

**Files:**
- Modify: `src/content_factory/app/ports.py` (`ResearchCampaignRepository`)
- Modify: `src/content_factory/infra/db/repositories.py` (`ResearchCampaignRepositoryImpl`)
- Test: `tests/infra/test_repositories.py`

Novo método no port:

```python
class ResearchCampaignRepository(Protocol):
    async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign: ...
    async def stop(self, segment_id: int) -> None: ...
    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None: ...

    async def active_segments(self) -> list[Segment]:
        """Todos os segmentos com campanha ativa e não vencida (ends_at > now).

        Fonte única de "quais segmentos o worker deve processar neste
        tick" — vale pros 3 jobs (collect/score/research), não só pesquisa.
        """
        ...
```

Implementação — join direto com `segments`, reaproveitando o parse de
config que `SegmentRepositoryImpl.get_by_slug` já faz:

```python
async def active_segments(self) -> list[Segment]:
    stmt = (
        select(SegmentRow)
        .join(ResearchCampaignRow, ResearchCampaignRow.segment_id == SegmentRow.id)
        .where(
            ResearchCampaignRow.active,
            ResearchCampaignRow.ends_at > datetime.now(UTC),
        )
        .distinct()
    )
    rows = (await self._s.execute(stmt)).scalars().all()
    return [
        Segment(id=row.id, **SegmentConfig.model_validate(row.config).model_dump()) for row in rows
    ]
```

(`SegmentRow`/`SegmentConfig` já importados no arquivo — confirmar antes
de duplicar import. `.distinct()` evita duplicar segmento se um dia
existir mais de 1 campanha ativa pro mesmo segmento, mesmo que `start()`
hoje não previna isso explicitamente.)

Teste:

```python
@requires_infra
async def test_active_segments_returns_only_segments_with_active_campaign() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        with_campaign = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-ativo-{uuid4().hex[:8]}",
                name="Com campanha",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        without_campaign = await SegmentRepositoryImpl(s).upsert(
            SegmentConfig(
                slug=f"teste-sem-{uuid4().hex[:8]}",
                name="Sem campanha",
                platforms=["youtube"],
                keywords=["k"],
            )
        )
        repo = ResearchCampaignRepositoryImpl(s)
        await repo.start(with_campaign.id, datetime.now(UTC) + timedelta(weeks=1))
        await s.commit()

        active = await repo.active_segments()
        active_ids = {seg.id for seg in active}
        assert with_campaign.id in active_ids
        assert without_campaign.id not in active_ids
    await engine.dispose()
```

- [x] RED → GREEN
- [x] `pytest tests/infra/test_repositories.py -v`
- [x] Commit: `feat(research): ResearchCampaignRepository.active_segments para worker multi-segmento`

## Task 2 — `run_collect_job` processa todos os segmentos ativos

**Files:**
- Modify: `src/content_factory/infra/queue/worker.py`
- Test: `tests/infra/queue/test_worker.py`

Hoje: 1 chamada a `collect()` com `cfg["segment"]` fixo. Depois: loop
sobre `campaigns_deps.active_segments()`, 1 `collect()` por segmento,
commit por segmento (1 segmento com erro não reverte os outros — mesmo
princípio do savepoint por candidato já usado em `upsert_candidates`).

```python
async def run_collect_job(ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_settings()
    deps: CollectDeps = ctx["collect_deps"]
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()
    results: dict[str, Any] = {}
    for segment in active:
        platforms = cfg["platforms"] or segment.platforms
        out = await _collect_impl(segment.slug, platforms, cfg["limit"], deps)
        await app_deps.commit()
        results[segment.slug] = {"ok": out.ok, "partial": out.partial, **out.counts}
    return {"ok": all(r["ok"] for r in results.values()) if results else True, "segments": results}
```

`cfg["segment"]` (`COLLECT_SEGMENT`) deixa de ser lido aqui — mantém
`cfg["platforms"]`/`cfg["limit"]` como override global opcional (cai no
fallback `segment.platforms` quando vazio, igual hoje).

Teste novo (2 segmentos ativos processados no mesmo tick):

```python
async def test_run_collect_job_processes_all_active_segments() -> None:
    calls: list[str] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(slug)

        class _Out:
            ok = True
            partial = False
            counts: ClassVar[dict[str, object]] = {"found": 1}

        return _Out()

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(w, "_load_settings", lambda: {"platforms": [], "limit": 10})
    campaigns = _FakeCampaignsMultiSegment(slugs=["velas", "sabonetes"])
    committed: list[str] = []

    class _AppDeps:
        async def commit(self) -> None:
            committed.append("commit")

    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": _AppDeps()}
    )
    assert calls == ["velas", "sabonetes"]
    assert out["ok"] is True
    assert set(out["segments"]) == {"velas", "sabonetes"}
    assert committed == ["commit", "commit"]  # 1 commit por segmento


async def test_run_collect_job_noop_when_no_active_segment() -> None:
    campaigns = _FakeCampaignsMultiSegment(slugs=[])
    out = await w.run_collect_job(
        {"collect_deps": object(), "campaigns_deps": campaigns, "app_deps": _NoopAppDeps()}
    )
    assert out == {"ok": True, "segments": {}}
```

(`_FakeCampaignsMultiSegment` e `_NoopAppDeps` são fixtures novas no
arquivo de teste — ver Task 5.)

- [x] RED → GREEN
- [x] Commit: `feat(worker): run_collect_job processa todos os segmentos com campanha ativa`

## Task 3 — `run_score_job` processa todos os segmentos ativos

**Files:**
- Modify: `src/content_factory/infra/queue/worker.py`
- Test: `tests/infra/queue/test_worker.py`

`ScoreDeps` é barato de construir (sem I/O) — monta 1 por segmento dentro
do loop, reaproveitando os MESMOS `videos`/`scores` (repositórios ligados
à sessão compartilhada, não ao segmento):

```python
async def run_score_job(ctx: dict[str, Any]) -> dict[str, Any]:
    base: ScoreDeps = ctx["score_deps"]  # só videos/scores — ver Task 5
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()
    results: dict[str, Any] = {}
    for segment in active:
        deps = ScoreDeps(
            videos=base.videos,
            scores=base.scores,
            segment=segment,
            keywords=segment.keywords,
        )
        out = await _score_impl(deps)
        await app_deps.commit()
        results[segment.slug] = {"ok": out.ok, "scored": out.scored}
    return {"ok": all(r["ok"] for r in results.values()) if results else True, "segments": results}
```

- [x] RED → GREEN (mesmo padrão de teste da Task 2: 2 segmentos ativos,
  0 segmentos ativos, 1 commit por segmento)
- [x] Commit: `feat(worker): run_score_job processa todos os segmentos com campanha ativa`

## Task 4 — `run_research_job` processa todos os segmentos ativos

**Files:**
- Modify: `src/content_factory/infra/queue/worker.py`
- Test: `tests/infra/queue/test_worker.py`

Hoje: `campaigns.active_for_segment(1 segmento fixo)` — se `None`, skip.
Depois: `campaigns.active_segments()` já É a lista — sem campanha ativa,
lista vazia, loop não roda nenhuma vez (sem precisar de um `if` especial
"sem campanha ativa" como hoje).

`ResearchDeps.guard` é a única parte amarrada a 1 segmento
(`FanoutGuard(segment.research or DepthConfig())`) — troca via
`dataclasses.replace` por segmento, sem mudar a assinatura de
`run_research_cycle` nem de `ResearchDeps`:

```python
from dataclasses import replace


async def run_research_job(ctx: dict[str, Any]) -> dict[str, Any]:
    base: ResearchDeps = ctx["research_deps"]  # guard aqui é só placeholder — ver Task 5
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    app_deps: AppDeps = ctx["app_deps"]
    active = await campaigns.active_segments()
    results: dict[str, Any] = {}
    for segment in active:
        deps = replace(base, guard=FanoutGuard(segment.research or DepthConfig()))
        out = await _research_impl(segment.slug, DepthConfig(), deps)
        await app_deps.commit()
        results[segment.slug] = {"ok": out.ok, "partial": out.partial, **out.counts}
    return {"ok": all(r["ok"] for r in results.values()) if results else True, "segments": results}
```

- [x] RED → GREEN (2 campanhas ativas processadas num tick só; 0 ativas
  → `{"ok": True, "segments": {}}`, sem mais a chave `"skipped"` antiga —
  atualizar/remover `test_run_research_job_skips_without_active_campaign`
  pro novo formato)
- [x] Commit: `feat(worker): run_research_job processa todos os segmentos com campanha ativa`

## Task 5 — `create_app_deps` para de resolver 1 segmento no startup

**Files:**
- Modify: `src/content_factory/infra/queue/worker.py`
- Test: `tests/infra/queue/test_worker.py`

`create_app_deps` hoje faz `segment = await segments.get_by_slug(_load_settings()["segment"])`
e usa isso pra `ScoreDeps.segment`/`.keywords` e `ResearchDeps.guard`.
Como as Tasks 2-4 agora constroem esses 2 pontos **por segmento dentro do
loop**, o startup não precisa mais resolver nenhum segmento — monta
`ScoreDeps`/`ResearchDeps` com placeholders que o loop sempre substitui
antes de usar (`segment=None`-safe via `cast`, ou um `Segment` "vazio" só
pra satisfazer o tipo — **decisão**: adicionar `segment: Segment | None`
em `ScoreDeps`/manter `ResearchDeps.guard` com um `FanoutGuard(DepthConfig())`
neutro de placeholder, documentando que startup nunca usa esses campos
diretamente, só os jobs, que sempre reconstroem).

```python
async def create_app_deps(settings: Settings) -> AppDeps:
    ...
    try:
        client = await stack.enter_async_context(redis_client(settings.redis_url))
        session = await stack.enter_async_context(get_session_factory(settings)())
        register_defaults(settings)
        # Fase multi-segmento: não resolve mais 1 segmento fixo aqui —
        # cada job resolve os segmentos ativos por conta própria
        # (ResearchCampaignRepository.active_segments) e reconstrói
        # ScoreDeps/ResearchDeps.guard por segmento. Os campos abaixo são
        # placeholders nunca lidos diretamente fora do loop dos jobs.
        socials: dict[str, SocialDiscoverer] = {}
        for name in registry.snapshot():
            collector = registry.resolve([name])[0]
            if hasattr(collector, "discover_by_keywords"):
                socials[name] = collector  # type: ignore[assignment]
        return AppDeps(
            collect=CollectDeps(
                collectors=registry.resolve(list(registry.snapshot())),
                videos=VideoRepositoryImpl(session),
                segments=SegmentRepositoryImpl(session),
                runs=CollectRunRepositoryImpl(session),
                limiter=RedisRateLimiter(client),
                lock=RedisLock(client),
            ),
            score=ScoreDeps(
                videos=VideoRepositoryImpl(session),
                scores=ScoreRepositoryImpl(session),
                segment=None,  # placeholder — run_score_job substitui por segmento
                keywords=[],
            ),
            research=ResearchDeps(
                web=WebSuggestProvider(),
                trends=TrendsGoogleProvider(),
                socials=socials,
                signals=KeywordSignalRepositoryImpl(session),
                trend_observations=TrendObservationRepositoryImpl(session),
                segments=SegmentRepositoryImpl(session),
                videos=VideoRepositoryImpl(session),
                runs=CollectRunRepositoryImpl(session),
                limiter=RedisRateLimiter(client),
                lock=RedisLock(client),
                guard=FanoutGuard(DepthConfig()),  # placeholder — run_research_job substitui
            ),
            campaigns=ResearchCampaignRepositoryImpl(session),
            commit=session.commit,
            rollback=session.rollback,
            close=stack.aclose,
        )
    except BaseException:
        await stack.aclose()
        raise
```

`ScoreDeps.segment: Segment | None` exige ajuste de tipo em
`app/score.py` (hoje `Segment`, sem `| None`) — como `score()` só lê
`deps.segment.id`, e o job SEMPRE substitui `segment` antes de chamar
`_score_impl`, o `None` nunca chega lá de verdade; ainda assim o pyright
exige o tipo certo. Checar se compensa `Segment | None` com um `assert
deps.segment is not None` no topo de `score()`, ou se é mais limpo o job
nunca expor `ScoreDeps` "vazio" fora do próprio módulo (campo privado).
Resolver durante a implementação, com pyright strict 0 como critério.

Ajustar os testes de `test_worker.py` que hoje fixam `_FakeCampaigns`
(1 segmento) pra `_FakeCampaignsMultiSegment` (lista de slugs — resolve
pra `Segment` fake com esse slug, `platforms=["youtube"]`,
`keywords=["k"]`, `research=None`).

- [x] RED → GREEN
- [x] `pyright` — 0 erros (ponto de atenção do `Segment | None`)
- [x] Commit: `refactor(worker): create_app_deps para de amarrar 1 segmento no startup`

## Task 6 — gates completos + simplificar o compose do experimento

- [x] `pytest`, `ruff`, `pyright`, `bandit`, `pip-audit`, `mkdocs --strict`
- [x] `docs/HANDOFF.md` — registrar a mudança
- [ ] **ADIADO — fim do experimento de 7 dias:** trocar
  `.devcontainer/docker-compose.experimento-7dias.yml` (3 workers, 3
  índices de Redis) por 1 único `worker` (profile `worker` já existente)
  — adicionar segmento novo à operação passa a ser só `cf segment load`
  + `cf research start`, sem tocar em compose/env/restart.
- [ ] **ADIADO — pós-troca do compose:** Validar na prática: com o worker novo já no ar, rodar `cf segment
  load` + `cf research start --weeks 1` pra um 4º segmento SEM reiniciar
  o container, e confirmar que ele aparece no próximo tick de
  `collect_job`/`score_job`/`research_job` — esse é o teste de aceitação
  real que o usuário pediu ("lançar workers novos simultâneos").
