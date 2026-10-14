# Concorrência do pipeline — correções do review pós-merge

> **Para quem for executar:** TDD sempre — RED antes de GREEN, um commit por Task.
> Gates antes de "pronto" em cada Task: `pytest`, `ruff`, `pyright` (strict, 0 erros),
> `alembic check`, `bandit`, `pip-audit`, `mkdocs --strict`, privacidade (spec §10).
> Registrar o avanço em `docs/HANDOFF.md`. Branch sugerida: `fix/concorrencia-review`.

## Context

Review de 2026-10-14 sobre o PR #8 (`feat/concorrencia-pipeline`, plano
`2026-10-13-content-factory-concorrencia-pipeline.md`). Gates verdes (285 testes
com Postgres descartável, ruff/pyright 0, `alembic check` limpo) e o desenho
segue o plano — mas a verificação em **infra real descartável** (Postgres e Redis
efêmeros, nada nos containers do experimento) achou um bug confirmado e lacunas
que precisam fechar **antes** da troca 3 workers → 1 (após 2026-10-19).

### Evidência empírica do review

| Verificação | Resultado |
| --- | --- |
| 3 segmentos concorrentes via `create_segment_deps` real, 1 levantando | ✅ pico 3, segmento que falhou com 0 linhas, os outros 2 commitados |
| `upsert_many` 1200 linhas | ✅ 3 `INSERT` (1 por bloco de 500); 1 FK inválida em 500 → 499 salvas |
| Lua CAS do `RedisLock`, **instâncias distintas** | ✅ release tardio de A não apaga o lock de B |
| Lua CAS do `RedisLock`, **mesma instância** (topologia do worker) | ❌ release tardio de A **apaga** o lock de B |
| Log de produção 2026-10-13 11:00 UTC (velas) | `DDGSException: No results found` abortou o segmento inteiro |

Lacunas de teste que deixaram o bug passar: o `FakeRedis.eval` reimplementa o
Lua em Python (o script real nunca roda na suíte) e o teste de posse usa duas
instâncias de `RedisLock`, quando o worker compartilha **uma**.

### Decisões que moldam o plano

- **Janela:** Etapa 0 inteira pronta e mergeada **antes de 2026-10-19**. Nada
  aqui toca os containers `cf-worker-*` (seguem na imagem pré-merge). A troca de
  compose continua sendo a do plano anterior, só que depois desta Etapa 0.
- **Lock:** corrigir pela posse por UoW (instância nova de `RedisLock` por
  bundle) + invariante "uma instância = um dono" — não mudar o protocolo `Lock`
  para API por token. Motivo: o protocolo tem ~47 usos em src/tests; o desenho
  da Task 10 já diz "deps nascem por UoW", e o lock é a peça que ficou de fora.
- **Exceção fora do contrato no discover:** passa a virar run `failed`
  (`internal_error`) com log, em vez de cancelar as plataformas irmãs — paridade
  com o research, que já faz isso. Inverte de propósito o contrato do teste
  `test_collect_non_collector_error_propagates_and_releases_lock`.
