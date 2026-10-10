# HANDOFF — fonte única de estado

> Regra de ouro: o estado detalhado vive **aqui**; `CLAUDE.md`/`AGENTS.md`
> ficam só com regras imutáveis. Atualizar **a cada sessão**.

## 🔄 EM ANDAMENTO

_(vazio — PRs #1, #2 e #3 merged; branches merged apagadas local+remota;
default branch do repositório corrigido para `main` em 2026-10-10)_

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
- [x] **Build do Dev Container corrigido**: serviço `python` declara o
  diretório temporário de features como `additional_context`, permitindo ao
  Compose passar a leitura específica requerida pelo Buildx 0.38; build via
  Dev Containers CLI 0.89.0 concluído e container `cf-python` iniciado
- [x] **Manual de operação** (`docs/manual/`, 8 capítulos pt-BR, humano +
  agente): spec `docs/superpowers/specs/2026-10-09-manual-operacao-design.md`
  + plano `docs/ai-plans/2026-10-09-manual-operacao.md` (8 tasks) executado
  por SDD com review por task; review final da branch encontrou 2 Critical +
  3 Important cross-chapter → fix `ea2542f` → re-review **APPROVED**;
  ledger `.superpowers/sdd/2026-10-09-manual-operacao/progress.md`
- [x] `credentials.md` corrigido (`ec05862`): worker lê `secrets.env` via
  Docker secret `app_secrets`, não `env_file`; `cf-python` lê só `app.env`
- [x] **PR #3 MERGED** (`7d5d295`, 2026-10-10): savepoint por candidato +
  collector Instagram (endpoint/gate #10) + tooling; 2 reviews "With fixes"
  aplicados; CI verde; branches merged (#1/#2/#3) apagadas local+remota;
  default branch do GitHub corrigido de `update/fundacao-2026-10-07` → `main`

## ⛔ O que falta (próximos passos aprovados)

1. **Issues pós-merge** (backlog da REVISÃO §6 — #1 mascaramento/finally já
   corrigido pelo savepoint; PR #3 merged `7d5d295`): lock token (#2 alta),
   INCR+EXPIRE (#3), detalhe de erro do score (#4) + deferrals do review do
   collector Instagram (media_type, simetria de `#`, paginação do fallback —
   ver `docs/ai-plans/2026-10-10-fix-instagram-collector.md`)
2. **Playtest worker COMPLETO** (2026-10-10): runs 123–140 `ok` nas 3
   plataformas (youtube/instagram/web), `score_job scored=15`, sane checks
   verdes; bugs 2–4 diagnosticados e corrigidos (imagem stale, savepoint/
   `skipped`, collector Instagram). **Pendências do usuário**:
   (a) submeter **App Review do Meta** (*Instagram Public Content Access*,
   gate #10 — até lá o fallback own-media cobre a coleta; ver
   `docs/PLANO_TESTE_E2E.md` §Bug 4); (b) `TIKTOK_MS_TOKEN` vazio (por design)
3. Fase de IA (replication/production + renormalização de pesos) e spec de
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
- Deferreds do manual (~15 nits cosméticos): ledger
  `.superpowers/sdd/2026-10-09-manual-operacao/progress.md`
