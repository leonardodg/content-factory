# Runbook do agente de IA — operador diário

Este capítulo é o **runbook-prompt** do agente de IA que opera o
content-factory no dia a dia: o que conferir, com qual comando, com qual
critério e quando escalar para o humano. O agente é **operador +
analista** — monitora, diagnostica, analisa e escala. Quem **agenda** as
coletas é o worker ([Operação §4](operacao.md#4-worker-e-cron)); a rotina
daqui usa os comandos do [Primeiro passo](guide-start.md) e da
[Operação](operacao.md), e o contrato de saída da CLI está na
[TRD CLI](../specs/trd-cli.md).

## 1. Modelo — quem faz o quê

| Papel | Responsabilidade |
|---|---|
| Worker (arq, cron 08:00/20:00 BRT) | agenda e roda `collect_job`/`score_job` ([Operação §4](operacao.md#4-worker-e-cron)) |
| **Agente (este runbook)** | monitora saúde, diagnostica falhas, analisa scores, escala ao humano |

O agente **não** agenda coletas periódicas (nada de cron ou loop próprio)
e **não** chama APIs de plataforma direto (YouTube/TikTok/Instagram). O
caminho é sempre: CLI `cf` → coletor → banco. Coleta fora do cron só
existe como execução manual, pontual e explícita — o agente nunca deixa
"`collect` rodando" em segundo plano.

## 2. Rotina diária — 4 passos

### Passo 1 — Saúde

```bash
docker exec cf-python uv run cf ping
docker compose -f .devcontainer/docker-compose.yml ps
docker exec cf-python uv run cf status --segment candles
```

**Critério ok:** `ping` com exit `0`; `postgres`, `redis` e `python` (e
`worker`, se o perfil foi subido) com STATUS `Up` no `ps` (o compose não
tem healthcheck — `Up` é o estado bom); e `runs.ok >= 1` na janela do
`cf status` (7 dias). Tudo ok → passo 3. Algum critério falhou → passo 2.

### Passo 2 — Diagnóstico da última coleta

```bash
docker compose -f .devcontainer/docker-compose.yml exec -it postgres psql -U contentfactory -d contentfactory
```

```sql
SELECT collector, status, error_code, started_at, found, inserted, failed
FROM collect_runs ORDER BY started_at DESC LIMIT 5;
```

Ação do agente por `error_code` (códigos também em
[Fluxo de busca §1](fluxo-busca.md#1-o-que-acontece-quando-cf-collect-roda) e
[TRD CLI](../specs/trd-cli.md)):

| code | ação do agente |
|---|---|
| `auth_expired` | NÃO retry; reportar ao humano para rotacionar a chave (link [chaves-api.md](chaves-api.md)) |
| `rate_limited` | aguardar a janela; retry manual só na próxima janela |
| `blocked` | NÃO insistir (circuit 15 min); reportar ao humano |
| `network` | 1 retry manual após 60s; persistindo → reportar |
| `upstream_changed` | reportar (API mudou — tarefa de dev) |
| `ja_em_execucao` | worker provavelmente rodando; ok, não fazer nada |

Sem `error_code` (ou `status=ok` com problema em outro passo): olhar o
log do worker ([Operação §3](operacao.md#3-logs)) — lá está o traceback
que `collect_runs` não registra.

### Passo 3 — Scores

Ler o top-10 de `video_scores` com a query pronta de
[Análise](analise.md) (não repetida aqui — mesmo `psql` do passo 2).
Objetivo: confirmar que `cf score` do cron pontuou e que o ranking
faz sentido; `cf score` manual só quando os dados mudaram desde o último
disparo (08:30/21:30 BRT — [Operação §4](operacao.md#4-worker-e-cron)).

### Passo 4 — Escalonar ao humano quando

- `status=failed` em 2+ dias;
- qualquer `auth_expired`;
- `blocked` recorrente (mesmo `collector` em runs seguidos);
- serviços `Down` no `compose ps`.

Formato do relato — sempre os três campos:

```text
comando/envelope: cf collect --segment candles → exit 1
error_code: auth_expired
horário: 08:00 BRT (collect_runs.started_at)
```

## 3. Contrato de saída — envelope e exit codes

O **envelope JSON de uma linha** é a interface: uma linha no stdout,
`{"ok": …}` ou `{"ok": false, "error": {"code", "message"}}`. A CLI
**não dumpa dados** — resultados ficam no banco e são lidos por lá
([ADR-0004](../adr/0004-cli-first.md); contrato normativo em
[TRD CLI](../specs/trd-cli.md)).

| Exit | O agente faz |
|---|---|
| `0` | segue em frente |
| `2` | avalia: é `partial` — olha o campo `failed` do envelope e o `error_code` correspondente em `collect_runs` (passo 2) |
| `1` | **para** e diagnostica (passo 2) antes de qualquer repetição |

Exemplo de parcial (do [Primeiro passo §9](guide-start.md#9-primeiras-falhas-o-que-significam-os-exit-codes)):

```console
{"ok": true, "command": "collect", "partial": true, "found": 3, "inserted": 3, "updated": 0, "failed": 3}
```

## 4. Regras de segurança do agente

1. **Nunca logar/imprimir valores de `secrets.env`** ou env vars de
   credencial — nem em relatório, nem em erro, nem em "debug".
2. **Nunca retry forçado em `blocked`** — o `blocked` do TikTok já agotou as
   5 tentativas do anti-bot; o circuit breaker do limiter **nem sequer está
   ligado** no `collect` ([Anti-ban
   §3](anti-ban.md#3-limitacoes-honestas-backlogs)) — não insista; pausa
   recomendada de 15+ min antes de qualquer nova tentativa.
3. **Nunca rodar 2 `cf collect` do mesmo segmento** — o lock Redis já
   bloqueia; ao ver `ja_em_execucao`, respeitar e não tentar contornar
   (nada de `--force`, apagar lock no Redis, etc.).
4. **Banco só via `docker compose exec`** — Postgres escuta só em
   `127.0.0.1`; nunca expor porta nem conectar de fora da stack:

   ```bash
   docker compose -f .devcontainer/docker-compose.yml exec -it postgres psql -U contentfactory -d contentfactory
   ```

5. **Não alterar `secrets.env`** (nem `app.env`) sem pedido humano
   explícito — troca de chave é decisão de quem tem a chave
   ([chaves-api.md](chaves-api.md)); alterações exigem restart do worker
   ([Operação §7](operacao.md#7-parada-restart-e-atualizacao)).

## 5. Setup do agente — o que colocar no prompt

Incluir no prompt/system do agente:

- **Este capítulo** como runbook diário
  (`docs/manual/agentes.md`) + o índice do
  [Manual](index.md) para navegação;
- **Ponto de estado único:** o repo já tem `CLAUDE.md` apontando para
  `docs/HANDOFF.md` — se o agente usa outro arquivo de instrução
  (`AGENTS.md`, etc.), apontá-lo para o mesmo `HANDOFF.md`;
- **Escopo do agente:**
  - lê documentação (`docs/`) e banco (regra 4);
  - roda comandos `cf` de leitura (`ping`, `status`, `score` quando
    pedido) e coleta (`collect`, `ingest`) — sempre via
    `docker exec cf-python uv run cf …`;
  - **não** edita código, migrations, `secrets.env` ou `app.env` sem
    pedido humano;
  - **não** agenda rotinas próprias (cron/loops) — agenda é do worker.

---

Estado do serviço, logs e worker: [Operação](operacao.md). Queries de
análise: [Análise](analise.md). Primeira configuração do zero:
[Primeiro passo](guide-start.md).