- **Semântica de `CollectRun.started_at/finished_at`:** volta a cobrir rede +
  persistência (como antes do PR #8 e como o baseline congelado mede).
- **Teto por plataforma:** semáforo **por processo** (o worker único é o alvo);
  entre processos, a cota global continua no limiter Redis da Task 3.

### Mapa de severidade (review)

| # | Achado | Severidade | Etapa |
| --- | --- | --- | --- |
| 1 | `RedisLock` com token não protege na mesma instância | 🔴 | 0 |
| 2 | Exceção do web cancela as irmãs e derruba o segmento | 🟠 | 0 |
| 3 | Exceção por segmento sem log/traceback; arq conta sucesso | 🟠 | 0 |
| 4 | `started_at/finished_at` só cobrem persistência | 🟠 | 0 |
| 5 | Sem teto global por plataforma (até 3 Playwright simultâneos) | 🟡 | 0 |
| 6 | Estado de coletor singleton misturado entre segmentos | 🟡 | 1 |
| 7 | Falha de lote do score engolida; erro real perdido | 🟡 | 1 |
| — | SOLID/DRY/armadilhas de composição | refactor | 2 |

---

# Etapa 0 — Bloqueadores da troca 3 → 1 (antes de 2026-10-19)

## Task 1 — Testes de lock contra Redis real (RED do achado #1)

Primeiro a prova, depois a correção. Hoje nenhum teste executa o Lua.

**Files:**
- Modify `tests/conftest.py` — `requires_redis = pytest.mark.skipif(os.getenv("TEST_REDIS_URL") is None, ...)`,
  no mesmo molde de `requires_infra`.
- Create `tests/infra/test_lock_redis.py` (marcador `integration`), sobre
  `redis_client(os.environ["TEST_REDIS_URL"])` com chave única por teste (`uuid4`):
  - `test_lua_release_by_owner_deletes` — o script real apaga quando o token bate.
  - `test_lua_stale_owner_distinct_instances_keeps_new_owner` — TTL de A
    (`ttl_s=1`) expira, B readquire, `release` de A é no-op.
  - `test_stale_owner_same_instance_keeps_new_owner` — **mesma** instância
    adquire, TTL expira, readquire, primeiro `release` → o lock **continua**
    existindo. **Deve falhar hoje** (reproduzido no review).
- Modify `.github/workflows/ci.yml` — `TEST_REDIS_URL: redis://localhost:6379/15`
  (o service `redis` já existe no job; banco 15 para não colidir).
- Modify `docs/dev/dev-environment.md` — como subir Redis descartável para
  `TEST_REDIS_URL`, nunca apontando para o Redis do experimento.

O terceiro teste fica RED até a Task 2.

## Task 2 — Posse do lock por UoW + invariante de dono único

**Files:**
- Modify `src/content_factory/infra/queue/worker.py` — em `create_segment_deps`,
  `lock=RedisLock(self.lock_client)` **novo** em `collect` e `research` do
  bundle (o template deixa de carregar uma instância compartilhada). Guardar o
  cliente Redis compartilhado em `AppDeps.lock_client` (é task-safe; a
  instância de lock, com seu mapa de tokens, não é).
- Modify `src/content_factory/infra/lock.py` — invariante: `acquire_once` numa
  chave que **esta instância** já possui levanta `RuntimeError("RedisLock é de
  dono único por chave: ...")` em vez de sobrescrever o token. Docstring do
  módulo: "uma instância por unidade de trabalho".
- Test `tests/infra/test_lock.py` — reaquisição na mesma instância sem release
  levanta; após `release` volta a adquirir.
- Test `tests/infra/queue/test_worker.py` — dois bundles do mesmo `AppDeps`
  recebem instâncias de lock **distintas** (`is not`), ambas sobre o mesmo
  cliente.

Com isso o teste da Task 1 de mesma instância é reescrito para o cenário real
pós-fix (dois bundles) e passa; o de mesma instância vira o teste do
`RuntimeError`.

## Task 3 — Web collector traduz erros do DDGS para o contrato

**Files:**
- Modify `src/content_factory/infra/collectors/web.py`:
  - `DDGSException` com "No results found" → `[]` (busca vazia não é erro).
  - `RatelimitException` → `RateLimited("web", ...)`.
  - `TimeoutException` → `NetworkError("web", ...)`.
  - outras `DDGSException` → `UpstreamChanged("web", ...)`.
  - Importar de `ddgs.exceptions` (classes reais, não o proxy lazy).
- Test `tests/infra/collectors/test_web.py` —
  monkeypatch de `DDGS` levantando cada exceção → o tipo de `CollectorError`
  esperado; "No results found" → lista vazia.

## Task 4 — Exceção fora do contrato no discover vira run `failed`, não cancelamento

Defesa em profundidade: o próximo coletor que vazar exceção não pode derrubar
as plataformas irmãs (cota já gasta, nada persistido).

**Files:**
- Modify `src/content_factory/app/collect.py` — em `_discover_one`, após os
  `except CollectorError`, `except Exception as exc` → `DiscoverResult` com
  `error=CollectorError("internal_error", platform, str(exc))` (preservar o
  original em `__cause__`). `CancelledError` (BaseException) continua
  propagando. Remover o ramo `ExceptionGroup` de `collect()`, que fica
  inalcançável (mesmo estado do research).
- Modify `src/content_factory/app/research_pipeline.py` — remover o ramo
  `ExceptionGroup` morto (o próprio comentário o declara inalcançável).
- Test `tests/app/test_collect.py` — substituir
  `test_collect_non_collector_error_propagates_and_releases_lock` por
  `test_collect_unexpected_error_becomes_failed_run_and_siblings_persist`:
  `_BoomCollector` + 2 coletores ok → 2 runs `ok` persistidas, 1 `failed` com
  `error_code="internal_error"`, `partial=True`, lock liberado.
- Manter `test_collect_external_cancel_releases_lock` intacto.

## Task 5 — Log com traceback por segmento e envelope com tipo do erro

**Files:**
- Modify `src/content_factory/infra/queue/worker.py` — `logger =
  logging.getLogger(__name__)`; em `_gather_segments`,
  `logger.exception("segmento %s falhou em %s", segment.slug, job_name)` antes
  de montar o envelope; envelope ganha `"error_type": type(exc).__name__`.
  `_gather_segments` recebe `job_name` (collect/score/research).
- Passar a mensagem por `redact()` (`infra/redact.py`, o mesmo helper da CLI) antes de gravar em
  `error`, já que o envelope vai para o log do arq.
- Test `tests/infra/queue/test_worker.py` — `caplog`: segmento que levanta gera
  1 registro `ERROR` com `exc_info` e o slug; envelope tem `error_type`.
- Docs `docs/manual/operacao.md` — onde procurar falhas por segmento (o arq
  marca o job como `●` mesmo com `ok: false`; o sinal é o log `ERROR` e o
  envelope, não `j_failed`).

## Task 6 — `started_at/finished_at` voltam a cobrir rede + persistência

**Files:**
- Modify `src/content_factory/app/collect.py` — `DiscoverResult.started_at`
  (lido de `deps.clock()` **antes** de `limiter.acquire`); a fase de persistência
  usa `run.started_at = result.started_at`; `finished_at` continua no `finally`
  da persistência. `discover_ms`/`persist_ms` inalterados.
- Modify `src/content_factory/app/research_pipeline.py` — mesmo campo em
  `SocialDiscoverResult`; trocar `datetime.now(UTC)` por um `clock` injetável em
  `ResearchDeps` (paridade com `CollectDeps`).
- Test `tests/app/test_collect.py` e `tests/app/test_research_pipeline.py` —
  clock falso em sequência: `finished_at - started_at == discover_ms +
  persist_ms` (± o tempo entre fases, que no clock falso é zero).
- Docs `docs/manual/baseline-concorrencia.md` — nota: a partir desta Task a
  duração `finished_at - started_at` é comparável ao baseline congelado;
  runs geradas entre o merge do PR #8 e esta Task (nenhuma em produção, os
  workers seguem na imagem antiga) não seriam.

