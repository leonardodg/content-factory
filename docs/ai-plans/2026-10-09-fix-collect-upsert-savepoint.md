# Plano — fix REVISÃO §6 #1: savepoint por candidato no upsert do collect

**Data:** 2026-10-09 · **Branch:** `fix/collect-upsert-savepoint` (de `origin/main`)
· **Tipo:** bugfix (TDD) · **Origem:** backlog #1 alta da
`docs/REVISAO_2026-10-07.md`, reproduzido no playtest real com worker
(evidência em `docs/PLANO_TESTE_E2E.md`)

## Bug (reproduzido no playtest 2026-10-09)

1. `WebCollector` devolveu `platform_video_id` com URL de 91 chars
   (coluna `VARCHAR(64)`) → `StringDataRightTruncation` no
   `upsert_candidates` (repositories.py)
2. O erro **abortou a transação inteira** do Postgres (permanece
   `failed` até ROLLBACK)
3. O `finally` de `app/collect.py` chama `runs.record` na transação
   já morta → `InFailedSqlTransaction` **mascara o erro original**
   (o job falha com o erro do audit, causa raiz só na cadeia)

Obs.: o sintoma 1 foi mitigado pelo fix `f393674` (sha1 da URL), mas a
classe de bug permanece — qualquer `DataError` (título grande demais,
valor nulo inesperado, etc.) reproduz o masking. É exatamente o
precedente que o score já resolveu no T16.

## Diretriz (REVISÃO §6 #1)

> savepoint por candidato (precedente score/T16)

Espelhar `ScoreRepositoryImpl.upsert` (`begin_nested` por vídeo) no
`VideoRepositoryImpl.upsert_candidates`, com a diferença de semântica do
collect: o score **propaga** (use-case decide partial); o collect já tem
o campo `failed` no `CollectRun`/`UpsertStats.skipped` para **contar e
seguir** — candidato doente é revertido isolado, os vizinhos persistem,
a transação nunca aborta e o `finally` do audit funciona.

## Mudança

1. **`src/content_factory/infra/db/repositories.py`**
   `upsert_candidates`: cada candidato roda dentro de
   `async with self._s.begin_nested():` (INSERT do vídeo + link do
   segmento). Em falha de DB do candidato: o savepoint reverte só ele,
   `stats.skipped += 1`, loop segue. Erro que não é de candidate-data
   (ex.: conexão caiu) segue propagando — savepoint não engole isso
   (salva e re-levanta após rollback do savepoint).

2. **`src/content_factory/app/collect.py`**
   Após o upsert: `run.failed = stats.skipped` e
   `counts["failed"] += stats.skipped` — visibilidade no audit
   (`collect_runs.failed`) e no envelope. `ok`/`partial` continuam
   governados pelos erros de plataforma (escopo do backlog não muda
   semântica de outcome).

3. **NÃO** guarda extra no `finally`: com a transação nunca abortada
   por candidate-data, `runs.record` não mascara mais a classe real do
   bug (que era essa). Erro de conexão no finally é outro fenômeno e
   continua fora (ledger).

## TDD

- **Red:** `tests/infra/test_repositories.py` —
  `test_upsert_candidates_skips_bad_candidate_keeps_txn_alive`:
  3 candidatos, o do meio com `platform_video_id` de 93 chars →
  `stats.skipped == 1`, os 2 vizinhos persistem após commit, e uma
  query seguinte na mesma sessão funciona (transação viva). Espelha a
  docstring do teste T16 (exige `TEST_DATABASE_URL`).
- **Red app:** `tests/app/test_collect.py` — fake de
  `VideoRepository` devolvendo `UpsertStats(skipped=2)` →
  `outcome.runs[0].failed == 2` e `counts["failed"] == 2`.
- **Green:** implementar (1) e (2).
- **Regressão:** suíte completa (pytest) + gates.

## Fora de escopo (ledger)

- Guard do `finally` para erros não-de-dado (conexão) — classe rara;
  REVISÃO não mandou
- Semântica nova: pular candidato virar `partial`/`ok=False`
- `error_code` para skipped (audit fica com `failed>0`, `error_code=NULL`)

## Gates

pytest (com `TEST_DATABASE_URL`), `ruff check` + `ruff format --check`,
`pyright` strict, `bandit`, `alembic check`, `mkdocs --strict`,
`check_privacy` — todos exit 0 antes do commit.

## Aceitação

1. Teste red→green nos 2 níveis (infra + app)
2. Suíte completa verde; nenhum teste existente quebrado
3. Code review (skill requesting-code-review) antes de considerar pronto
4. Evidência do playtest + este fix documentados em
   `docs/PLANO_TESTE_E2E.md`; HANDOFF/ledger atualizados

## Code review (2026-10-10) — aplicações e deferrals

**With fixes** → aplicados neste branch:
- Important: `counts["failed"]` misturava plataforma+candidato e mentia em
  `cli/main.py:156` → chave nova `counts["skipped"]` (testes atualizados)
- Important: docs (REVISÃO §6/§8, HANDOFF, PLANO_TESTE_E2E) diziam "backlog"
- Minor: smoke passou a isolar por `id > max(id)` (imune a relógio/worker
  concorrente); formato PEP 758 + bump `ruff-pre-commit` v0.16.10 (skew
  bloqueava o commit)

**Deferrals (não bloqueiam merge):**
- Testes de contrato: `OperationalError` propagando via fake; linha em
  `collect_runs` com `status='ok'` e `failed=N` pós-collect (só unitário hoje)
- `except IntegrityError` largo: violação sistêmica (FK) contaria como
  skipped em vez de abortar — risco baixo (segmento validado antes); se
  estreitar: só `DataError`
- `tests/infra/test_repositories.py` deixa 2 vídeos + 1 segmento commitados
  no banco dev compartilhado (ids uuid, sem colisão) — DELETE de limpeza
  opcional
