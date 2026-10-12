# HANDOFF — fonte única de estado

> Regra de ouro: o estado detalhado vive **aqui**; `CLAUDE.md`/`AGENTS.md`
> ficam só com regras imutáveis. Atualizar **a cada sessão**.

## 🔄 EM ANDAMENTO

_(nenhuma branch de feature aberta — roadmap A→C→D→E→B COMPLETO. Pipeline
de pesquisa multi-plataforma funcional: IG/YT/TikTok discover_by_keywords,
research_job com cron 07:00, CLI cf research start/stop/status, Google
Trends real via pytrends com circuit breaker. `web` (web-suggest) continua
noop — decisão pendente do usuário.)_

## ✅ CONCLUÍDO

### Fase B — Google Trends real via pytrends (2026-10-12)

Branch `feat/pipeline-pesquisa-fase-b` (4 commits, 175 passed + pyright 0).
Executada via executing-plans, plano
`docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-b.md` (TDD, RED verificado).

- [x] **Task B1** (`e7ec12b`): dependência `pytrends` 4.9.2 (Apache 2.0,
  pip-audit limpo; puxa numpy+pandas).
- [x] **Task B2** (`dd16ad1`): `TrendsGoogleProvider` — `asyncio.to_thread`
  (precedente web.py), mapeamento de erros, circuit breaker 5 falhas →
  cooldown 3 ciclos (5 testes).
- [x] **Task B3** (`263c26f`): worker `create_app_deps` usa
  `TrendsGoogleProvider` real (substitui `_NoopTrendsProvider`).
- [x] **Task B4**: gates completos (pytest 175 passed, ruff/format 0,
  pyright 0, alembic ok, bandit 0, mkdocs strict ok, privacy 178) +
  ADR-0005 (risco não-oficial do pytrends).

### Fase E — YouTube/TikTok discover_by_keywords (2026-10-12)

Branch `feat/pipeline-pesquisa-fase-e` (3 commits, 170 passed + pyright 0).
Executada via executing-plans, plano
`docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-e.md` (TDD, RED verificado).

- [x] **Task E1** (`e221e1c`): `YoutubeCollector.discover_by_keywords` —
  extrai `_search()` de `discover()`; query OR de keywords+hashtags
  (max 5 termos); mesmo fluxo search→videos→channels (1 teste).
- [x] **Task E2** (`ace9653`): `TikTokCollector.discover_by_keywords` —
  `_SearchEndpoint` adapta `client.search.videos` ao contrato
  `.videos(count=)` do `_call_with_backoff`; mesma sessão Playwright +
  backoff/jitter (1 teste).
- [x] **Task E3** (`2b98df5`): E2E guard-rail com coletores REAIS
  (MockTransport YT, stub TikTok, spy IG) — `fanout_per_stage` corta
  sinais web antes dos socials; dedupe casefold via `guard.admit` (2 testes).
- [x] **Task E4**: gates completos (pytest 170 passed, ruff/format 0,
  pyright 0, alembic check ok, bandit 0, mkdocs strict ok, privacy 176).

### Fase D — research_job + CLI + campanhas (2026-10-12)

Branch `feat/pipeline-pesquisa-fase-d` (4 commits, 166 passed + pyright 0).
Executada via executing-plans, plano
`docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-d.md` (TDD, RED verificado).

- [x] **Task D1** (`5fa1c70`): `ResearchCampaign` (model + migração 0003 +
  ORM + repo, 4 testes).
- [x] **Task D2** (`2644b55`): `research_job` no worker (cron 07:00 BRT,
  skip sem campanha ativa, noop KeywordExpander/TrendsProvider até Fase B,
  socials reais do registry; 3 testes novos).
- [x] **Task D3** (`68afbf4`): CLI `cf research start/stop/status`
  (envelope padrão, 6 testes).
- [x] **Task D4**: gates completos + docs (seção §5 em `operacao.md`) +
  HANDOFF + checkboxes do plano.

### Fase C — Instagram orientado a keyword (2026-10-12)

Merge local fast-forward em `main` (`34d5172` + `3e08390`, branch deletada;
153 passed + pyright 0 no merged). Executada via executing-plans, plano
`docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-c.md` (TDD, RED verificado).

