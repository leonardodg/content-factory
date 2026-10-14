# Research resiliente a falha de fonte (Suggest/Trends)

> TDD sempre — RED antes de GREEN. Gates do CLAUDE.md antes de "pronto".
> Branch: `fix/concorrencia-etapa1` (entra no PR #10, junto da Etapa 1).

## Context

Achado da Etapa 1 T10 (`2026-10-14-content-factory-concorrencia-review-fixes.md`):
em `run_research_cycle`, `deps.web.expand` e `deps.trends.interest_over_time`
(e os `limiter.acquire` deles) não têm proteção. Qualquer erro, inclusive
`circuit_open`, aborta o research do segmento **antes da fase social**. Em
produção, o Trends devolve HTTP 400 (`UpstreamChanged`) todo dia, e por isso
o research dos 3 segmentos falha inteiro. Isso contradiz o contrato que o
`collect()` segue: falha de uma fonte não derruba as demais.

## Decisões

1. **Falha de fonte vira run `failed`, não exceção.** `CollectorError` →
   `error_code` do contrato. Exceção fora do contrato → `internal_error` +
   `logger.exception` (mesma regra da Task 8.1 para coletores). O ciclo segue
   com os sinais que houver: Suggest falhou → `web_signals = []`; Trends
   falhou → `trend_points = []`.
2. **Uma `CollectRun` por fonte, sempre (ok ou failed)**: `research:web_suggest`
   (`deps.web.platform`) e `research:trends` (a mesma chave do limiter), no
   mesmo padrão das redes sociais (`research:<plataforma>`). Assim a falha
   diária do Trends fica auditável em `collect_runs`, não só no envelope.
3. **Fallback de keywords para a fase social:** se não houver nenhum sinal
   (as duas fontes falharam ou voltaram vazias), a fase social usa as seeds
   do segmento (já admitidas pelo guard na profundidade 0). Sem isso, a
   resiliência não teria efeito, porque as redes receberiam lista vazia.
4. **Envelope:**
   - `ok` = nenhuma fonte **e** nenhuma rede social falhou.
   - `partial` = `not ok` e a fase social inseriu ou atualizou algum
     candidato. É o contrato anterior. A ideia de contar sinais salvos foi
     descartada na implementação: quase todo ciclo salva sinal, e `partial`
     perderia o sentido.
   - Contagens novas: `web_failed`/`trends_failed` (0/1) e
     `source_errors`.
5. **Fora de escopo:** erro de banco (`signals.upsert_many`,
   `trend_observations.record_many`) continua propagando. É infraestrutura
   e segue a regra da Etapa 1: erro original, UoW revertida.

## Tasks

### Task A — Suggest/Trends falhando não abortam o ciclo
- RED em `tests/app/test_research_pipeline.py`:
  - **Trends levantando `UpstreamChanged`:** social roda, `trends_failed == 1`,
    run `research:trends` com status `failed`/`upstream_changed`, `ok False`,
    `partial True` quando houve candidato social.
  - **Suggest em `circuit_open`:** trends e social seguem; run
    `research:web_suggest` `failed`/`circuit_open`.
  - **As duas falhando:** a social recebe as seeds.
  - **Exceção fora do contrato numa fonte:** `internal_error` + 1 registro
    ERROR com traceback.
  - **Caminho feliz:** runs `ok` de `research:web_suggest` e `research:trends`
    com `found`, e envelope `ok True`.
- GREEN em `app/research_pipeline.py`: helper `_run_source(name, call)` com
  run/contagem/log, e o fallback de seeds.
- Docs: `operacao.md` (remover o alerta e descrever o contrato novo),
  docstring de `run_research_job`, `docs/diagram/fluxos.md` se citar o
  abort, e HANDOFF.

## Verificação
- Gates completos com Postgres/Redis descartáveis.
- Após o swap (pós-2026-10-19): o `research_job` do dia mostra
  `research:trends failed` em `collect_runs` e runs sociais `ok`, e não mais
  o segmento inteiro com `ok: false` e `error: UpstreamChanged`.
