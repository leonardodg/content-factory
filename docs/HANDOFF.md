# HANDOFF — fonte única de estado

> Regra de ouro: o estado detalhado vive **aqui**; `CLAUDE.md`/`AGENTS.md`
> ficam só com regras imutáveis. Atualizar **a cada sessão**.

## 🔄 EM ANDAMENTO

- **PR #2** `docs/manual-operacao` → `main`: manual de operação 8 capítulos
  (CI verde, MERGEABLE, aprovado em review — aguarda merge humano; pós-merge:
  apagar branches). *Obs.: este branch também edita HANDOFF/PLANO_TESTE_E2E —
  resolver conflito de merge se os dois entrarem.*
- **Branch `fix/collect-upsert-savepoint`** (playtest worker → 3 bugs fechados):
  fix REVISÃO §6 #1 (`21ad56b`) + tooling (`57bbc23`, `c7e6712`) + ajustes de
  review (`8347dcc`, `e79d36d`) + **fix do collector Instagram** (`fc320bf`:
  endpoint `ig_hashtag_search`/`top_media` + fallback own-media no gate #10,
  TDD 3 testes) — review "With fixes" aplicado; gates verde; **pronto para
  PR/merge humano**.

## ✅ CONCLUÍDO (sessão 2026-10-07 → 2026-10-10)

- [x] Fase 0 (brain/spec/ADRs/PRD/plano 18 tasks): commits `5f9b4ef`,
  `2c80bde`, `4b81c65`, `2276254`
- [x] **Plano `docs/ai-plans/2026-10-07-fundacao-coletores-banco.md`
  EXECUTADO INTEIRO (Tasks 1–18)** na branch `update/fundacao-2026-10-07`
  (SDD task-by-task, TDD, review por task; ledger
  `.superpowers/sdd/2026-10-07-fundacao-coletores-banco/progress.md`)
- [x] Frente 1 — fundação: uv/ruff/pyright strict/pre-commit, camadas
  `domain/app/infra/cli`, domínio+scoring, ORM+Alembic+repositórios,
  segmentos YAML, CLI envelope 0/2/1, compose+devcontainer, CI+privacy,
  mkdocs strict (Tasks 1–9)
- [x] Frente 2 — coletores+banco: ports/DTO, use-case `collect` (lock+
  rate-limit Redis), coletores YouTube/TikTok/Instagram/Web, `cf ingest`
  com guarda SSRF, use-case `cf score`, worker arq (cron 2×/dia BRT,
  `max_jobs=1`), `cf status`, E2E `00`/`01` (Tasks 10–18)
- [x] **Gap T17 corrigido**: `cf collect` de produção agora chama
  `register_defaults` na composition root (`30f06a2`, TDD)
- [x] **Bug real do playtest E2E corrigido** (decisão humana opção 1):
  `WebCollector` usava URL cheia como `platform_video_id` >
  `VARCHAR(64)` → abortava collect; agora `sha1(url)` (`f393674`, TDD)
- [x] **REVISÃO §6 #1 corrigido** (2026-10-10, `21ad56b`, TDD): savepoint por
  candidato no upsert (não mata mais a transação inteira; `skipped` visível em
  `CollectRun.failed` e `counts["skipped"]`); review da branch "With fixes"
  aplicado (2 Important + 1 Minor)
- [x] Playtest E2E real (compose de pé): `cf collect` (web ok=3;
  multi-plataforma partial yt/tt/ig=failed com chaves vazias) →
  `cf score` `scored=7` → `cf status` — transcrição em
  `docs/PLANO_TESTE_E2E.md`
- [x] **Bug 4 do playtest corrigido** (2026-10-10, `fc320bf`, TDD): collector
  Instagram chamava `/{user}/hashtags` (endpoint inexistente, Graph #100 →
  mascarado como `auth_expired`); agora `ig_hashtag_search`+`top_media` com
  `user_id` e fallback automático para `/{uid}/media` (própria conta,
  filtrado por keyword) quando o gate #10 (App Review) responde. Token IGAA
  inutilizável trocado pelo EAA funcional (secrets.env, fora do git) +
  `--build --force-recreate` no worker
- [x] **Playtest com chaves válidas verde nas 3 plataformas** (runs 123–125:
  youtube/instagram/web `ok`; IG 3 inserted, `viral_score` 16.5) +
  `score_job scored=15` — evidência em `docs/PLANO_TESTE_E2E.md`
- [x] Auditorias finais: `docs/SOLID_AUDIT.md` (GO) +
  `docs/REVISAO_2026-10-07.md` (GO, 6 findings: 1 corrigido no branch de fix,
  4 backlog, 1 deferido)
- [x] Gates finais (2026-10-09, com evidência): pytest 119 passed (+
  TEST_DATABASE_URL), e2e 4 passed, ruff check/format 0, pyright strict 0,
  bandit exit 0, `alembic check` ok, pip-audit 0 vulns, privacy ok (142
  arquivos), `mkdocs build --strict` ok

## ⛔ O que falta (próximos passos aprovados)

1. **CI verde no PR** → merge em `main` → apagar branches local+remota
2. **Issues pós-merge** (backlog da REVISAO §6, exceto #1 — corrigido em
   `21ad56b`): lock token (#2 alta), INCR+EXPIRE (#3), detalhe de erro do
   score (#4)
3. **Playtest worker COMPLETO** (2026-10-10): runs 123–125 `ok` nas 3
   plataformas (youtube/instagram/web), `score_job scored=15`, sane checks
   verdes; bugs 2–4 diagnosticados e corrigidos (imagem stale, savepoint/
   `skipped`, collector Instagram). **Pendências do usuário**:
   (a) submeter **App Review do Meta** (*Instagram Public Content Access*,
   gate #10 — até lá o fallback own-media cobre a coleta; ver
   `docs/PLANO_TESTE_E2E.md` §Bug 4); (b) `TIKTOK_MS_TOKEN` vazio (por design)
4. Fase de IA (replication/production + renormalização de pesos) e spec de
   segurança TOCTOU — próximos planos, não desta entrega

## Pendências / cuidados

- Remote `origin` = `github.com/leonardodg/content-factory` (privado);
  legado pré-migração em `legacy/` (untracked de referência: `viral_analyzer.py`,
  `instagram_graph_api.py`, `content_analyzer.py`)
- Credenciais **nunca** em arquivo rastreado; placeholders em
  `.devcontainer/secrets/secrets.env` (gitignored); `check_privacy.py` é gate
  (`.pre-commit` não o tem — ledger T8)
- Redis do compose **não publica porta no host** — CLI/worker rodam via
  `docker exec cf-python` / perfil `worker` do compose
- `TEST_DATABASE_URL` (testes) ≠ `DATABASE_URL` (CLI) — E2E automatizado usa a
  primeira, playtest manual exporta a segunda (ledger T6/coord T18)
- Deferreds de todas as tasks: ledger
  `.superpowers/sdd/2026-10-07-fundacao-coletores-banco/progress.md`
