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
[Primeiro passo](guide-start.md)) e o segmento de `COLLECT_SEGMENT` (default
`candles`) já carregado no banco — senão o container morre no startup
([§6](#6-limitacoes-conhecidas)).

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
  **ausência** de traceback logo abaixo (o fail-fast `SegmentNotFound` vem
  depois desta linha) ou confirme o estado via `cf status`;
- **falha de job tratada** (`CollectorError` — plataforma bloqueada, cota,
  auth): capturada por plataforma dentro do `collect`, o job **retorna**
  normalmente e a sessão é commitada — auditada em `collect_runs` com
  `status`/`error_code` (queries em [Análise](analise.md));
- **exceção não tratada no job** (bug): o job lança, `on_job_end` dá rollback
  na sessão — a linha de auditoria **não** persiste em `collect_runs`; o que
  sobra é o traceback no log do worker;
- `SegmentNotFound` no startup (fail-fast): o segmento de `COLLECT_SEGMENT`
  não existe no banco; o container morre com exit ≠ 0. Corrigir com
  `cf segment load` e subir de novo ([§6](#6-limitacoes-conhecidas));
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

**Envs do worker** (`COLLECT_SEGMENT`/`PLATFORMS`/`LIMIT` são relidas a cada
job; `COLLECT_CRON_HOURS` vale do carregamento do processo):

| Env | Default | Significado |
|---|---|---|
| `COLLECT_SEGMENT` | `candles` | slug do segmento coletado e pontuado pelo worker |
| `COLLECT_PLATFORMS` | vazio | vazio = plataformas declaradas do segmento |
| `COLLECT_LIMIT` | `10` | limite de vídeos por plataforma na coleta |
| `COLLECT_CRON_HOURS` | `08,20` | horas do collect em BRT; valor inválido cai no default |

Onde mudar: bloco `environment:` do serviço `worker` em
`.devcontainer/docker-compose.yml` (ou `.devcontainer/env/app.env`, que os
dois serviços leem via `env_file`) — depois, recrie o container:
`… --profile worker up -d worker`. Ver também [§6](#6-limitacoes-conhecidas)
sobre o `defaults.env`.

**`max_jobs=1` não é bug:** os jobs compartilham uma única sessão Postgres
montada no startup do worker; mais de um job em paralelo quebraria a sessão
(`IllegalStateChangeError`). Um job por vez, em série.

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

## 6. Limitações conhecidas

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
- **Worker falha rápido se o segmento não existir.** O startup carrega
  `COLLECT_SEGMENT` do banco; slug inexistente → `SegmentNotFound` e o
  container morre (visível em `logs worker`, [§3](#3-logs)). Antes de subir o
  worker: `docker exec cf-python uv run cf segment load segments/candles.yaml`.

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