## Task 7 — Teto de concorrência por plataforma, compartilhado entre segmentos

Hoje o pico é `WORKER_MAX_SEGMENTS × COLLECT_MAX_CONCURRENCY` = 6 discovers,
com até 3 sessões Playwright do TikTok simultâneas (cada `discover` abre a sua).

**Files:**
- Create `src/content_factory/app/concurrency.py` — `PlatformSlots`: dict
  `platform → asyncio.Semaphore`, `slot(platform)` como async CM; plataforma sem
  teto configurado = sem semáforo (no-op).
- Modify `src/content_factory/app/collect.py` e `research_pipeline.py` —
  `CollectDeps.platform_slots`/`ResearchDeps.platform_slots` (default: vazio,
  sem teto); `_discover_one`/`_discover_social_one` envolvem `limiter.acquire` +
  `discover` em `async with deps.platform_slots.slot(platform)`.
- Modify `src/content_factory/infra/queue/worker.py` — uma **única** instância
  de `PlatformSlots` criada no startup e herdada por todos os bundles
  (`replace` mantém o objeto); env `PLATFORM_MAX_CONCURRENCY` (formato
  `tiktok=1,instagram=2`; inválido → ignora a entrada), default `tiktok=1`.
- Modify `.devcontainer/env/app.env.example` — documentar
  `PLATFORM_MAX_CONCURRENCY` (vale do startup).
