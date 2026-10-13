# Concorrência do pipeline — plano de implementação

> **Para quem for executar:** TDD sempre — RED antes de GREEN, um commit por Task.
> Gates antes de "pronto" em cada Task: `pytest`, `ruff`, `pyright` (strict, 0 erros),
> `alembic check`, `bandit`, `pip-audit`, `mkdocs --strict`.
> Na execução, copiar este arquivo para `docs/ai-plans/2026-10-13-content-factory-concorrencia-pipeline.md`
> (convenção `YYYY-MM-DD-<slug>.md`) e registrar o avanço em `docs/HANDOFF.md`.

## Context

O review externo apontou que `collect_job`/`score_job`/`research_job` já percorrem
todos os segmentos com campanha ativa (merge `a4faf04`), mas **sequencialmente** —
multi-segmento não é concorrência. O review também levantou risco de coleta
triplicada, por o compose do experimento ainda definir 3 workers.

**Verificação empírica feita antes deste plano (corrige a premissa do review):**
os 3 containers (`cf-worker-velas/sabonetes/resina`) rodam imagens criadas em
`2026-10-12 23:26`, **anteriores** aos commits multi-segmento (`c9f1e41`+, `2026-10-13 00:01`).
Dentro do container, `active_segments` tem **0 ocorrências** em
`/app/content_factory/infra/queue/worker.py`, que ainda lê
`os.getenv("COLLECT_SEGMENT")`; o md5 difere do arquivo no repo.

Conclusão: **não há coleta duplicada hoje**. O risco é latente — qualquer
`docker compose up --build` ou restart com a `main` atual passa a triplicar. Isso
rebaixa o P0 de "incidente em andamento" para "trava antes do próximo rebuild",
mas a trava continua sendo a primeira coisa a fazer.

Resultado pretendido: cota de API respeitada globalmente, coleta mais rápida por
concorrência de I/O limitada, sessões de banco isoladas por unidade de trabalho e
decisões de CPU baseadas em medição — sem comprometer o experimento em curso.

### Decisões que moldam o plano

- **Janela de deploy:** o experimento de 7 dias termina **~2026-10-19**. Todo o
  código, testes e compose novo ficam prontos durante a semana; a troca
  3 workers → 1 worker multi-segmento só é aplicada **depois do dia 19**. A Task 1
  é a única que toca o ambiente agora, e apenas para impedir rebuild acidental.
- **Escopo:** Etapas A, B e C completas.
- **Achados próprios incluídos:** cota Redis fragmentada, `RedisLock` sem token de
  posse, `score()` com um round-trip por vídeo.

### Mapa de pressão (o que é I/O, CPU, e o que não é gargalo)

| Etapa | Natureza | Modelo correto |
| --- | --- | --- |
| `discover()` YouTube, Instagram | I/O — `httpx.AsyncClient` | `asyncio` limitado (P1) |
| `discover()` TikTok | I/O — TikTokApi async generator | `asyncio` limitado (P1) |
| `web`, `web_suggest`, `trends_google` | I/O síncrono já em `asyncio.to_thread` | manter (P2) |
| `upsert_candidates`, `runs.record` | I/O Postgres, mesma sessão | sequencial, nunca concorrente na mesma sessão |
| `viral_score`, `relevance_of` | CPU trivial | nenhum paralelismo |
| `scores.upsert` por vídeo | I/O Postgres, N round-trips | **lote** (Etapa C) |

## Pré-requisito

Não rebuildar nem reiniciar `cf-worker-*` até a Task 1 estar mergeada. Conferir
antes de qualquer ação no ambiente:

```bash
docker exec cf-worker-velas-artesanais \
  grep -c active_segments /app/content_factory/infra/queue/worker.py   # espera 0
```

---

# Etapa 0 — Travar o risco operacional (P0)

## Task 1 — Guard de arranque contra o compose de 3 workers

Impede que a imagem nova suba sob o compose antigo. Se `COLLECT_SEGMENT` estiver
definido no ambiente, o worker multi-segmento falha no `startup` com mensagem
explicativa, em vez de silenciosamente coletar os 3 segmentos.

**Files:**
- Modify `src/content_factory/infra/queue/worker.py` — em `startup()`, antes de
  `create_app_deps`: se `os.getenv("COLLECT_SEGMENT")` estiver presente, levantar
  `RuntimeError` citando que o worker agora é multi-segmento e que o compose do
  experimento deve ser substituído. `shutdown()` já usa `ctx.get("app_deps")`, então
  a falha de startup propaga limpa (coberto por
  `test_startup_failure_then_shutdown_propagates_original_error`).