- [x] **Task C1** (`34d5172`): `InstagramCollector.discover_by_keywords` —
  extrai `_own_media_by_terms` (helper parametrizado por lista de termos)
  de `_own_media`; `discover_by_keywords` consome a união keywords+hashtags
  do `research_pipeline`. `InstagramCollector` satisfaz estruturalmente
  `Collector` E `SocialDiscoverer` (1 teste novo, RED via AttributeError).
- [x] **Task C2** (`3e08390`): gates completos verdes (pytest 153 passed,
  ruff/format 0, pyright 0, bandit 0, pip-audit 0, mkdocs strict, privacy
  174 arquivos) + HANDOFF + checkboxes do plano.
- Ajuste fino vs plano: o fixture compartilhado `_OWN_MEDIA` tem 18002 como
  `IMAGE` (filtro de vídeo da Fase 0); o teste usa route custom com VIDEO
  para provar a união keywords+hashtags.

### Fase A — fundação do pipeline de pesquisa (2026-10-12)

**PR #4 MERGED** (`a6f60f5`): fundação + Fase 0 (fix Instagram) num único
PR (main local já estava 8 commits à frente do remoto).

Executada via executing-plans na branch `feat/pipeline-pesquisa-fase-a`
(plano split e copiado para `docs/ai-plans/2026-10-12-pipeline-pesquisa-fase-a.md`
em `9db8a7d`/`b1602d4`); TDD (RED verificado em todas as tasks),
review e gates completos. Nenhum provider real — só contratos,
guard-rail, tabela e orquestrador; Fases B–E pendem de decisão do usuário.

- [x] **Task 1** (`1762f91`): modelos `KeywordSignal`/`TrendPoint`/`DepthConfig`
  + `SegmentConfig.research` (`domain/models.py`, 4 testes novos).
- [x] **Task 2** (`0ac14a0`): `FanoutGuard` (`domain/fanout_guard.py`) — dedupe
  casefold, cap `max_nodes_per_cycle`, truncate top-N por peso (5 testes).
- [x] **Task 3** (`319b9f6`): Protocols `KeywordExpander`/`TrendsProvider`/
  `SocialDiscoverer`/`KeywordSignalRepository` (`app/ports.py`, 3 testes
  estruturais).
- [x] **Task 4** (`1e27099`): migração Alembic `0002_keyword_signals` +
  `KeywordSignalRow` (`infra/db/orm.py`) + `KeywordSignalRepositoryImpl`
  (`infra/db/repositories.py`, upsert por `(segment_id, keyword, source)`,
  3 testes @requires_infra; `alembic check` limpo).
- [x] **Task 5** (`579ab51` + fix `f2817fb`): use-case
  `app/research_pipeline.py::run_research_cycle` — lock por segmento,
  guard-rail nas seeds/trends, rate-limit por plataforma, falha de 1 rede
  social não aborta as demais (4 testes em `tests/app/test_research_pipeline.py`).
- [x] **Task 6**: gates completos verdes — pytest 152 passed (com
  `TEST_DATABASE_URL`), ruff check/format 0, pyright strict 0,
  `alembic check` ok, bandit exit 0 (B112 resolvido em `f2817fb`),
  pip-audit 0 vulns, `mkdocs build --strict` ok, privacidade ok (174
  arquivos).