- Test `tests/app/test_concurrency.py` — teto respeitado; plataforma sem teto não
  bloqueia.
- Test `tests/infra/queue/test_worker.py` — 3 segmentos concorrentes com
  `TrackedCollector("tiktok")` → pico de tiktok == 1, demais plataformas
  sobrepõem; os 3 bundles compartilham o mesmo `PlatformSlots` (`is`).

## Task 8 — Teste de integração da UoW concorrente real

Fecha a outra lacuna: hoje só fakes cobrem `create_segment_deps`.

**Files:**
- Create `tests/infra/queue/test_worker_integration.py` (`requires_infra` +
  `requires_redis`) — `create_app_deps` sobre `TEST_DATABASE_URL`/
  `TEST_REDIS_URL`, 3 segmentos novos (slugs com `uuid4`), campanha ativa via
  `deps.campaigns.start`, `_collect_impl` monkeypatchado gravando 2 candidatos +
  1 run pelo **bundle** e dormindo 0,2 s; o segmento `-b` levanta depois de
  gravar. Asserções: pico 3; `video_segments` = 2/0/2; envelope com o erro de
  `-b`; `campaigns.stop` no teardown. (Mesmo roteiro executado à mão no review.)
- Não registrar coletores reais: `register_defaults` não pode fazer rede no
  teste — confirmar que construir os coletores não abre conexão.

## Review da implementação da Etapa 0 (2026-10-14)