- Test `tests/infra/queue/test_worker.py` — `COLLECT_SEGMENT` setado via
  `monkeypatch.setenv` → `pytest.raises(RuntimeError)`; ausente → startup normal.

## Task 2 — Compose de worker único (preparado, não aplicado)

**Files:**
- Create `.devcontainer/docker-compose.multi-segmento.yml` — **um** service
  `worker` em `redis://redis:6379/0`, sem `COLLECT_SEGMENT`, mesmo `DATABASE_URL`
  e `env_file`. Cabeçalho documentando que ele substitui
  `docker-compose.experimento-7dias.yml` e **não deve ser aplicado antes de
  2026-10-19**.
- Modify `.devcontainer/docker-compose.experimento-7dias.yml` — comentário no topo
  marcando o arquivo como legado da janela até 2026-10-19 e apontando para o novo.
- Modify `docs/manual/experimento-7-dias.md` e `docs/HANDOFF.md` — procedimento de
  troca (parar os 3, subir 1, conferir cron) e a data de corte.

## Task 3 — Unificar cota e circuit breaker entre workers

Achado próprio: `RedisRateLimiter` é global **por banco Redis**. Com `/1`, `/2`,
`/3`, cada worker tem sua própria janela `cf:rl:youtube` (100/dia) e seu próprio
breaker `cf:cb:*` — a cota real compartilhada da API pode ser consumida em até 3×,
e um breaker aberto num worker não protege os outros. Isso já é verdade hoje.

Com worker único (Task 2) o problema desaparece por construção, mas a correção
precisa existir para o caso de voltarem a existir múltiplos processos (Etapa B em
escala, ou um futuro worker dedicado por plataforma).

**Files:**
- Modify `src/content_factory/config.py` — nova setting `rate_limit_redis_url`,
  default = `redis_url`. Operacionalmente aponta sempre para `/0`, independente do
  banco de fila.
- Modify `src/content_factory/infra/queue/worker.py` — em `create_app_deps`, um
  segundo `redis_client` no `AsyncExitStack` para limiter/breaker quando as URLs
  diferirem; `RedisRateLimiter` recebe esse cliente. Locks de segmento vão no
  mesmo cliente compartilhado (ver Task 9), porque lock por fila não protege nada.
- Test `tests/infra/test_ratelimit.py` — dois `RedisRateLimiter` sobre o **mesmo**
  `FakeRedis` compartilham janela e breaker; sobre `FakeRedis` distintos, não
  (documenta o modo de falha).
- Test `tests/infra/queue/test_worker.py` — URLs iguais ⇒ um cliente; diferentes ⇒
  dois, ambos fechados no `shutdown`.

---

# Etapa 1 — Baseline de desempenho (P1)

Sem baseline, "ficou mais rápido" é opinião. Esta etapa é pré-requisito das
Etapas A/B/C e não altera comportamento.

## Task 4 — Instrumentar duração por plataforma e por segmento

`CollectRun` já tem `started_at`/`finished_at`, `found`, `inserted`, `updated`,
`skipped`, `failed`, `error_code` e os `*_gate_hits` em `counts` — a maior parte do
baseline já é persistida. Falta separar **tempo de rede** de **tempo de
persistência** e expor o agregado.

**Files:**
- Modify `src/content_factory/domain/models.py` — em `CollectRun`, campos
  opcionais `discover_ms: int | None` e `persist_ms: int | None`.
- Modify `src/content_factory/app/collect.py` — medir `discover()` e
  `upsert_candidates()` separadamente com um clock injetável (seguir o padrão
  `clock: Callable[[], datetime]` de `ScoreDeps`; adicionar a `CollectDeps` com
  default, para não quebrar chamadas existentes).
- Create `alembic/versions/*_collect_run_timings.py` — duas colunas nullable.
  `alembic check` precisa passar.
- Modify `src/content_factory/infra/db/repositories.py` — `CollectRunRepositoryImpl.record`
  persiste os dois campos; estender `status_counts` ou adicionar um método de
  agregação (p95/média por plataforma e segmento).
- Modify `src/content_factory/cli.py` (ou módulo equivalente) — subcomando de
  relatório devolvendo só o envelope `{"ok": ...}`, dados no banco.
- Test `tests/app/test_collect.py` — clock fake avança em `discover` e em
  `upsert_candidates`; asserta os dois campos separados no `CollectRun` gravado.
- Test `tests/infra/test_repositories.py` — round-trip das colunas (`requires_infra`).

## Task 5 — Capturar o baseline do experimento em curso

