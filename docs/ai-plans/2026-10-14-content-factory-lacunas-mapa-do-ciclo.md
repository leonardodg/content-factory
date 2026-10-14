# Lacunas do mapa do ciclo: breaker por plataforma e timeout dos jobs

> TDD sempre. Gates do CLAUDE.md antes de "pronto". Branch
> `fix/concorrencia-etapa1`, que entra no PR #10.

## Context

Ao montar `docs/diagram/mapa-do-ciclo.md` apareceram duas lacunas:

1. **Breaker por plataforma no Redis inativo.** `RedisRateLimiter` tem
   `record_failure`/`record_success` (3 falhas seguidas → `blocked` por
   15 min, chaves `cf:cb:*`) e o `acquire` consulta o bloqueio. Só que
   nenhum caminho do app grava falha, então o breaker da spec §7 nunca
   abre.
2. **`job_timeout` de 300 s (default do arq) para o job inteiro.** O prazo
   cobre os 3 segmentos. Com `TIKTOK_MS_TOKEN` e `tiktok=1` serializando o
   Playwright, o collect e o research podem ser cancelados. Os crons também
   usam `max_tries=1` (default do `cron()`), então não há retry.

## Decisões

1. **Porta nova, segregada (ISP):** `PlatformHealth` em `app/ports.py`, com
   `record_failure(platform)` e `record_success(platform)`. A `RateLimiter`
   não muda, e os fakes existentes continuam valendo. `CollectDeps` e
   `ResearchDeps` ganham `health: PlatformHealth`, com default
   `NoPlatformHealth` (null object, em `app/`). A composição do worker
   passa o `RedisRateLimiter` compartilhado, que implementa as duas portas.
2. **O que conta como falha da plataforma:** só o erro do `discover`, e
   não o do `acquire` (janela cheia é o nosso limiter; `blocked` no
   acquire é o próprio breaker aberto). Os códigos que contam são
   `blocked`, `rate_limited`, `network` e `upstream_changed`. **Não contam:**
   - `auth_expired`: config, a requisição nem sai;
   - `invalid_payload`: dado ruim;
   - `internal_error`: bug nosso.

   Sucesso do `discover` zera o contador. Vale para o collect e para a busca
   social do research.
3. **Timeout por função:** `collect_job` e `research_job` com
   **1500 s (25 min)**, menor que o TTL do lock por segmento (1800 s), para
   o lock nunca expirar com o job ainda vivo. `score_job` com 300 s
   explícito, porque é só banco. O valor vai na `func()` **e** no `cron()`.
   Um teste garante `timeout < DEFAULT_TTL_S` do lock.

## Tasks

### Task A — Breaker por plataforma ligado

> ✅ Feita (`8ca7be4`). A implementação acrescentou um ponto: registrar saúde
> nunca levanta (`report_discover` loga e segue), porque o Redis fora do ar
> cancelaria as irmãs.
- RED em `tests/app/test_collect.py`:
  - **Discover com falha de plataforma** (`RateLimited`, `NetworkError`,
    `blocked`, `UpstreamChanged`) chama `record_failure`; sucesso chama
    `record_success`.
  - **Falhas que não contam** (`auth_expired`, `internal_error`, erro no
    `acquire`) não chamam.
- RED no research com a mesma regra para a busca social.
- RED de composição: `bundle.collect.health is shared.limiter`, e o mesmo
  para o research.
- Integração (Redis real): 3 falhas de discover em segmentos diferentes
  deixam o 4º `acquire` com `blocked`.

### Task B — Timeout explícito por job

> ✅ Feita.
- RED: `WorkerSettings.functions`/`cron_jobs` com `timeout` 1500/1500/300,
  e `max(timeouts de collect/research) < lock.DEFAULT_TTL_S`.
- Docs: `mapa-do-ciclo.md` (lacunas resolvidas, `max_tries=1` nos crons),
  `operacao.md` e HANDOFF.
