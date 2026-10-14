# Operação — serviços, logs e rotinas

Este capítulo cobre o dia a dia de quem **opera** a stack: subir os serviços
(base + worker), confirmar que estão de pé, achar os logs quando algo falha e
entender o cron do worker (horários BRT, envs e limites). A primeira subida
do zero (arquivos de ambiente, schema, primeira coleta) está no [Primeiro
passo](guide-start.md); o que acontece por dentro do `collect`: [Fluxo de
busca](fluxo-busca.md). Os comandos de status daqui são a base da rotina do
[Runbook do agente](agentes.md).

Convenção: compose sempre com `-f .devcontainer/docker-compose.yml`; a CLI
roda **dentro** do container `cf-python` via `docker exec cf-python uv run
cf …` (envelope JSON de uma linha, exit codes `0`/`2`/`1` — contrato em
[TRD CLI](../specs/trd-cli.md)).

## 1. Subir a stack

Serviços base (Postgres, Redis e o container `cf-python` da CLI):

```bash
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
```

Worker (perfil `worker` — não sobe no `up -d` acima, que só seleciona os
serviços nomeados; manter `--profile` nos exemplos deixa a intenção explícita):

```bash
docker compose -f .devcontainer/docker-compose.yml --profile worker up -d worker
```

Pré-condições do worker: `app.env` e `secrets.env` já criados (passo 2 do
[Primeiro passo](guide-start.md)). O worker é multi-segmento: os segmentos
processados saem de `active_segments()` por job — sem campanha ativa, os
jobs são noop. `COLLECT_SEGMENT` NÃO deve estar definido: a imagem nova sob
o compose antigo falha no startup com `RuntimeError` (guard da Task 1, ver
[experimento](experimento-7-dias.md)).