Sem alterar containers: ler o Postgres do experimento e congelar os números de
referência (duração por plataforma/segmento, contagens, `failed`/`blocked`, gate
hits) em `docs/manual/baseline-concorrencia.md`, com a janela de datas e a nota de
que vêm do código **pré-concorrência**. As colunas da Task 4 ficam nulas para esse
período — esperado e documentado.

---

# Etapa A — Concorrência de I/O na descoberta (P2)

Objetivo: dentro de um segmento, as descobertas de plataformas diferentes correm
em paralelo; **a persistência continua sequencial na sessão compartilhada**.

## Task 6 — Separar `collect()` em fase de rede e fase de persistência

Refatoração sem mudança de comportamento, para dar uma fronteira à concorrência.
`collect()` passa a: (1) adquirir lock, (2) resolver segmento, (3) para cada
plataforma, `limiter.acquire` + `discover` → resultado ou erro, (4) persistir
`upsert_candidates` + `runs.record` em sequência, (5) liberar lock.

Semântica a preservar exatamente, verificada contra `src/content_factory/app/collect.py`:
- `CollectOutcome.counts` com `found`/`inserted`/`updated`/`failed`/`skipped` e as
  chaves `{platform}_gate_hits` por delta (o collector é singleton no registry).
- `CollectRun.status` = `"blocked"` quando `exc.code == "blocked"`, senão `"failed"`;
  `run.failed = 1` no erro.
- Só `CollectorError` é tratado dentro de `collect()`; outras exceções sobem para o
  try/except por segmento do worker.
- `partial` = `not ok and inserted + updated > 0`.
- `release` do lock no `finally`.
- Nenhum `commit` dentro de `collect()` — quem commita é o caller.

**Files:**
- Modify `src/content_factory/app/collect.py` — extrair
  `_discover_one(collector, segment, limit, deps) -> DiscoverResult`, onde
  `DiscoverResult` carrega `platform`, `found | None`, `error: CollectorError | None`,
  `gate_hits_delta`, `discover_ms`. Fase de persistência consome a lista.
- Test `tests/app/test_collect.py` — testes atuais passam sem alteração de asserts.

## Task 7 — Concorrência limitada com `TaskGroup` + semáforo

**Files:**
- Modify `src/content_factory/app/collect.py` — `asyncio.TaskGroup` com
  `asyncio.Semaphore(deps.max_concurrent_discoveries)`. Cada task captura
  `CollectorError` na **própria fronteira** e devolve `DiscoverResult` — nenhuma
  exceção esperada escapa, para não cancelar as tasks irmãs. `CollectDeps` ganha
  `max_concurrent_discoveries: int = 2` (ponto de partida para benchmark, não
  limite seguro universal).
- Modify `src/content_factory/infra/queue/worker.py` — `_load_settings()` lê
  `COLLECT_MAX_CONCURRENCY` (default 2) e injeta nos deps.
- Modify `.devcontainer/env/app.env.example` + `docs/manual/*` — nova env.
- Test `tests/app/test_collect.py`:
  - ordem de persistência é **determinística** (ordem de `deps.collectors`), mesmo
    com `discover` terminando fora de ordem — garante diffs de dados estáveis;
  - falha parcial: 1 plataforma com `CollectorError`, as outras persistem; `counts`
    e `CollectRun`s idênticos ao caminho sequencial;
  - concorrência máxima respeitada: `FakeCollector` registra entrada/saída e
    asserta pico ≤ 2 (estender `tests/stubs.py` com um collector instrumentado);
  - exceção **não**-`CollectorError` em uma plataforma propaga e não deixa task
    pendente nem lock retido;
  - cancelamento externo: lock liberado (`FakeLock` já falha de verdade se não
    houver `release`);
  - `RateLimited` com janela estourada continua virando `failed`, sem bater na API.

## Task 8 — Mesma concorrência no pipeline de pesquisa

`run_research_cycle` tem o mesmo loop sequencial por rede social.

**Files:**
- Modify `src/content_factory/app/research_pipeline.py` — aplicar o padrão da
  Task 7 ao fan-out de `socials`, respeitando o `FanoutGuard` por segmento. O guard
  passa a ser consultado **antes** de agendar a task, não dentro dela.
- Test `tests/app/test_research_pipeline.py` — concorrência limitada, falha parcial
  por rede, e `FanoutGuard` ainda cortando o fan-out no mesmo ponto.

---

# Etapa B — Sessão por unidade de trabalho e concorrência entre segmentos (P3)

Só começar com a Etapa A verde e o baseline comparado. É aqui que o
`max_jobs = 1` deixa de ser necessário.

## Task 9 — `RedisLock` com token de posse (fencing)