Tasks 1–8 implementadas na branch `fix/concorrencia-review` (commits
`978d984`..`8035758`, 1 por Task). Gates reexecutados com infra descartável
(`cf-test-pg`/`cf-test-redis`): **297 passed, 0 skip**; `alembic check`
limpo; ruff/format, pyright 0; bandit 0; pip-audit sem vulnerabilidades;
`mkdocs --strict` ok; privacidade ok (210 arquivos). Todos os critérios de
saída da Etapa 0 (abaixo) estão cobertos por teste. Os 7 achados do review
original estão endereçados (🔴 #1 por T1/T2, 🟠 #2–#4 por T3–T6, 🟡 #5 por T7).

Achados novos, verificados empiricamente:

| # | Sev. | Achado | Evidência |
| --- | --- | --- | --- |
| A1 | 🟠 | T4 transforma bug em `internal_error` **mudo**: `CollectRun` não tem mensagem, `_discover_one`/`_discover_social_one` não logam e o `__cause__` se perde. Antes, a exceção propagava e (com T5) teria traceback. | `collect.py:117-155`; `CollectRun` só tem `error_code` |
| A2 | 🟠 | `logger.exception` em `_gather_segments` grava mensagem e traceback **sem `redact()`**, e só o envelope é redigido. Isso viola spec §7 (`infra/redact.py`). | Simulado com `default_log_config` do arq: `RuntimeError: boom ms_token=SECRET123` em claro no stderr |
| A3 | 🟡 | Nenhum handler para `content_factory.*`: o arq configura só o logger `arq`. Por isso o registro sai pelo `logging.lastResort`, sem timestamp nem nível, e procurar "ERROR" no log (como diz `operacao.md`) não acha nada. | mesma simulação: saída só `segmento velas falhou em collect` + traceback |
| A4 | 🟡 | Bloqueio na fila: o semáforo do segmento é adquirido **antes** da vaga global da plataforma. Um TikTok esperando vaga ocupa 1 dos `COLLECT_MAX_CONCURRENCY` do segmento. Afeta só vazão, sem deadlock. | `collect.py:188-190` + `_discover_one`; idem research |
| A5 | 🟡 | `_platform_max_concurrency_from_env` sem teste do parser (entrada inválida, vazia, limite 0, default). | `grep` em `tests/` |
| A6 | 🟡 | `_discover_one` repete 4 vezes a mesma construção de `DiscoverResult`, cerca de 60 linhas (DRY). | `collect.py:103-155` |
| A7 | 🟡 | O teste de integração da T8 só para as campanhas: segmentos, vídeos e runs ficam acumulados no banco descartável. Os dois testes de "instâncias distintas" em `test_lock_redis.py` são quase duplicados. | `test_worker_integration.py` teardown |

## Task 8.1 — Causa do `internal_error` visível (A1), antes do swap

> ✅ Feita em `9db64ea`. `logger.exception` (ERROR) nos pontos de conversão;
> no research só para não-`CollectorError` (discover e persistência).

**Files:** `app/collect.py`, `app/research_pipeline.py`, testes em `tests/app/`.
- Ao converter uma exceção fora do contrato em `internal_error` (collect) ou
  em run `failed` (research), registrar `logger.warning(..., exc_info=exc)`
  com o segmento e a plataforma (logging na camada app é permitido; `domain/`
  segue puro).
- RED: `caplog` com coletor levantando `KeyError` → 1 registro com
  `exc_info` e plataforma; irmãs persistem (o teste da T4 continua verde).

## Task 8.2 — Log do worker redigido e com formato (A2 + A3), antes do swap

> ✅ Feita. Desvio do texto abaixo: `propagate` ficou **ligado**. O root não tem
> handler no worker, então não há duplicata, e o `caplog` dos testes continua
> funcionando. `configure_logging()` roda no início do `startup`, antes do guard.
> Verificado com o `default_log_config` do arq: linha `… ERROR
> content_factory.app.collect …` e `ms_token=***` no traceback.

**Files:** `infra/redact.py` (novo `RedactingFilter(logging.Filter)`),
`infra/queue/worker.py` (`startup` instala handler com o filtro no logger
`content_factory`, formato `%(asctime)s %(levelname)s %(name)s %(message)s`,
`propagate=False`), `docs/manual/operacao.md`.
- O filtro aplica `redact()` em `record.getMessage()` **e** no traceback
  formatado (`record.exc_text`, preenchido via `Formatter.formatException`).
- RED: `caplog`/handler de teste com exceção contendo `ms_token=X` → nenhum
  `X` no texto final; o nível `ERROR` aparece na linha.

A4–A7 ficam para a Etapa 2 (A4/A6 na Task 12 `bounded_gather`: adquirir a
vaga da plataforma antes do semáforo do segmento e montar o resultado num
único ponto; A5/A7 como miudezas da Task 16).

---

# Etapa 1 — Correções de semântica (pode entrar após 2026-10-19)

## Task 9 — Contagem de `gate_hits` por chamada, não por instância

> ✅ Feita. `app/gate_hits.py` (`counting()`/`record_gate_hit()`); o campo virou
> `gate_hits` (não é mais delta). RED confirmado: no código antigo, 2 segmentos
> concorrentes contavam 2 cada.

Com segmentos concorrentes sobre o mesmo coletor singleton, o delta
before/after de `self.gate_hits` soma hits de outros segmentos.

**Files:**
- Create `src/content_factory/app/gate_hits.py` — `ContextVar[list[int] | None]`
  com `record_gate_hit()` (no-op sem contador ativo) e `counting()` (CM que abre
  um contador novo e devolve o total). Cada task do TaskGroup copia o contexto,
  então o contador é por chamada.
- Modify `src/content_factory/infra/collectors/instagram.py` — chamar
  `record_gate_hit()` junto do `self.gate_hits += 1` (o atributo fica, outros
  leitores podem existir).
- Modify `src/content_factory/app/collect.py` e `research_pipeline.py` — trocar o
  delta por `with counting() as hits:` em volta do discover.
- Test — 2 segmentos concorrentes sobre o mesmo coletor fake que registra 1 hit
  por chamada → cada envelope conta 1, não 2.

## Task 10 — Breaker de `web_suggest`/`trends` por tempo, não por chamada

> ✅ Feita. `infra/collectors/breaker.CooldownBreaker`, compartilhado pelos 2
> providers; default 72 h (3 ciclos diários do research, o que "3 ciclos" valia
> com 1 worker por segmento); em memória (worker único), decisão no docstring.
> **Achado colateral:** erro do Suggest/Trends aborta o research do segmento
> (sem try em `expand`/`interest_over_time`), o que contradizia o `operacao.md`. A doc
> foi corrigida e o caso registrado como pendência.

O cooldown "em ciclos" decrementa a cada chamada: com 3 segmentos por tick ele
dura 1/3 do previsto, e falhas de segmentos diferentes se somam.

**Files:**
- Modify `src/content_factory/infra/collectors/web_suggest.py` e
  `trends_google.py` — `breaker_cooldown_s` + clock monotônico injetável
  (`time.monotonic` default); aberto enquanto `now < open_until`. Manter
  `breaker_threshold`. Default equivalente ao atual em tempo: 1 ciclo = 12 h
  (cron 2×/dia) → documentar o valor escolhido.
- Avaliar mover o breaker para o Redis compartilhado (mesma família de chaves
  `cf:cb:*` da Task 3 do plano anterior) — só se houver mais de um processo; com
  worker único, em memória basta. Registrar a decisão no docstring.
- Test — clock falso: abre após N falhas, fecha após `cooldown_s`; 3 chamadas
  concorrentes no mesmo instante não consomem o cooldown.

## Task 11 — Falha de lote do score propaga

> ✅ Feita. Worker: envelope com o erro original + rollback só da UoW (RED: era
> `{ok: false, scored: 0}` e commitava). CLI: já capturava `Exception` → agora
> `internal_error` com a mensagem real, não a genérica.

`except Exception: scored = 0` em `score()` esconde o erro real; o `commit`
seguinte levanta `PendingRollbackError` e só ele chega ao envelope.

**Files:**
- Modify `src/content_factory/app/score.py` — remover o `try/except` em volta de
  `upsert_many` (linhas ruins já são isoladas dentro do repo; o que sobra é erro
  de infraestrutura, que deve chegar ao worker, ser logado pela Task 5 e reverter
  a UoW).
- Test `tests/app/test_score.py` — reescrever
  `test_score_batch_connection_failure_scores_nothing` como
  `..._propagates`: `pytest.raises` com a exceção original.
- Test `tests/infra/queue/test_worker.py` — score de 1 segmento levantando →
  envelope com o erro **original**, não `PendingRollbackError`.

---

# Etapa 2 — Refactor de qualidade (SOLID/DRY), sem mudar comportamento

Cada Task aqui é refactor puro: a suíte existente é a rede de segurança; testes
novos só onde o refactor cria uma unidade nova.

## Task 12 — `bounded_gather` único

> ✅ Feita. `app/concurrency.bounded_gather` é usado em `collect()`,
> `run_research_cycle()` e `_gather_segments`, que agora devolve o envelope
> inteiro: o cálculo de `ok` repetido nos 3 jobs saiu. **A6:** `_discover_one`
> tem um único ponto de construção do `DiscoverResult`. **A4 reavaliado, sem
> mudança:** inverter a ordem (vaga da plataforma antes do semáforo do
> segmento) deixaria o TikTok (`tiktok=1`, gargalo do processo) **ocioso**
> esperando o segmento. Na ordem atual, quem espera é trabalho não-gargalo,
> que termina antes do fim do job. Exemplo: 3 segmentos × [tiktok 60 s,
> youtube 5 s, instagram 5 s], teto 2 por segmento, dá 180 s nas duas ordens;
> o tempo total é o TikTok serializado.

O padrão TaskGroup + semáforo + slots por índice aparece em `collect()`,
`run_research_cycle()` e `_gather_segments`.

- Create em `src/content_factory/app/concurrency.py`:
  `async def bounded_gather(items, limit, fn) -> list[R]` — ordem de entrada
  preservada; `fn` não deve levantar (contrato documentado); semáforo `max(1, limit)`.
- Usar nos três pontos. Test unitário do helper (ordem, teto, cancelamento externo).

## Task 13 — Camadas: bundle fora das portas, UoW enxuta (DIP/ISP)

> ✅ Feita. `app/segment_deps.py` com bundle + fábrica; `ports.py` sem
> `TYPE_CHECKING` dos use-cases; `tests/app/test_layers.py` trava a regra por AST.

- Move `SegmentDepsBundle` e `SegmentDepsFactory` de `app/ports.py` para
  `app/segment_deps.py` — `ports.py` deixa de importar os use-cases (hoje via
  `TYPE_CHECKING`, dependência invertida).
- `UnitOfWork` fica com `commit`/`rollback` apenas (ninguém consome
  `__aenter__`/`__aexit__`); `SessionUnitOfWork` perde o CM.

## Task 14 — Composição sem sessão de startup nem templates expostos

> ✅ Feita junto com a Task 15 (mesmo trecho de `create_app_deps`). `SharedDeps`
> frozen; `ctx` só com `app_deps` + `campaigns_deps`. `campaigns_deps` fica:
> é o repo de sessão fresca, não template. Testes: 0 sessões no startup,
> `set(ctx)` exato; o bloco de monkeypatch repetido em 6 testes virou
> `_patch_composition`, e os helpers de template mortos saíram.

Hoje `create_app_deps` abre uma sessão que nunca é usada, monta deps
"template" presos a ela (com `ScoreDeps.segment`/`guard` placeholder) e ainda os
publica em `ctx["collect_deps"|"score_deps"|"research_deps"]` — quem usar um
deles reintroduz o `IllegalStateChangeError` com `max_jobs=3`.

- `SharedDeps` (collectors, limiter, lock_client, platform_slots, providers web/
  trends, socials, clock, max_concurrency): só o que é task-safe.
- `create_segment_deps` monta `CollectDeps`/`ScoreDeps`/`ResearchDeps` inteiros
  a partir de `SharedDeps` + sessão da UoW — sem `replace` sobre template.
- `startup` injeta só `ctx["app_deps"]`; remover a sessão de startup.
- Test: `ctx` sem as chaves antigas; nenhuma sessão aberta no startup (fake
  factory conta aberturas = 0 até o primeiro job).

## Task 15 — Engine via `create_engine_and_factory`, sem seam por `kw["bind"]`

> ✅ Feita. `engine: AsyncEngine` (não opcional). `get_session_factory` fica
> só para a CLI.

- `create_app_deps` usa `create_engine_and_factory` (que hoje existe e o worker
  não usa); some o `isinstance` com `# pyright: ignore` e o `engine: ... | None`.
- O seam de teste passa a ser `db_base.create_engine_and_factory`
  (monkeypatch devolvendo `(fake_engine, fake_factory)`), atualizar os testes
  do worker que trocavam `get_session_factory`.

## Task 16 — Dividir `worker.py` e miudezas

> ✅ Feita. `infra/queue/composition.py` (`SharedDeps`, `AppDeps`,
> `_FreshSessionCampaigns` com `_campaign_repo`, `compose_app_deps`) **não lê
> env**: `worker.create_app_deps` lê `COLLECT_MAX_CONCURRENCY` e
> `PLATFORM_MAX_CONCURRENCY` e passa os valores, o que evita import circular.
> `worker.py` caiu de ~610 para ~335 linhas. O `or` do `rate_limit_redis_url`
> saiu na Task 14. **A5:** teste de caracterização do parser do env. **A7:**
> `_purge` no teardown da integração (contagem `t8-%` estável entre execuções)
> e removido o teste de lock quase duplicado. **Não feito:** a conversão
> `int(...)` do `RedisOps.eval` em `RedisLock.release`. O retorno já é
> descartado, então nenhum `Any` vaza, e a mudança não traria ganho.

- `infra/queue/composition.py`: `AppDeps`, `SharedDeps`, `create_app_deps`,
  `_FreshSessionCampaigns` (com um helper `_repo(session)` no lugar dos 4
  imports locais repetidos). `worker.py` fica com env, jobs e `WorkerSettings`.
- `create_app_deps`: remover `settings.rate_limit_redis_url or settings.redis_url`
  (o validator de `Settings` já resolve o default).
- `RedisOps.eval`: manter a união do stub, mas `RedisLock.release` converte o
  retorno com `int(...)` e o ignora explicitamente — o `Any` não vaza.

---

# Verificação

**Por Task:** os gates do topo. Integração com infra **descartável**:
```bash
docker run -d --rm --name cf-test-pg -e POSTGRES_USER=cf -e POSTGRES_PASSWORD=cf \
  -e POSTGRES_DB=cftest -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16
docker run -d --rm --name cf-test-redis -p 127.0.0.1:56379:6379 redis:7-alpine
export DATABASE_URL=postgresql+psycopg://cf:cf@127.0.0.1:55432/cftest
export TEST_DATABASE_URL=$DATABASE_URL TEST_REDIS_URL=redis://127.0.0.1:56379/15
uv run alembic upgrade head && uv run pytest -q -m "not e2e"
docker rm -f cf-test-pg cf-test-redis
```
Nunca apontar `TEST_*` para `content-factory-postgres-1`/`-redis-1` (experimento).

**Critérios de saída da Etapa 0:**
- `test_lock_redis.py` verde contra Redis real, incluindo o cenário de dois
  bundles do mesmo `AppDeps`.
- Teste de integração da Task 8 verde (UoW concorrente real).
- `collect()` com 1 coletor levantando exceção arbitrária persiste as irmãs.
- Log `ERROR` com traceback por segmento falho (`caplog`).
- `finished_at - started_at` ≈ `discover_ms + persist_ms` em runs novas.
- Pico de TikTok == 1 com 3 segmentos concorrentes.
- (Tasks 8.1/8.2) `internal_error` com traceback no log, e log do worker
  com nível/timestamp e sem segredo em claro.

**Troca de ambiente (inalterada, só após 2026-10-19 e após a Etapa 0):**
exportar dados do experimento → parar os 3 workers → subir
`docker-compose.multi-segmento.yml` → conferir guard sem disparo, 3 crons
registrados, `active_segments()` com 3 segmentos e, no primeiro tick, `ERROR`
ausente no log e `collect-timings` populando `discover_ms`/`persist_ms`.
Comparar com `docs/manual/baseline-concorrencia.md` usando
`discover_ms + persist_ms` (ver nota do review em `baseline-concorrencia.md`: `finished_at - started_at` inclui a espera pelas plataformas irmãs).

---

# Code review final (2026-10-14, `/code-review` sobre `main...refactor/concorrencia-etapa2`)

São 10 achados, cada um conferido no código:

| # | Achado | Veredito | Ação |
| --- | --- | --- | --- |
| 1 | `logger.exception` novo também roda na CLI, que não tinha handler → traceback sem redação no stderr | procede | `infra/logs.configure_logging` compartilhado; a CLI chama em WARNING |
| 2 | `DDGS.text` reembrulha o erro da engine em `DDGSException(err)` → ratelimit vira `upstream_changed` | procede (conferido na fonte do ddgs) | `web._classify` olha `exc.args[0]` e o texto (429/ratelimit/timeout) |
| 3 | research segue na mesma sessão após erro de banco no upsert → `PendingRollbackError` derruba o segmento | procede, **pré-existente** (o `except Exception` é anterior) | pendência da Etapa 1 (junto da T11): savepoint na persistência por plataforma |
| 4 | engine não descartado se o startup falha depois de criá-lo, ou se o close do Redis levanta | procede | `stack.push_async_callback(engine.dispose)` |
| 5 | `RedactingFilter` chama `getMessage()` fora da proteção do logging | procede | try/except, cai na mensagem crua (redigida) |
| 6 | `finished_at` inclui a espera pelas irmãs → duração não comparável ao baseline | procede (a T6 não resolvia) | métrica de comparação passa a ser `discover_ms + persist_ms` (doc do baseline) |
| 7 | `PLATFORM_MAX_CONCURRENCY` setado derruba o `tiktok=1`; `tiktok=0` = sem teto | procede | a env soma ao default; limite < 1 é ignorado |
| 8 | research ignora `COLLECT_MAX_CONCURRENCY` | **não é regressão** (sempre usou o default 2; a env é de collect) | nada |
| 9 | docstring de `run_collect_job` desatualizada | procede | reescrita |
| 10 | `found=None if error…` redundante; research com 2 returns | procede (menor) | um único return nos dois |