Detalhe que muda a rotina de atualização: o serviço `python` roda com o
repositório montado (`bind-mount` → código novo aparece sem rebuild), mas o
`worker` **não tem bind-mount** — o `src/` entra na imagem (target `worker`
do Dockerfile). Mudou código que o worker executa? Rebuild obrigatório
([§7](#7-parada-restart-e-atualizacao)).

## 2. Status dos serviços

```bash
docker compose -f .devcontainer/docker-compose.yml ps
```

Esperado: `postgres`, `redis` (e `worker`, se o perfil foi subido) com STATUS
`Up`. O compose não define healthcheck — o estado bom é `Up`, não `healthy`.

CLI de dentro do container `cf-python` (`container_name` fixo no compose):

```bash
docker exec cf-python uv run cf ping
docker exec cf-python uv run cf status --segment candles
```

Saídas esperadas (exit `0`):

```console
{"ok": true, "command": "ping"}
{"ok": true, "command": "status", "runs": {"ok": 1}}
```

`cf status` agrega os runs do segmento nos últimos 7 dias por status — leitura
com exemplos em [Primeiro passo §8](guide-start.md#8-status-operacional).

Confirmar que Postgres e Redis respondem de verdade (e não só o container
`Up`):

```bash
# revisão do alembic aplicada; saída vazia = nenhuma migração rodou
docker compose -f .devcontainer/docker-compose.yml exec python uv run alembic current

# PONG = Redis ok
docker compose -f .devcontainer/docker-compose.yml exec redis redis-cli ping
```

## 3. Logs

Três lugares, conforme o que falhou.

**Worker (cron e jobs):**

```bash
docker compose -f .devcontainer/docker-compose.yml --profile worker logs -f worker
```

O que procurar:

- `Starting worker for 3 functions: collect_job, score_job, research_job` — o worker subiu,
  conectou no Redis e registrou os 3 jobs. Atenção: essa linha é emitida
   **antes** do `on_startup`; para confirmar que o startup terminou, procure a
   **ausência** de traceback logo abaixo ou confirme o estado via `cf status`;
- **falha de job tratada** (`CollectorError` — plataforma bloqueada, cota,
  auth): capturada por plataforma dentro do `collect`, o job **retorna**
  normalmente e a sessão é commitada — auditada em `collect_runs` com
  `status`/`error_code` (queries em [Análise](analise.md));
- **exceção não tratada no job** (bug): capturada POR SEGMENTO dentro da task
  do TaskGroup e registrada no envelope
  (`segments: {slug: {ok: false, error, error_type}}`) — a UoW do segmento
  reverte sozinha (`session_scope`), as irmãs commitam normal. O arq marca
  o job como concluído (`●`) mesmo com `ok: false`: o sinal de falha é o
  registro `ERROR` com traceback no log (`segmento <slug> falhou em
  <collect|score|research>`) + o envelope — não o status do job. Os logs
  de `content_factory.*` saem no formato
  `<data hora> ERROR <logger> <mensagem>`, com cookies/tokens mascarados
  (`***`, `RedactingFilter`); `grep ' ERROR content_factory'` acha todos;
- **coletor com exceção fora do contrato** (bug, não `CollectorError`): a
  run fica `failed` com `error_code = internal_error` e as plataformas
  irmãs seguem. A causa NÃO vai para o banco (`collect_runs` só guarda o
  código); ela fica no log como `ERROR … discover fora do contrato:
  <slug>/<plataforma>` (ou `persistência fora do contrato` no research),
  com traceback;
- `RuntimeError` no startup (guard multi-segmento): `COLLECT_SEGMENT` está
  definido no ambiente — a imagem nova não roda sob o compose antigo de 3
  workers. Remova a env (troca pelo compose de worker único, só após
  2026-10-19 — ver [experimento](experimento-7-dias.md));
- `shutdown on SIGTERM ◆ … jobs complete …` — desligamento limpo via
  `stop`/`down`; sem essa linha, o processo foi morto e o lock Redis pode
  sobrar até o TTL de 30 min ([§6](#6-limitacoes-conhecidas)).

**CLI / coleta manual:** o erro aparece no envelope da própria saída
(`{"ok": false, …}`, ou exit `2` com `"partial": true`) — a CLI não escreve
arquivo de log. A auditoria durável é a tabela `collect_runs` (1 linha por
plataforma por execução): queries em [Análise](analise.md); leitura dos
códigos em [Primeiro passo §9](guide-start.md#9-primeiras-falhas-o-que-significam-os-exit-codes).

**Container `cf-python` (CLI, alembic, `uv`):**

```bash
docker logs cf-python
```

## 4. Worker e cron

O worker é um processo arq que **só** executa os 2 jobs agendados (não serve
a CLI manual); cron no fuso do projeto:

| Job | Horário (BRT) | Origem |
|---|---|---|
| `research_job` | 07:00 | fixo no código (`hour={7}`, `minute=0`) — só roda com campanha ativa ([§5](#5-campanhas-de-pesquisa-cf-research)) |
| `collect_job` | 08:00 e 20:00 | horas via `COLLECT_CRON_HOURS` (default `08,20`), minuto `0` |
| `score_job` | 08:30 e 21:30 | fixo no código (`hour={8, 21}`, `minute=30`) |

Fuso fixo `America/Sao_Paulo` (`ZoneInfo` no `WorkerSettings`) — os horários
não dependem do TZ da máquina. Os dois cron jobs têm `unique=True`: um
disparo pendente não é enfileirado duas vezes.

**Envs do worker** (`COLLECT_PLATFORMS`/`LIMIT` são relidas a cada
job, assim como `WORKER_MAX_SEGMENTS`; `COLLECT_CRON_HOURS` e
`COLLECT_MAX_CONCURRENCY` valem do carregamento
do processo — trocar exige restart do worker):

| Env | Default | Significado |
|---|---|---|
| `COLLECT_PLATFORMS` | vazio | vazio = plataformas declaradas do segmento |
| `COLLECT_LIMIT` | `10` | limite de vídeos por plataforma na coleta |
| `COLLECT_CRON_HOURS` | `08,20` | horas do collect em BRT; valor inválido cai no default |
| `COLLECT_MAX_CONCURRENCY` | `2` | teto de `discover()` simultâneos por collect (Task 7); ponto de partida para benchmark, não limite seguro universal; valor inválido ou < 1 cai em `2`/`1` |
| `WORKER_MAX_SEGMENTS` | `3` | teto de segmentos simultâneos por job (Task 11); lido a cada tick, trocar não exige restart; valor inválido ou < 1 cai em `3`/`1` |
| `PLATFORM_MAX_CONCURRENCY` | `tiktok=1` | teto de discovers simultâneos **por plataforma no processo**, entre segmentos (`plataforma=limite,…`); a env é SOMADA ao default, nunca remove o teto do TikTok; entrada inválida ou < 1 é ignorada; vale do startup (exige restart) |

Onde mudar: bloco `environment:` do serviço `worker` em
`.devcontainer/docker-compose.yml` (ou `.devcontainer/env/app.env`, que os
dois serviços leem via `env_file`) — depois, recrie o container:
`… --profile worker up -d worker`. Ver também [§6](#6-limitacoes-conhecidas)
sobre o `defaults.env`.

**`max_jobs=3` (1 por tipo de job):** cada segmento roda na SUA sessão Postgres
(1 UoW por segmento, revertida em exceção via `session_scope`); engine e
clientes Redis são compartilhados por serem task-safe. O `max_jobs=1` anterior
existia SÓ porque a sessão era compartilhada (quebraria com
`IllegalStateChangeError`). Dentro de cada job, os segmentos rodam concorrentes
sob TaskGroup + semáforo `WORKER_MAX_SEGMENTS` (default 3).

## 5. Campanhas de pesquisa (`cf research`)

O worker roda um `research_job` diário às 07:00 BRT, mas **só executa o
ciclo se houver campanha ativa** para o segmento. Sem campanha, o job
retorna `{"ok": true, "skipped": "sem campanha ativa"}` e não faz nada.

| Comando | O que faz |
|---|---|
| `cf research start --segment candles --weeks 4` | Inicia campanha de 4 semanas (default) |
| `cf research stop --segment candles` | Para a campanha (ativas → inativas) |
| `cf research status --segment candles` | Mostra se há campanha ativa + `ends_at` |

Exemplos:

```bash
# iniciar campanha de 6 semanas
docker exec cf-python uv run cf research start --segment candles --weeks 6

# conferir status
docker exec cf-python uv run cf research status --segment candles
# {"ok": true, "command": "research.status", "active": true, "ends_at": "2026-11-23T10:00:00+00:00"}

# parar antes do fim
docker exec cf-python uv run cf research stop --segment candles
```

A campanha expira sozinha quando `ends_at` passa — não é preciso `stop`
manual no fim do período. O `research_job` no worker filtra
`active == true AND ends_at > now()` ([§4](#4-worker-e-cron)).

### O que o research_job faz (Fase F)

1. **Web suggest** — expande `segment.keywords` via Google Suggest.
2. **Google Trends** — busca interesse histórico e grava a série
   temporal em `trend_observations` (append-only) + um `KeywordSignal`
   por ponto (descoberta).
3. **Social (IG/YT/TikTok)** — para cada plataforma do segmento, chama
   `discover_by_keywords` e **persiste os candidatos via
   `upsert_candidates`** (mesmo contrato do `cf collect`, idempotente).
   Cada plataforma gera um `CollectRun` auditável com
   `collector=research:<platform>` (prefixo para diferenciar do
   `collect()` no audit).

### Contadores do envelope

| Contador | Significado |
|---|---|
| `web_found` | sinais de keyword expandidos pelo web suggest |
| `trends_found` | pontos de trend convertidos em sinais |
| `signals_saved` | total de sinais gravados (web + trends) |
| `social_found` | candidatos descobertos pelos coletores sociais |
| `social_inserted` | candidatos novos inseridos em `videos` |
| `social_updated` | candidatos já existentes atualizados |
| `social_skipped` | candidatos revertidos pelo savepoint |
| `<platform>_failed` | falhas por plataforma social |

## 6. Limitações conhecidas

- **Google Trends e Suggest são scraping não-oficial** (`pytrends` e
  `suggestqueries.google.com`). Ambos usam circuit breaker **por tempo**
  (5 falhas consecutivas → cooldown de 72 h = 3 ciclos diários do research;
  `infra/collectors/breaker.py`, em memória porque o worker é único) e
  mapeiam erros para `RateLimited`/`NetworkError`/`UpstreamChanged`.
  **Atenção:** hoje um erro do Suggest/Trends (ou `circuit_open`) **aborta
  o research do segmento**. `expand`/`interest_over_time` não são
  protegidos, então a fase social nem roda e o envelope do segmento sai
  `ok: false`. É o que acontece com o Trends HTTP 400 (pendência no
  HANDOFF). Ver ADR-0005.
- **`defaults.env` não é montado no compose.** O arquivo
  `.devcontainer/env/defaults.env` é versionado, mas nenhum serviço o lê (o
  compose só monta `env/app.env` e o secret `secrets/secrets.env`). Os
  defaults que valem são os do **código**: `COLLECT_*` em
  `infra/queue/worker.py` e janelas de rate-limit em `infra/ratelimit.py`.
  Para alterar, use `environment:`/`app.env` — editar o `defaults.env` não
  muda nada em runtime.
- **Lock de 30 min por segmento.** Coleta em execução segura um lock Redis
  com TTL de 1800 s; enquanto ocupado, `cf collect` devolve
  `ja_em_execucao` (exit `1`) — não force. Se o worker morrer no meio da
  coleta, o lock expira sozinho em até 30 min (é o TTL cobrindo o processo
  morto sem `release`).
- **Worker falha rápido com compose antigo.** Se `COLLECT_SEGMENT` estiver
  definido, o startup morre com `RuntimeError` (guard multi-segmento, visível
  em `logs worker`, [§3](#3-logs)) — é proposital, não erro de config.

## 7. Parada, restart e atualização

Manter `--profile worker` nos comandos: o perfil é necessário quando o
serviço **não** é nomeado (ex.: um `up -d` geral sem argumentos); comandos
que nomeiam `worker` explicitamente funcionam sem o perfil nesta versão do
compose — os exemplos abaixo trazem o flag de propósito, para a seleção ser
explícita:

```bash
# parar só o worker
docker compose -f .devcontainer/docker-compose.yml --profile worker stop worker

# derrubar a stack inteira (worker incluso)
docker compose -f .devcontainer/docker-compose.yml --profile worker down

# subir de novo: base primeiro, worker em seguida
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
docker compose -f .devcontainer/docker-compose.yml --profile worker up -d worker
```

Rotina de atualização após `git pull`, conforme o que mudou:

| Mudou o quê | Comando |
|---|---|
| `src/` que o **worker** executa | `… --profile worker up -d --build worker` (sem bind-mount: código entra só via imagem) |
| `Dockerfile`/`uv.lock` (serviço `python`) | `… up -d --build python` e depois `… exec python uv sync` |
| migrações (alembic) | `… exec python uv run alembic upgrade head` |
| `secrets.env` (chaves novas) | `… --profile worker restart worker` (ou `up -d --force-recreate worker`) — o `up -d` normal **não** recria o container por causa do arquivo de secret mudado, e o worker só faz `set -a; . /run/secrets/app_secrets` no startup: sem restart, as envs antigas continuam valendo |

`up -d` é idempotente: recria só o que mudou (imagem, env, comando). `down`
remove os containers, mas **não** o volume `pgdata` do Postgres — não use
`down -v` na rotina.

---

Próximo passo: rotina diária passo a passo com os comandos deste capítulo:
[Runbook do agente](agentes.md).
