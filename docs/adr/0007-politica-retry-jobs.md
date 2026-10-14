# ADR-0007 — Política de retry dos jobs

**Situação:** Aceita · **Data:** 2026-10-14

## Contexto

A revisão sênior de 2026-10-14 registrou que a política de retry era
**implícita** (R9): os crons usam `max_tries=1`, nenhum job declara `Retry`
do `arq`, e o comportamento dependia de saber quais erros se repetem sozinhos
(quebra de rede, janela de cota) e quais não (credencial vencida). Implícito
significa que a próxima pessoa a tocar no worker não tem de adivinhar.

## Decisão

**Sem retry do arq.** `max_tries=1` é a regra: o próximo horário do cron é a
nova tentativa. Recomputar o mesmo período depois de falhar não agrega nada —
collect e score são idempotentes no que importa, e research recria os sinais
do dia.

| Job | Frequência (BRT) | Tentativa nova |
| --- | --- | --- |
| `collect_job` | 08:00 e 20:00 | 12 h depois |
| `research_job` | 07:00 | no dia seguinte |
| `score_job` | 08:30 e 21:30 | 12 h depois |

O que se tenta **dentro** do mesmo job, em ordem:

| Camada | Comportamento | Limite |
| --- | --- | --- |
| Backoff do limiter | espera com backoff exponencial + jitter quando a janela está cheia | 5 tentativas (`_MAX_ATTEMPTS`), espera > 60 s vira `RateLimited` |
| Backoff do TikTok | `EmptyResponseException` (bloqueio provisório) com backoff + jitter | 5 tentativas (`MAX_ATTEMPTS`), depois `blocked` |
| Breaker de plataforma | 3 falhas (`blocked`, `rate_limited`, `network`, `upstream_changed`) → `blocked` por 15 min no Redis, somando segmentos | `auth_expired`/`invalid_payload`/`internal_error` **não** abrem breaker |
| Breaker de fonte | 5 falhas seguidas de web_suggest/trends → `circuit_open` | `CooldownBreaker` em memória (1 processo no worker único), cooldown de 72 h = 3 ciclos diários do research |
| Erro de infra (banco/Redis) | o segmento falha inteiro: rollback da UoW + log com traceback; os outros segmentos seguem | sem retry — consertar o ambiente |

`auth_expired` nunca é retryable por código: é credencial vencida e exige
humano (ver [chaves-api](../manual/chaves-api.md)).

## Idempotência da reexecução

- **collect/research:** `upsert_candidates` com `ON CONFLICT DO UPDATE` —
  rodar de novo atualiza, não duplica.
- **score:** `video_scores` é append-only (timeseries): uma reexecução
  acrescenta um novo snapshot com `computed_at` próprio; a leitura usa a
  captura mais recente por vídeo (`DISTINCT ON`), então o ranking não muda
  de forma espúria.
- **runs:** cada execução grava sua própria `collect_runs` — é assim que o
  histórico de falhas fica visível.

## Shutdown

Cancelamento propaga pelo `TaskGroup`, o `finally` libera o lock por
segmento e a UoW reverte. Coberto por
`test_collect_external_cancel_releases_lock` em
`tests/app/test_collect.py`.

## Reexecução manual

A CLI roda fora do worker e usa suas próprias dependências (não disputa o
lock com o cron quando o cron não está no meio de um tick):

```bash
cf collect --segment <slug>
cf score --segment <slug>
cf research status --segment <slug>
```

Para forçar um tick do worker (fora do cron) — usado pela fumaça do deploy:

```bash
docker exec cf-worker-multi-segmento uv run python -c "
import asyncio
from arq import create_pool
from arq.connections import RedisSettings

async def main():
    pool = await create_pool(RedisSettings.from_dsn('redis://redis:6379/0'))
    print(await pool.enqueue_job('collect_job'))
    await pool.aclose()

asyncio.run(main())
"
```

> `uv run` porque o worker instala as dependências no venv do uv: o
> `python` puro do container é o do sistema e não tem `arq`
> (auditoria 2026-10-14 A-1).

## Alternativas consideradas

| Alternativa | Por que não |
| --- | --- |
| `Retry` do arq por job | Repete falha de dado/credencial sem diagnóstico e atrasa o tick seguinte; com jobs diários, o retry automático compra pouco e esconde o erro |
| DLQ com reprocessamento (ADR-0002 avaliado de novo) | Não há trabalho caro perdido: todo estado está no banco e o próximo tick refaz |
| Retry infinito para `auth_expired` | Nunca resolve sozinho; mascara a pendência humana |

## Consequências

- Falha transitória de rede custa até 12 h de atraso da coleta — aceitável
  para um experimento de 7 dias; se virar produção contínua, reavaliar.
- **Revisitar se:** o score passar a ser disparado por evento (não por cron)
  e o atraso de 12 h deixar de ser aceitável.
- O mapa do ciclo
  ([mapa-do-ciclo](../diagram/mapa-do-ciclo.md)) e o
  [manual de operação](../manual/operacao.md) referenciam esta política.
