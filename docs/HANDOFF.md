# HANDOFF — fonte única de estado

> Regra de ouro: o estado detalhado vive **aqui**; `CLAUDE.md`/`AGENTS.md`
> ficam só com regras imutáveis. Atualizar **a cada sessão**.

## 🔄 EM ANDAMENTO

- **Execução do plano** task-by-task via `superpowers:subagent-driven-development`
  `docs/ai-plans/2026-10-07-fundacao-coletores-banco.md` (18 tasks, TDD;
  commitado `2276254`). Isolamento: branch `update/fundacao-2026-10-07`
  (in place, p/ manter legado untracked visível à Task 1); workspace SDD
  `.superpowers/sdd/2026-10-07-fundacao-coletores-banco/` (ledger `progress.md`).
  Iniciada em 2026-10-07 — começar pela **Task 1**.

## ✅ CONCLUÍDO (sessão 2026-10-07)

- [x] Skill `python-quality-standards` + `bootstrapping-python-devcontainer-projects` carregadas
- [x] Perguntas obrigatórias respondidas (nome, docs completos, infra, GPU= não, escopo)
- [x] Decisões fechadas em brainstorming (1 por vez, aprovadas por seção):
  - PostgreSQL 16 + pgvector · Redis + arq · TikTok-Api · Instagram Graph oficial
  - CLI-first (JSON = envelope de status; dados no banco) · monolito modular
  - Compose único padrão da casa · segurança + anti-bloqueio aprovados
- [x] Spec de design: `docs/specs/2026-10-07-content-factory-design.md` (auto-revisão feita)
- [x] ADRs: `0001-postgres-pgvector`, `0002-redis-arq-fila`, `0003-coletores-fontes`, `0004-cli-first`
- [x] PRD: `docs/produto/prd.md`
- [x] Índice de roteamento: `docs/roteamento.md`
- [x] Ideia inicial preservada verbatim: `docs/produto/ideia-inicial.md` (`4b81c65`)
- [x] **Plano 18 tasks escrito + auto-revisão concluída** e commitado
  (`2276254`): protocolos fechados, snippets concretos sem placeholder,
  nav mkdocs por arquivos, SSRF/redact, privacy check no CI
- [x] git init (`main`) + `.gitignore` (`.env`, `database.db*` ignorados)
- [x] Commits: `5f9b4ef` (spec), `2c80bde` (ADRs, PRD, HANDOFF, índice),
  `4b81c65` (ideia inicial), `2276254` (plano)

## ⛔ O que falta (ordem aprovada)

1. **Execução fase A (fundação)**: tooling → camadas → domínio → DB/Alembic →
   CLI/segmento → compose/devcontainer → CI → docs/mkdocs (Tasks 1–9)
2. **Execução fase B (coletores + score)**: ports/DTO → use-case collect →
   YouTube → TikTok → Instagram → Web → score → worker arq → E2E (Tasks 10–18)
3. Gates de aceitação do PRD (§critérios) + `SOLID_AUDIT.md` + `REVISAO_*.md`
4. Push final da branch + PR em `leonardodg/content-factory` (repo privado
   **já criado e com `main` + branch da execução pushados**; `ci.yml` +
   `docs.yml` verdes ao fim da Task 18)

## Pendências / cuidados

- Remote `origin` = `github.com/leonardodg/content-factory` (privado, criado
  2026-10-07); legado não commitado e tratado na Task 1 (mover p/ `legacy/`): `src/` flat, `README.md` antigo, `pyproject.toml`
  (poetry), `.devcontainer/` (2025), `.env-copy`, `.dockerignore` —
  referência de domínio: `viral_analyzer.py`, `instagram_graph_api.py`,
  `content_analyzer.py`
- Credenciais pendentes p/ execução real: `YOUTUBE_API_KEY`, `INSTAGRAM_ACCESS_TOKEN`+`IG_USER_ID`,
  `TIKTOK_MS_TOKEN` (fornecidas pelo usuário; nunca em arquivo rastreado)
- `mkdocs` e docs geradas só na fase de execução (estrutura de docs nascida hoje
  é manual e versionada)
