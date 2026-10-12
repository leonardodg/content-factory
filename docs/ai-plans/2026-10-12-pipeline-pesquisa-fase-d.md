# Fase D — Orquestração no worker + campanha

**Goal:** `run_research_cycle` (Fase A) passa a rodar de verdade via worker
arq, com um conceito de "campanha" em banco (`research_campaigns`) para
expressar "rodar diariamente por N semanas/meses" — o arq só tem cron
recorrente sem fim automático, então o estado de fim-de-campanha precisa
ser persistido e consultado no início de cada job.

**Pré-requisito:** Fase A mergeada (`run_research_cycle`/`ResearchDeps`
existem). Não depende de Fase B/C — pode registrar `ResearchDeps` com
providers ainda fake/stub se B/C não estiverem prontas (decisão: se Fase D
rodar antes de B/C neste roadmap, os providers reais entram depois, o job
já fica com a mecânica de orquestração pronta usando `socials={}` vazio
até lá — `run_research_cycle` já tolera isso, `counts["social_found"] = 0`).

### Task D1: `research_campaigns` — modelo + migração + repositório

**Files:**
- Modify: `src/content_factory/domain/models.py` (`ResearchCampaign`)
- Modify: `src/content_factory/app/ports.py` (`ResearchCampaignRepository`)
- Create: `alembic/versions/0003_research_campaigns.py`
- Modify: `src/content_factory/infra/db/orm.py` (`ResearchCampaignRow`)
- Modify: `src/content_factory/infra/db/repositories.py`
  (`ResearchCampaignRepositoryImpl`)
- Test: `tests/domain/test_models.py`, `tests/infra/test_repositories.py`

```python
# domain/models.py
class ResearchCampaign(BaseModel):
    id: int
    segment_id: int
    started_at: datetime
    ends_at: datetime
    active: bool = True
```

```python
# app/ports.py
class ResearchCampaignRepository(Protocol):
    async def start(self, segment_id: int, ends_at: datetime) -> ResearchCampaign: ...
    async def stop(self, segment_id: int) -> None: ...
    async def active_for_segment(self, segment_id: int) -> ResearchCampaign | None: ...
```

Migração: tabela `research_campaigns` (`id`, `segment_id` FK `segments.id`,
`started_at`, `ends_at`, `active` boolean default true), índice em
`(segment_id, active)` (consulta mais frequente: "existe campanha ativa
pra este segmento?").

`ResearchCampaignRepositoryImpl.active_for_segment`: `SELECT ... WHERE
segment_id=? AND active AND ends_at > now() ORDER BY started_at DESC
LIMIT 1` — campanha "ativa" mas vencida não conta (`stop()` não precisa
rodar via cron separado pra isso, a query já filtra por data).

TDD: testes de domain model (defaults), testes de repositório real
(`start` cria linha, `active_for_segment` devolve só se `ends_at` no
futuro, `stop` marca `active=false` e para de aparecer).

- [x] Steps 1-7 seguindo o padrão exato da Task 4 da Fase A (migração →
  ORM → repo → testes `@requires_infra` em `tests/infra/test_repositories.py`).
- [x] Commit: `feat(db): tabela research_campaigns + ResearchCampaignRepositoryImpl`

### Task D2: `research_job` no worker arq

**Files:**
- Modify: `src/content_factory/infra/queue/worker.py`

**Interfaces:**
- Consumes: `run_research_cycle`/`ResearchDeps` (Fase A),
  `ResearchCampaignRepository` (Task D1).
- Produces: `run_research_job(ctx) -> dict`, `AppDeps.research:
  ResearchDeps` — segue EXATAMENTE o padrão de `run_collect_job`/
  `create_app_deps` já existente.

```python
async def run_research_job(ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_settings()  # reaproveita segmento default já configurado
    deps: ResearchDeps = ctx["research_deps"]
    campaigns: ResearchCampaignRepository = ctx["campaigns_deps"]
    segment = await deps.segments.get_by_slug(cfg["segment"])
    campaign = await campaigns.active_for_segment(segment.id)
    if campaign is None:
        return {"ok": True, "skipped": "sem campanha ativa"}
    out = await _research_impl(cfg["segment"], DepthConfig(), deps)
    await ctx["app_deps"].commit()
    return {"ok": out.ok, **out.counts}
```

- `_research_impl = run_research_cycle` (hook de teste, mesmo padrão de
  `_collect_impl`/`_score_impl`).
- `create_app_deps` ganha a montagem de `ResearchDeps` (registry de
  `socials` = `{}` se Fases C/E ainda não mergeadas, ou os collectors reais
  já registrados se já existirem; `web`/`trends` = instâncias reais só
  depois das Fases B/C — até lá, usar um `KeywordExpander`/`TrendsProvider`
  "noop" mínimo em `infra/collectors/` que devolve lista vazia, pra não
  quebrar o worker enquanto as Fases B/E não chegam. Documentar essa
  decisão explicitamente no plano real desta task quando for escrita em
  `docs/ai-plans/`, porque depende de quais fases já estão mergeadas no
  momento da execução.)
- Novo cron: `cron(run_research_job, name="research_job", hour={7},
  minute=0, unique=True)` em `WorkerSettings.cron_jobs` — antes do
  `collect_job` das 08:00.

TDD: testes de `run_research_job` com `ctx` fake (mesmo padrão de
`test_worker.py` se existir, ou criar `tests/infra/test_worker.py` seguindo
o padrão de mock de `ctx`/ hooks trocáveis já estabelecido no módulo).

- [x] Steps de TDD completos (ler `infra/queue/worker.py` e o teste
  existente do worker antes de escrever, reaproveitando a estrutura de
  hooks `_collect_impl`/`_score_impl`).
- [x] Commit: `feat(worker): research_job + cron 07:00, campanha ativa consultada no startup do job`

### Task D3: CLI `cf research start/stop/status`

**Files:**
- Modify: `src/content_factory/cli/main.py`
- Test: `tests/cli/test_main.py` (ou equivalente já existente)

```python
@app.command("research")
def research_cmd(
    action: str = typer.Argument(..., help="start|stop|status"),
    segment: str = typer.Option(..., "--segment"),
    weeks: int = typer.Option(4, "--weeks", help="duração da campanha (start)"),
) -> None: ...
```

`start`: calcula `ends_at = now + timedelta(weeks=weeks)`, chama
`campaigns.start(segment.id, ends_at)`, envelope `{"ok": true, "command":
"research.start", "ends_at": ...}`. `stop`: `campaigns.stop(segment.id)`.
`status`: `campaigns.active_for_segment(segment.id)` → envelope com
`active`/`ends_at` ou `active: false`.

- [x] Steps de TDD completos (ler `cli/main.py` pra reaproveitar o padrão
  exato de `segment_cmd`/`status_cmd` já existentes, envelope
  `envelope_ok`/`envelope_err`, exit codes).
- [x] Commit: `feat(cli): cf research start/stop/status`

### Task D4: Gates + docs

- [ ] Gates completos.
- [ ] `docs/manual/` ganha um capítulo ou seção nova documentando
  `cf research start/stop/status` (seguir o estilo pt-BR do manual
  existente, `docs/manual/fluxo-busca.md` é a referência mais próxima).
- [ ] `docs/HANDOFF.md` atualizado, plano copiado pra `docs/ai-plans/`.
- [ ] Commit final.

> **Nota**: plano de intenção detalhada (não SDD travado como a Fase A) — leia os arquivos reais citados antes de executar e ajuste os detalhes finos; a arquitetura (Protocols, nomes, pontos de integração) é o que está fixado aqui.