Achado próprio. `release()` hoje é `DEL` incondicional. Com concorrência real: se o
TTL de 30 min do worker A expirar e B adquirir o mesmo lock, o `release` de A
**apaga o lock de B** e dois processos coletam o mesmo segmento. Hoje é inofensivo
(1 job por vez); na Etapa B é um bug de corretude e precisa vir **antes** dela.

**Files:**
- Modify `src/content_factory/infra/lock.py` — `acquire_once` gera um token
  (`secrets.token_hex`) e faz `SET NX PX`; `release` só apaga se o valor for o
  token, via script Lua (`EVAL` com CAS) — `GET` + `DEL` não é atômico. Manter a
  assinatura do port `Lock` (`acquire_once(key) -> bool` / `release(key)`) guardando
  o token por chave na instância, para não vazar o conceito para `app/`.
- Modify `src/content_factory/infra/redis_ops.py` — adicionar `eval`/`script` ao
  Protocol `RedisOps`, mantendo o estilo `def -> Awaitable[T]` exigido pelo pyright
  strict.
- Modify `tests/stubs.py` — `FakeRedis` suporta o CAS.
- Test `tests/infra/test_lock.py` — release de lock expirado-e-readquirido **não**
  apaga o do novo dono; release do dono apaga; release sem posse é no-op.

## Task 10 — Engine compartilhado + sessão por unidade de trabalho

`get_session_factory` (`src/content_factory/infra/db/base.py`, 8 linhas) cria um
**engine novo a cada chamada**. Chamá-la por segmento vazaria pools. Precisa de um
engine único com uma factory reutilizável.

**Files:**
- Modify `src/content_factory/infra/db/base.py` — `create_engine_and_factory(settings)`
  devolvendo `(engine, async_sessionmaker)`, e um `asynccontextmanager` de sessão
  com commit/rollback. `get_session_factory` permanece como wrapper fino, para não
  quebrar `tests/infra/queue/test_worker.py`, que monkeypatcha
  `db_base.get_session_factory`.
- Modify `src/content_factory/app/ports.py` — novo Protocol `UnitOfWork`
  (`__aenter__`/`__aexit__`, `commit`, `rollback`) e um `SegmentDepsFactory`
  (`create_segment_deps(segment) -> ...`). Hoje commit/rollback só existem como
  `Callable` solto no `AppDeps` do worker.
- Modify `src/content_factory/infra/queue/worker.py` — `AppDeps` guarda engine +
  factory; os repositórios deixam de ser construídos no startup e passam a nascer
  por unidade de trabalho. O engine e os clientes Redis continuam compartilhados
  (são thread/task-safe); a **sessão nunca é**.
- Modify `src/content_factory/app/score.py` — converter `ScoreDeps` em
  `@dataclass` para permitir `dataclasses.replace`, como já é feito com
  `ResearchDeps` em `run_research_job`. Hoje é classe comum com `__init__`.
- Test `tests/infra/test_base.py` — duas sessões da mesma factory são distintas e
  compartilham um único engine; engine fechado no shutdown.
- Test `tests/infra/queue/test_worker.py` — cada segmento recebe uma sessão
  própria; commit de um não depende do outro.

## Task 11 — Segmentos concorrentes, uma transação cada

**Files:**
- Modify `src/content_factory/infra/queue/worker.py` — nos 3 jobs, trocar o loop
  `for segment in active` por `TaskGroup` + semáforo (`WORKER_MAX_SEGMENTS`,
  default 3), cada segmento com sua própria sessão/transação. Preservar o envelope
  exato `{"ok": all(...), "segments": {slug: {...}}}` e o isolamento de exceção por
  segmento introduzido em `290a226` — sucesso, parcial e falha continuam
  distinguíveis, e nenhum segmento pode ser silenciosamente abandonado.
- Modify `WorkerSettings` — `max_jobs` pode subir de 1, **só porque** a sessão
  deixou de ser compartilhada. Atualizar o comentário load-bearing que hoje
  justifica o `1` (`IllegalStateChangeError`) e o teste
  `test_workersettings_max_jobs_is_one`, que vira
  `test_workersettings_max_jobs_allows_concurrency`.
- Modify `on_job_end` — o `job_end` atual faz rollback na sessão global; com
  sessão por unidade de trabalho isso perde o objeto e precisa sair ou mudar de
  alvo.
- Test `tests/infra/queue/test_worker.py` — 3 segmentos concorrentes com
  concorrência limitada; falha de 1 não reverte os commits dos outros; teto global
  de concorrência de plataforma respeitado **somando** os segmentos (o limiter
  Redis da Task 3 é quem garante isso).

---

