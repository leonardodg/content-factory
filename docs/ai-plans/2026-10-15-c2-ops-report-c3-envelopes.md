# C2 `cf ops-report` e C3 contrato de envelopes (2026-10-15)

Origem: plano de revisão sênior (§C2, §C3) e segunda auditoria externa,
que pediu os dois **antes** do B3 — o D+3 (17/10) e o D+7 (21/10) passam a
sair do comando, não de SQL manual.

## C2 — `cf ops-report --segment <slug> [--hours 24]`

Leitura pura do Postgres (sem Redis), envelope único com:

- `collectors`: por coletor **e** por fonte do research (`research:*`):
  runs, contagem por status (`ok`/`partial`/`failed`/`blocked`), taxa ok,
  `empty` (ok com `found = 0`), códigos de erro, found/inserted/updated/
  skipped e `quota_used`;
- `web_empty`: runs `web` vazias, sempre com `ambiguous: true` (E-4.3);
- `blocked_runs`: runs barradas pelo breaker;
- `ticks`: execuções do collect agrupadas por proximidade de início;
  **duração de parede** (min `started_at` → max `finished_at`) separada da
  **soma** `discover_ms + persist_ms` (concorrência faz a soma passar da
  parede);
- `coverage`: por plataforma, vídeos do segmento, com `views` no último
  snapshot e com ≥ 2 snapshots;
- `scored`: scores calculados na janela.

Camadas: agregação pura em `app/ops_report.py`; repositórios só devolvem
linhas (`CollectRunRepository.runs_since`, `OpsReportReader`).

Tasks (TDD): T1 agregação pura; T2 repositórios com Postgres real; T3 CLI
+ manual.

## C3 — contrato único dos envelopes

Escrito depois do C2, em `docs/architecture/contrato-envelopes.md`, com
testes de paridade CLI × worker para `collect` e `score` (mesmas chaves e
mesma semântica de `ok`/`partial`/`error.code`). Detalhe no próprio PR.
