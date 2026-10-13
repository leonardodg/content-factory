# Primeiro passo — do zero ao primeiro coletor verde

Este capítulo leva você do repositório limpo até o primeiro `cf status` com
dados: subir a stack, carregar o segmento `candles`, coletar pela plataforma
`web` (a única que **não exige chave**), pontuar e auditar. A sequência é
cópia-e-cola do playtest manual já validado em 2026-10-09 (transcrição em
[PLANO_TESTE_E2E](../PLANO_TESTE_E2E.md)).

Toda saída da CLI é **uma linha JSON** (envelope `{"ok": …}`) e os exit codes
são `0`=ok · `2`=parcial · `1`=erro — contrato completo em
[TRD CLI](../specs/trd-cli.md). Índice do manual: [Manual de
operação](index.md).

## 1. Pré-requisitos

- **Docker + Docker Compose** instalados (a stack sobe via
  `.devcontainer/docker-compose.yml`, não é preciso abrir o devcontainer).
- **Chaves de API: não necessárias para este capítulo.** O caminho abaixo usa
  só a plataforma `web` (DuckDuckGo, sem chave). Para ligar YouTube,
  Instagram e TikTok depois, siga [Chaves de API](chaves-api.md) — os valores
  entram em `secrets.env` (o que o worker lê) **e** em `app.env` (o que o CLI
  manual `cf-python` lê; camadas em [Chaves de API
  §4](chaves-api.md#4-onde-colar)), ambos fora do git.
- Ambiente de **desenvolvedor/contribuidor** (uv, venv, testes): fora do
  escopo deste manual — ver [ambiente de
  dev](../dev/dev-environment.md).

## 2. Arquivos de ambiente

Cópia dos exemplos versionados para os arquivos reais (gitignored):

```bash
cp .devcontainer/env/app.env.example .devcontainer/env/app.env
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env
```

Sem saída esperada (silêncio = sucesso). **Valores vazios estão ok** para o
primeiro teste com `web`. Nunca commite `app.env` nem `secrets.env`; valores
reais de chave são fornecidos pelo operador fora do git
([chaves-api.md](chaves-api.md)).

## 3. Subir a stack e o schema

```bash
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
docker compose -f .devcontainer/docker-compose.yml exec python uv run alembic upgrade head
```

Esperado: os serviços `postgres`, `redis` e `python` (containers
`content-factory-postgres-1`, `content-factory-redis-1` e `cf-python`) em
estado `Up`; o alembic termina com uma linha
`INFO [alembic...] Running upgrade <revisão> -> <head>` (em banco novo, todas
as migrações; em banco já migrado, `Running upgrade ... -> ...` não aparece e
o comando sai em silêncio, exit 0).

## 4. Healthcheck: `cf ping`

O CLI roda **dentro** do container `cf-python` (o Redis do compose não
publica porta no host; ele recebe `DATABASE_URL`/`REDIS_URL` do compose):

```bash
docker exec cf-python uv run cf ping
```

Saída esperada (exit `0`):

```console
{"ok": true, "command": "ping"}
```

## 5. Carregar o segmento

```bash
docker exec cf-python uv run cf segment load segments/candles.yaml
```

Saída esperada (exit `0`; o `id` pode variar conforme o banco):

```console
{"ok": true, "command": "segment.load", "slug": "candles", "id": 40}
```

## 6. Primeira coleta — só `web`, sem chave

```bash
docker exec cf-python uv run cf collect --segment candles --platform web --limit 3
```

Saída esperada (exit `0`):

```console
{"ok": true, "command": "collect", "found": 3, "inserted": 3, "updated": 0, "failed": 0, "skipped": 0}
```

`inserted: 3` no primeiro run; num run seguinte com as mesmas URLs o upsert é
idempotente (`updated` sobe e `inserted` fica `0`) — o comportamento correto é
ambos.

## 7. Pontuar

```bash
docker exec cf-python uv run cf score --segment candles
```

Saída esperada (exit `0`):

```console
{"ok": true, "command": "score", "scored": 7}
```

`scored` é o total de vídeos do segmento pontuados neste run (o playtest
fechou em `7` porque o banco já tinha seeds prévios no segmento; em banco
zerado o número acompanha o que o passo 6 coletou).

## 8. Status operacional

```bash
docker exec cf-python uv run cf status --segment candles
```

Saída esperada após a primeira coleta `web` (exit `0`):

```console
{"ok": true, "command": "status", "runs": {"ok": 1}}
```

`runs` agrega **todos** os runs do segmento nos últimos 7 dias por status. No
playtest completo (incluindo a falha parcial do passo 9) a saída foi
`{"ok": true, "command": "status", "runs": {"failed": 3, "ok": 2}}`.

## 9. Primeiras falhas — o que significam os exit codes

| Exit | Significado | O que fazer |
|---|---|---|
| `0` | sucesso | seguir em frente |
| `2` | **parcial**: alguma plataforma falhou, o lote seguiu | ok para seguir; investigar depois |
| `1` | **erro**: o comando não fez o trabalho pedido | corrigir causa antes de repetir |

**Exit 2 — falha parcial.** Exemplo real do playtest, multi-plataforma com as
chaves ainda vazias (`youtube`/`tiktok`/`instagram` falham, `web` entrega):

```console
$ docker exec cf-python uv run cf collect --segment candles --platform youtube,tiktok,instagram,web --limit 3
{"ok": true, "command": "collect", "partial": true, "found": 3, "inserted": 3, "updated": 0, "failed": 3, "skipped": 0}
# exit=2
```

Códigos típicos dessas falhas (gravados em `collect_runs.error_code`):
`auth_expired` (token ausente/expirado) e `rate_limited` (cota estourada).
Solução: criar/preencher as chaves ([chaves-api.md](chaves-api.md)).

**Exit 1 — erro total.** Envelope de erro, no formato
`{"ok": false, "command": "…", "error": {"code": "…", "message": "…"}}`. Códigos
mais comuns no primeiro dia:

- `segment_not_found` — slug inexistente: o `segment load` do passo 5 não
  rodou (ou rodou em outro banco);
- `ja_em_execucao` — o lock Redis do segmento está ocupado (outra coleta ou o
  worker do cron rodando): não force; espere a execução terminar;
- `usage_error` / `invalid_segment` — argumento/YAML rejeitado.

**Onde olhar quando algo falha:** a tabela `collect_runs` guarda
`status`/`error_code` de cada execução — queries prontas em
[Análise](analise.md). Tabela de códigos normativa:
[TRD CLI](../specs/trd-cli.md).

## 10. Notas finais e próximo passo

- CLI sempre via `docker exec cf-python uv run cf …` — nada de expor Redis ou
  rodar o CLI no host apontando para o banco do compose.
- Cadeira do dia a dia (`collect` → `score` → `status`) com worker e cron:
  [Operação](operacao.md). O que acontece por dentro de cada estágio do
  `collect` (lock, limiter, discover, upsert, auditoria): [Fluxo de
  busca](fluxo-busca.md).