### Sessão 2026-10-07 → 2026-10-10 (fundação + PR #3)

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
   INCR+EXPIRE (#3), detalhe de erro do score (#4) — **deferrals do collector
   Instagram (media_type, simetria de `#`, paginação do fallback) corrigidos
   em 2026-10-12, ver seção "Fase 0" abaixo**
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

## Fase 0 — fix do collector Instagram (2026-10-12)

Executada via subagent-driven-development, branch `fix/instagram-collector-fase0`,
ledger `.superpowers/sdd/vamos-atualizar-este-projeto-virtual-kernighan/progress.md`,
plano `docs/ai-plans/2026-10-12-fix-instagram-collector-fase0.md`.

- [x] **Task 2** (`bab58fa`): filtro `media_type` IMAGE — `discover`/`_own_media`
  passam a descartar posts sem vídeo (Reels analyzer só quer vídeo/carrossel).
- [x] **Task 3** (`7b6e79f`): simetria de `#` — keyword cadastrada como `#vela`
  agora casa legenda sem `#` (fix em `_own_media` e em `app/score.py::relevance_of`).
- [x] **Task 4** (`0b88d2c`): paginação do fallback `_own_media` — antes só
  buscava 1 página de 50 posts; agora pagina até `_MAX_OWN_MEDIA_PAGES=3`.
- [x] **Task 5** (`015927d`): telemetria `gate_hits` — `cf collect` expõe
  `{platform}_gate_hits` no envelope quando o fallback do gate #10 é acionado.
- [x] **Playtest real (Tasks 1+6)**: achado importante — o token
  `INSTAGRAM_ACCESS_TOKEN` em uso não era "gate #10 pendente" como
  documentado, era **token sem a permissão `instagram_basic`** (403 code 10,
  gerado pelo fluxo errado — "API com login do Instagram" em vez de "API com
  login do Facebook"). Investigado ao vivo no Meta for Developers (app
  `Automation-IvanaLereaBR`, id `4160317280965017`): feature "Instagram
  Public Content Access" nunca foi submetida para App Review ("Adicionar à
  análise do app", não "aguardando aprovação"); "Ações necessárias" do app
  vazio — sem bloqueio ativo da Meta. Token novo gerado (User Access Token,
  escopos `instagram_basic,business_management,pages_read_engagement,pages_show_list`,
  via OAuth direto — `Generate Access Token` do Graph Explorer estava com
  popup bloqueado pelo Chrome) e validado: `ig_hashtag_search` agora cai
  certinho no gate #10 documentado (não mais erro de permissão), `/media`
  retorna 46 posts reais.
- [x] **Worker real validado** (não só `cf collect`): subiu
  `content-factory-worker-1` (`docker compose --profile worker up`),
  enfileirou `collect_job`/`score_job` manualmente via arq (sem esperar o
  cron 08:00/20:00) — runs 154-156 `ok` nas 3 plataformas (web/youtube/
  instagram), `score_job scored=23`.
- [x] Gates completos verdes: pytest 120 passed (1 failed é
  `tests/e2e/test_00_prereq.py::test_postgres_up` — ambiente/`TEST_DATABASE_URL`
  ausente, não relacionado a código), ruff
  check/format 0, pyright strict 0 (local — dentro do `cf-python` falta
  `libatomic.so.1` para o node do pyright-python, ambiente, não bloqueante),
  bandit 0, pip-audit 0 vulns, `alembic check` ok, privacidade ok (162
  arquivos).
- [x] **Token estendido para 60 dias** (2026-10-12, via Access Token
  Debugger — "Estender token de acesso", expira 2026-12-11): substituído em
  `secrets.env` e `app.env`, revalidado com `cf collect` real (`ok=true,
  found=3, instagram_gate_hits=1`). Procedimento de extensão/renovação
  documentado em `docs/manual/chaves-api.md` §2 passos 9-10.
- ⚠️ **Pendência do usuário**: 60 dias é o máximo de um *User Access Token*
  — antes de 2026-12-11, repetir a extensão (passo 9 do manual) ou migrar
  para **System User token sem expiração** (passo 10) para não depender de
  renovação manual recorrente no cron de produção.
- Minors deferidos (não bloqueiam): condição redundante `not out and page == 0`
  em `_own_media` (Task 4); relatório da Task 4 sem evidência explícita do
  RED do TDD.
- [x] **Review final da branch corrigido** (2026-10-12): 2 Important + 3
  Minor. `app/collect.py` — `gate_hits` agora é lido no `finally` do loop por
  collector (não só no caminho de sucesso, então sobrevive a `discover()`
  levantando exceção DEPOIS de incrementar o contador) e reporta **delta
  before/after por run**, não o valor absoluto do atributo (o collector é
  singleton no registry — `self.gate_hits` do worker acumula entre runs);
  2 testes novos em `tests/app/test_collect.py`. Docs: `403 code 200` →
  `403 code 10` (batia com `PLANO_TESTE_E2E.md` Bug 5); âncoras
  `#2-instagram--graph-api-...` (`--` duplo) → `#2-instagram-graph-api-...`
  (`-` simples, confirmado contra o slug real do mkdocs) em
  `PLANO_TESTE_E2E.md` e `manual/chaves-api.md`; contagem de testes
  atualizada (117→120 passed, 1 failed ambiente).