# Etapa C — Lote e paralelismo de CPU (P4)

## Task 12 — Medir antes de paralelizar

Perfilar `score()` com dados reais do experimento: tempo em
`latest_for_segment()`, em `viral_score()` e nos `upsert()`; número de vídeos por
execução. Registrar em `docs/manual/baseline-concorrencia.md`.

Expectativa, pela leitura de `src/content_factory/app/score.py` e de
`ScoreRepositoryImpl.upsert`: `viral_score` é CPU trivial e o custo real é **um
round-trip ao Postgres por vídeo** — `begin_nested()` + `flush()` por linha. Se a
medição confirmar, **não** há paralelismo de CPU a fazer, e Task 14 é descartada.

## Task 13 — `upsert` em lote no score

`video_scores` é append-only (sem `ON CONFLICT`), o que torna o lote direto.
O obstáculo é o SAVEPOINT por vídeo, que existe porque no Postgres um flush
quebrado envenena a transação inteira.

**Files:**
- Modify `src/content_factory/app/ports.py` — `ScoreRepository.upsert_many(rows)`,
  mantendo `upsert` para a CLI.
- Modify `src/content_factory/infra/db/repositories.py` — `upsert_many` com
  `begin_nested()` + `add_all` + **um** `flush` por bloco de N (ex.: 500); em falha
  do bloco, cair para linha-a-linha naquele bloco, preservando a garantia atual de
  que "repo em falha não derruba o lote".
- Modify `src/content_factory/app/score.py` — calcular todos os `ScoreResult`
  (puro) e persistir em lote. `ScoreOutcome.scored`/`ok`/`partial` mantêm a
  semântica atual.
- Test `tests/app/test_score.py` — lote todo ok; 1 linha ruim no meio ⇒ as demais
  persistem e `partial` é `True`; contagem de flushes cai (documenta o ganho).
- Test `tests/infra/test_repositories.py` — `upsert_many` com `requires_infra`.

## Task 14 — (condicional) paralelismo de CPU real

**Só executar se a Task 12 mostrar CPU como gargalo dominante.** Nesse caso:
extrair uma função pura que recebe e devolve dados simples (nunca `AsyncSession`,
cliente HTTP ou objeto de infra), e comparar `ProcessPoolExecutor` contra
`InterpreterPoolExecutor` (disponível no Python 3.14; `pyproject.toml` declara
`requires-python = ">=3.14"`) em benchmark registrado. Não introduzir pool por
haver múltiplos núcleos.

---

# Fila: manter `arq` + Redis

`insta_kb` é boa referência de **confiabilidade** (durabilidade, retry, DLQ,
idempotência, separação API/worker), não de paralelismo — seu worker também
consome uma mensagem por vez (`prefetch_count=1`, `pika.BlockingConnection`,
callback síncrono). Migrar para RabbitMQ agora seria mudança arquitetural sem
relação com o gargalo de I/O. Revisitar só se o pipeline passar a gerar milhares
de tarefas independentes por vídeo/keyword. Registrar essa decisão como ADR
(`docs/adr/`), ao lado de `0002-redis-arq-fila.md`.

---

# Verificação

**Por Task:** `pytest`, `ruff`, `pyright`, `alembic check`, `bandit`, `pip-audit`,
`mkdocs --strict` (CLAUDE.md). Testes de privacidade da spec §10 onde aplicável.

**Etapa A, fim a fim** — sem tocar nos containers do experimento, contra um
Postgres descartável:
```bash
TEST_DATABASE_URL=... uv run pytest -m "integration or e2e"
uv run cf collect --segment velas-artesanais --limit 5   # envelope {"ok": ...}
```
Comparar `discover_ms`/`persist_ms` em `collect_runs` com o baseline da Task 5.
Critério: duração total do segmento cai aproximadamente para o máximo das
plataformas em vez da soma, com `found`/`inserted`/`updated` equivalentes.

**Etapa B:** rodar `collect_job` com 3 segmentos ativos num banco de teste;
confirmar 3 transações independentes (um segmento falhando não reverte os outros)
e que o total de chamadas por plataforma respeita a janela do limiter somando os
segmentos.

**Etapa C:** comparar contagem de round-trips e tempo de `score_job` antes/depois
do lote, no mesmo volume de vídeos.

**Troca de ambiente (só após 2026-10-19):** parar os 3 workers, subir o worker
único com `docker-compose.multi-segmento.yml`, conferir que o guard da Task 1 não
dispara (sem `COLLECT_SEGMENT`), que os 3 crons registram e que
`active_segments()` devolve os 3 segmentos. Antes de parar os containers, exportar
os dados do experimento.
