# HANDOFF — fonte única de estado

> Regra de ouro: o estado detalhado vive **aqui**; `CLAUDE.md`/`AGENTS.md`
> ficam só com regras imutáveis. Atualizar **a cada sessão**.

## 🔄 EM ANDAMENTO

- Plano de implementação task-by-task em `docs/ai-plans/2026-10-07-fundacao-coletores-banco.md`
  (elaboração — escrito nesta sessão)

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
- [x] Índice de roteamento: `docs/README.md`
- [x] git init (`main`) + `.gitignore` (`.env`, `database.db*` ignorados)
- [x] Commits: `5f9b4ef` (spec), `docs: ADRs, PRD, HANDOFF e índice`

## ⛔ O que falta (ordem aprovada)

1. Plano em `docs/ai-plans/` (skill writing-plans) — **esta sessão**
2. Execução fase A (fundação): tooling → camadas → domínio → DB/Alembic →
   CLI/segmento → compose/devcontainer → CI → docs/mkdocs
3. Execução fase B (coletores + score): ports/DTO → use-case collect →
   YouTube → TikTok → Instagram → Web → score → worker arq → E2E
4. Gates de aceitação do PRD (§critérios) + `SOLID_AUDIT.md` + `REVISAO_*.md`
5. Push repo privado `content-factory` no GitHub (ci.yml + docs.yml verdes)

## Pendências / cuidados

- Repositório **local ainda sem remote**; legado `src/` e `README.md` antigos
  não foram commitados (tratados na task de fundação: mover p/ `legacy/` ou
  remover — decidir na execução, referência de domínio: `viral_analyzer.py`,
  `instagram_graph_api.py`, `content_analyzer.py`)
- Credenciais pendentes p/ execução real: `YOUTUBE_API_KEY`, `INSTAGRAM_ACCESS_TOKEN`+`IG_USER_ID`,
  `TIKTOK_MS_TOKEN` (fornecidas pelo usuário; nunca em arquivo rastreado)
- `mkdocs` e docs geradas só na fase de execução (estrutura de docs nascida hoje
  é manual e versionada)
