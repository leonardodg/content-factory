# Manual de Operação (docs/manual/) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o manual de operação em 8 capítulos (`docs/manual/`) cobrindo primeiro passo, lógica das buscas, operação, runbook de agente de IA, análise SQL, anti-ban e chaves API — aprovado no spec `docs/superpowers/specs/2026-10-09-manual-operacao-design.md`.

**Architecture:** Documentação pura (markdown) + nav do mkdocs. Capítulos são autossuficientes com links para os docs dev existentes (`docs/dev/credentials.md`, `docs/dev/dev-environment.md`) em vez de duplicar. Fatos técnicos (comandos, janelas de rate-limit, códigos de erro, schema) vêm dos arquivos fonte do repo citados em cada task — o implementador confere antes de escrever.

**Tech Stack:** Markdown + MkDocs Material (nav por arquivos em `mkdocs.yml`), gates `mkdocs build --strict` e `scripts/check_privacy.py`.

## Global Constraints

- **Idioma**: português (pt-BR), mesmo padrão dos docs existentes.
- **Docs puros**: NENHUM código Python/CLI novo; exceto `mkdocs.yml` (nav) e `docs/roteamento.md` (1 linha), nenhum arquivo fora `docs/manual/` muda.
- **Sem duplicação**: camadas de env/setup → link para `docs/dev/credentials.md` / `docs/dev/dev-environment.md`, nunca copiar.
- **Nenhum segredo/placeholder indevido**: `uv run python scripts/check_privacy.py` deve passar (sem valores reais de chave; placeholders `SUA_CHAVE_AQUI` ok).
- **Comandos literais devem funcionar**: todo comando mostrado é copiado do estado real do repo (fonte indicada em cada task); exit codes conferem com `docs/specs/trd-cli.md` (0=ok, 2=parcial, 1=erro).
- **Gates antes de cada commit**: `uv run mkdocs build --strict` (exit 0) + `uv run python scripts/check_privacy.py` (exit 0).
- **Conventional Commits** com corpo do porquê.
- **Gaps documentados, não escondidos**: `video_metrics` sem captura em produção; 4 backlogs da `docs/REVISAO_2026-10-07.md` que afetam operação; `defaults.env` não montado no compose; circuit breaker do rate-limit não ligado no `collect`.

**Fontes de verdade para fatos (conferir antes de escrever):**

| Fato | Arquivo-fonte |
|---|---|
| Comandos/flags/exit codes da CLI | `src/content_factory/cli/main.py`, `src/content_factory/cli/output.py`, `docs/specs/trd-cli.md` |
| Fluxo do collect (lock→upsert→audit) | `src/content_factory/app/collect.py` |
| Janelas de rate-limit | `src/content_factory/infra/ratelimit.py::DEFAULT_WINDOWS` |
| Erros por plataforma | `docs/specs/trd-collectors.md` + `src/content_factory/infra/collectors/*.py` |
| Worker/cron/envs | `src/content_factory/infra/queue/worker.py` |
| Compose/serviços | `.devcontainer/docker-compose.yml` |
| Schema/queries | `docs/data-model/README.md`, `src/content_factory/infra/db/orm.py` |
| Chaves/escopo/rotação | `docs/dev/credentials.md` |
| Transcrição real do playtest | `docs/PLANO_TESTE_E2E.md` |
| Backlogs operacionais | `docs/REVISAO_2026-10-07.md` §6 |

---

### Task 1: Scaffold do manual (nav + index + roteamento)

**Files:**
- Create: `docs/manual/index.md`
- Modify: `mkdocs.yml` (nav: seção "Manual" com os 8 arquivos — os 7 que ainda não existem serão criados nas Tasks 2–8; criar o nav completo agora e cada task cria seu arquivo)
- Modify: `docs/roteamento.md` (+1 linha na tabela apontando para `docs/manual/index.md`)

**Interfaces:**
- Produces: caminho canônico `docs/manual/<capitulo>.md` que Tasks 2–8 devem seguir. Nav do mkdocs lista: `index`, `guide-start`, `fluxo-busca`, `operacao`, `agentes`, `analise`, `anti-ban`, `chaves-api`.

- [ ] **Step 1: Ler a estrutura nav atual do mkdocs.yml**

Run: `grep -A 40 "^nav:" mkdocs.yml`
Obs.: padrão "nav por arquivos" do projeto — manter estilo/idêntação existentes.

- [ ] **Step 2: Escrever `docs/manual/index.md`** (~80 linhas), contendo:
  1. Título + 1 parágrafo: o que é o manual (operação do content-factory, público humano + agente).
  2. Tabela de roteamento: "Quero…" → capítulo. Linhas: rodar a primeira busca → `guide-start.md`; entender a lógica das buscas → `fluxo-busca.md`; subir/verificar o serviço e logs → `operacao.md`; configurar agente de IA → `agentes.md`; analisar resultados → `analise.md`; evitar ban → `anti-ban.md`; criar chaves das APIs → `chaves-api.md`.
  3. Diagrama do pipeline (mermaid): `segments YAML → cf collect (lock→limiter→discover→upsert) → videos/video_segments/collect_runs → cf score → video_scores → cf status`.
  4. Tabela verdade dos comandos (conferir em `trd-cli.md`): `cf ping` (sem DB), `cf segment load PATH`, `cf collect --segment SLUG [--platform a,b] [--limit N]` (1–50, default 10), `cf ingest --url URL [--segment SLUG]`, `cf score --segment SLUG`, `cf status --segment SLUG`; coluna exit codes: 0=ok, 2=parcial, 1=erro; nota de que a CLI roda dentro do container: `docker exec cf-python uv run cf <cmd>`.

- [ ] **Step 3: Adicionar seção Manual no nav do mkdocs.yml**

```yaml
  - Manual:
      - manual/index.md
      - manual/guide-start.md
      - manual/fluxo-busca.md
      - manual/operacao.md
      - manual/agentes.md
      - manual/analise.md
      - manual/anti-ban.md
      - manual/chaves-api.md
```
(Ajustar ao estilo do nav existente; se o mkdocs estritar por arquivo inexistente, criar os 7 arquivos como stub `# <Título>` — serão preenchidos nas Tasks 2–8. NÃO deixar nav quebrado entre commits.)

- [ ] **Step 4: +1 linha em `docs/roteamento.md`**: na tabela de roteamento, "operar o serviço / manual completo" → `manual/index.md`.

- [ ] **Step 5: Gates**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py`
Expected: exit 0 ambos.

- [ ] **Step 6: Commit**

```bash
git add docs/manual/ mkdocs.yml docs/roteamento.md
git commit -m "docs(manual): scaffold com index, nav mkdocs e roteamento

Cria docs/manual/index.md (roteamento, pipeline, tabela dos comandos cf)
e registra os 8 capitulos no nav do mkdocs. Ponto de entrada do manual
para humano e agente de IA."
```

---

### Task 2: guide-start.md — primeiro passo

**Files:**
- Create: `docs/manual/guide-start.md` (~150 linhas)

**Interfaces:**
- Consumes: `index.md` (link de volta), comandos verificados em `trd-cli.md` e transcrição de `docs/PLANO_TESTE_E2E.md`.
- Produces: caminho de setup que `operacao.md` (Task 4) referencia.

- [ ] **Step 1: Conferir a transcrição real do playtest**

Run: `sed -n '1,80p' docs/PLANO_TESTE_E2E.md`
Obs.: copiar daí os comandos literalmente (já validados em 2026-10-09).

- [ ] **Step 2: Escrever `docs/manual/guide-start.md`** com passos numerados, cada um com comando + saída esperada:
  1. Pré-requisitos: Docker + Docker Compose; chaves (link → `chaves-api.md`; sem chaves dá para começar só com `web`).
  2. Env: `cp .devcontainer/env/app.env.example .devcontainer/env/app.env` e `cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env` (valores vazios ok para o primeiro teste com `web`).
  3. Subir stack: `docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python` + `docker compose -f .devcontainer/docker-compose.yml exec python uv run alembic upgrade head`.
  4. `docker exec cf-python uv run cf ping` → esperar `{"ok": true, "command": "ping"}`.
  5. `docker exec cf-python uv run cf segment load segments/candles.yaml` → `{"ok": true, ..., "slug": "candles", ...}`.
  6. `docker exec cf-python uv run cf collect --segment candles --platform web --limit 3` → `{"ok": true, ..., "inserted": 3, ...}` exit 0.
  7. `docker exec cf-python uv run cf score --segment candles` → `{"ok": true, ..., "scored": N}`.
  8. `docker exec cf-python uv run cf status --segment candles` → `{"ok": true, ..., "runs": {...}}`.
  9. Seção "Primeiras falhas": exit 2 = parcial (ex.: plataformas sem chave → `failed>0`, códigos `auth_expired`/`rate_limited`); exit 1 = erro (`segment_not_found`, `ja_em_execucao`); onde olhar: `collect_runs.error_code` (link → `analise.md`).
  10. Nota: CLI roda dentro do container (Redis sem porta pública); próximo passo → `operacao.md` / `fluxo-busca.md`.

- [ ] **Step 3: Gates**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/manual/guide-start.md
git commit -m "docs(manual): guia do primeiro passo ate a primeira coleta verde

Sequencia copiavel do zero ao cf status com saidas esperadas e
decodifica dos exit codes 0/2/1, baseada no playtest ja validado."
```

---

### Task 3: fluxo-busca.md — a lógica das buscas

**Files:**
- Create: `docs/manual/fluxo-busca.md` (~200 linhas)

**Interfaces:**
- Consumes: fatos de `app/collect.py`, `infra/collectors/*.py`, `infra/ratelimit.py` (conferir em Step 1).
- Produces: vocabulário (tabelas, códigos de erro) usado por `analise.md` e `agentes.md`.

- [ ] **Step 1: Conferir fatos nos fontes**

Run: `grep -n "DEFAULT_WINDOWS\|Window(" src/content_factory/infra/ratelimit.py | head` e `grep -n "CollectorError\|RateLimited\|AuthExpired\|blocked" src/content_factory/infra/collectors/*.py | head -30`
Obs.: anotar as janelas exatas e os mapeamentos de erro por plataforma.

- [ ] **Step 2: Escrever `docs/manual/fluxo-busca.md`** com:
  1. **Visão geral do `cf collect`**: lock Redis `collect:{segment}` (TTL 30min; se ocupado → `ja_em_execucao`, exit 1) → lê segmento → para cada plataforma: `limiter.acquire(platform)` → `collector.discover(segment, limit)` → upsert → no `finally` grava `collect_runs` (status `ok`/`failed`/`blocked`, `error_code`, contagens).
  2. **Seção por plataforma** (tabela: fonte, credencial, janela, erros):
     - **YouTube**: YouTube Data API v3 (`search` custo 100 + `videos` custo 1; cota 10.000 U/dia); `YOUTUBE_API_KEY`; janela conforme DEFAULT_WINDOWS (conferido); `403` → `rate_limited`; `>=400`/JSON estranho → `upstream_changed`; rede → `network`.
     - **TikTok**: TikTok-Api + Playwright headless; cookie `ms_token`; sem token → `auth_expired`; `EmptyResponseException` (anti-bot) → retry `2**attempt + jitter(0..2s)`, máx. 5 → `blocked`; proxy opcional `TIKTOK_PROXY`.
     - **Instagram**: Graph API `/{ig_user_id}/hashtags` → `top_media`; `INSTAGRAM_ACCESS_TOKEN`; janela 20/60s; `400/401` → `auth_expired`; header `X-App-Usage` só informativo; erros passam por `redact` (token nunca vaza).
     - **Web**: DuckDuckGo (`ddgs`), sem credencial; janela 10/60s; `platform_video_id = sha1(url)` (≤64 chars).
  3. **Workflow até o banco** (mermaid ou passos): tabelas escritas — `videos` (INSERT…ON CONFLICT (platform, platform_video_id)), `video_segments` (vídeo↔segmento), `collect_runs` (auditoria). Depois `cf score` lê `videos`+`video_segments` ⋈ `video_metrics` (LEFT JOIN), calcula `viral_score`/`trend_decay` (domínio puro) e grava `video_scores` (append timeseries, SAVEPOINT por vídeo).
  4. **Verdade documentada**: `video_metrics` ainda não é capturada por produção (só testes) → score usa zero-fill; scores atuais são placeholder até a fase de captura de métricas. `cf ingest` é rota alternativa para URL avulsa (guarda SSRF).
  5. Próximo: `operacao.md` (rodar) ou `anti-ban.md` (limites).

- [ ] **Step 3: Gates**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/manual/fluxo-busca.md
git commit -m "docs(manual): logica das buscas por plataforma ate o banco

Passo a passo do cf collect (lock, limiter, discover, upsert, audit),
janelas e erros por plataforma, workflow de score, e o gap honesto do
video_metrics sem captura em producao."
```

---

### Task 4: operacao.md — status, logs, worker

**Files:**
- Create: `docs/manual/operacao.md` (~150 linhas)

**Interfaces:**
- Consumes: compose real (`.devcontainer/docker-compose.yml`), worker (`infra/queue/worker.py`).
- Produces: rotina de status usada pelo `agentes.md` (Task 5).

- [ ] **Step 1: Conferir serviços e envs do worker**

Run: `grep -n "profiles\|container_name\|COLLECT_\|cron" .devcontainer/docker-compose.yml src/content_factory/infra/queue/worker.py | head -30`

- [ ] **Step 2: Escrever `docs/manual/operacao.md`** com:
  1. **Subir stack**: base (`up -d postgres redis python`); worker: `docker compose -f .devcontainer/docker-compose.yml --profile worker up -d worker`.
  2. **Status dos serviços**: `docker compose -f .devcontainer/docker-compose.yml ps` (esperar `Up`/`healthy`); `cf ping`; `cf status --segment candles`; como confirmar Postgres (`exec python uv run alembic current`) e Redis (`exec redis redis-cli ping` → `PONG`).
  3. **Logs**:
     - Worker: `docker compose -f .devcontainer/docker-compose.yml logs -f worker` — o que procurar: `Starting worker for N functions: collect_job, score_job`, erros de job, `SegmentNotFound` no startup (fail-fast), `shutdown on SIGTERM`.
     - CLI/collect: erros aparecem no envelope; auditoria persistida em `collect_runs` (query → link `analise.md`).
     - Container python: `docker logs cf-python`.
  4. **Worker e cron**: horários BRT — collect 08:00/20:00, score 08:30/21:30 (`ZoneInfo America/Sao_Paulo`); envs do worker: `COLLECT_SEGMENT` (default `candles`), `COLLECT_PLATFORMS` (vazio = plataformas do segmento), `COLLECT_LIMIT` (default 10), `COLLECT_CRON_HOURS` (default `08,20`); onde mudar (environment do serviço worker no compose); `max_jobs=1` (1 job por vez — não é bug).
  5. **Limitações conhecidas**: `defaults.env` versionado mas não montado no compose (defaults valem do código); lock de 30 min; worker falha rápido se o segmento não existir (`cf segment load` antes).
  6. **Parada/restart/atualização**: `down`, `up -d` após pull; rebuild do serviço `python` após mudanças de código.

- [ ] **Step 3: Gates**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/manual/operacao.md
git commit -m "docs(manual): operacao do servico — status, logs e worker cron

Subir stack e worker, comandos de status, onde ficam os logs do arq,
horarios BRT do cron, envs do worker e limitacoes conhecidas."
```

---

### Task 5: agentes.md — runbook do agente de IA

**Files:**
- Create: `docs/manual/agentes.md` (~180 linhas)

**Interfaces:**
- Consumes: `operacao.md` (status/logs), `analise.md` (queries — linkar, não repetir), contrato de envelope/exit codes do `index.md`.
- Produces: o capítulo que o usuário cola no prompt/system do agente.

- [ ] **Step 1: Conferir códigos de erro e contrato**

Run: `grep -n "error.*code\|auth_expired\|rate_limited\|blocked\|ja_em_execucao" docs/specs/trd-cli.md | head -20`

- [ ] **Step 2: Escrever `docs/manual/agentes.md`** com:
  1. **Modelo**: worker arq faz o agendamento diário (08:00/20:00 BRT); o agente é **operador + analista** — monitora, diagnostica, analisa, escala. O agente NÃO agenda coletas periódicas nem chama APIs direto.
  2. **Rotina diária** (passos com comando exato + critério):
     - Passo 1 — Saúde: `docker exec cf-python uv run cf ping` (exit 0?) + `docker compose -f .devcontainer/docker-compose.yml ps` (tudo `Up`?) + `cf status --segment candles`. Critério ok: ping ok, serviços up, `runs.ok >= 1` na janela. Senão → Passo 2.
     - Passo 2 — Diagnóstico da última coleta: query `SELECT collector, status, error_code, started_at, found, inserted, failed FROM collect_runs ORDER BY started_at DESC LIMIT 5;` (psql via `docker compose -f .devcontainer/docker-compose.yml exec -it postgres psql -U contentfactory -d contentfactory`). Tabela decisão por `error_code`:
       | code | ação do agente |
       |---|---|
       | `auth_expired` | NÃO retry; reportar ao humano para rotacionar a chave (link `chaves-api.md`) |
       | `rate_limited` | aguardar a janela; retry manual só na próxima janela |
       | `blocked` | NÃO insistir (circuit 15 min); reportar ao humano |
       | `network` | 1 retry manual após 60s; persistindo → reportar |
       | `upstream_changed` | reportar (API mudou — tarefa de dev) |
       | `ja_em_execucao` | worker provavelmente rodando; ok, não fazer nada |
     - Passo 3 — Scores: query top-10 (igual à de `analise.md` — linkar).
     - Passo 4 — Escalonar quando: 2+ dias com `status=failed`; qualquer `auth_expired`; `blocked` recorrente; serviços `Down`. Formato do relato: envelope/command + error_code + horário.
  3. **Contrato de saída**: envelope JSON é a interface; exit 0 → segue, 2 → avalia (partial, olha `failed`), 1 → para e diagnostica.
  4. **Regras de segurança do agente** (5): (a) nunca logar/imprimir valores de `secrets.env`/env vars de credencial; (b) nunca retry forçado em `blocked`; (c) nunca rodar 2 `cf collect` do mesmo segmento (lock já bloqueia — respeitar `ja_em_execucao`); (d) banco só via `docker compose exec` (Postgres só em 127.0.0.1); (e) não alterar `secrets.env` sem pedido humano.
  5. **Setup do agente**: o que incluir no prompt — link para este capítulo + nota de que o repo tem `CLAUDE.md`/`AGENTS.md` apontando ao `HANDOFF.md`; escopo do agente (lê docs/banco, roda comandos `cf` somente leitura/coleta, não edita código sem pedido).

- [ ] **Step 3: Gates**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/manual/agentes.md
git commit -m "docs(manual): runbook do agente de IA como operador diario

Rotina de 4 passos com comandos literais e tabela decisoria por
error_code, contrato de envelope/exit codes, 5 regras de seguranca do
agente e instrucoes de setup no prompt."
```

---

### Task 6: analise.md — queries SQL prontas

**Files:**
- Create: `docs/manual/analise.md` (~150 linhas)

**Interfaces:**
- Consumes: schema real (`docs/data-model/README.md` / `orm.py`).
- Produces: queries reutilizadas por `agentes.md`.

- [ ] **Step 1: Conferir colunas do schema**

Run: `grep -n "class.*Base\|mapped_column\|__tablename__" src/content_factory/infra/db/orm.py | head -60`
Obs.: confirmar nomes exatos: `video_scores.viral_score`, `video_scores.computed_at`, `collect_runs.{status,error_code,started_at,found,inserted,failed}`, `videos.{platform,title,platform_video_id}`, `video_segments.segment_id`.

- [ ] **Step 2: Escrever `docs/manual/analise.md`** com:
  1. **Acesso**: `docker compose -f .devcontainer/docker-compose.yml exec -it postgres psql -U contentfactory -d contentfactory` (ou dentro do container python via `exec python … psql` se houver; usar o que existir de fato — conferir imagem pgvector tem psql, sim).
  2. Por query: título + 1 linha "o que responde" + bloco SQL:
     - **Top scores do segmento** (top 10): join `video_scores` ⋈ `videos` ⋈ `segments` filtrando `segments.slug='candles'`, ordenando `viral_score DESC`, mostrando `title, platform, viral_score, relevance, recency, computed_at`.
     - **Evolução da timeseries**: `SELECT computed_at, viral_score FROM video_scores … ORDER BY computed_at` (últimos N) — evolução temporal do score.
     - **Saúde das coletas 7 dias**: `SELECT collector, status, count(*), sum(found), sum(inserted), sum(failed) FROM collect_runs WHERE started_at > now() - interval '7 days' GROUP BY collector, status ORDER BY collector;`.
     - **Últimos erros**: `SELECT collector, error_code, started_at FROM collect_runs WHERE status <> 'ok' ORDER BY started_at DESC LIMIT 10;`.
     - **Vídeos sem métricas (zero-fill)**: `SELECT count(*) FROM videos v LEFT JOIN video_metrics m ON m.video_id = v.id WHERE m.video_id IS NULL;` — com aviso: métricas ainda não capturadas em produção; score desses vídeos é placeholder (link `fluxo-busca.md`).
     - **Cobertura do segmento**: quantos vídeos vinculados (`video_segments`) vs pontuados (`video_scores`).
  3. Rodapé: sair com `\q`; envelope da CLI para operação (esta página é para análise).

- [ ] **Step 3: Validar sintaxe das queries (as que não dependem de dados)**

Run: `docker compose -f .devcontainer/docker-compose.yml exec -t postgres psql -U contentfactory -d contentfactory -c "EXPLAIN <top-score-query>;"`
Expected: OK sem erro de sintaxe (dados podem ser 0 linhas — ok). Repetir para as demais (EXPLAIN não precisa de rows).

- [ ] **Step 4: Gates**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add docs/manual/analise.md
git commit -m "docs(manual): queries SQL prontas para analisar resultados

Top viral_score, evolucao da timeseries, saude das coletas em 7 dias,
ultimos erros por error_code e videos sem metricas (zero-fill), todas
validadas com EXPLAIN no banco do compose."
```

---

### Task 7: anti-ban.md — segurança operacional

**Files:**
- Create: `docs/manual/anti-ban.md` (~120 linhas)

**Interfaces:**
- Consumes: `infra/ratelimit.py` (janelas/backoff), `docs/REVISAO_2026-10-07.md` §6 (backlogs), `docs/dev/credentials.md` (conta secundária).

- [ ] **Step 1: Conferir janelas/backoff reais**

Run: `sed -n '1,80p' src/content_factory/infra/ratelimit.py`

- [ ] **Step 2: Escrever `docs/manual/anti-ban.md`** com:
  1. **O que o sistema já faz por você** (tabela): janela YouTube 100/dia (conferir DEFAULT_WINDOWS), TikTok 6/min, Instagram 20/60s, web 10/60s; backoff exponencial `2**attempt` ±20% jitter, máx. 5 tentativas, aborta se espera >60s (`rate_limited`); lock distribuído (coleta única por segmento); `redact` de tokens em todo erro de CLI; retry anti-bot do TikTok (5×, `blocked` no fim); fail-fast de `auth_expired` (sem retry cego).
  2. **Regras do operador**: conta secundária no TikTok sempre (nunca a principal); rotacionar token ao primeiro `auth_expired` (link `chaves-api.md`); `--limit` gradual (começar 3–10; 50 é teto, não default); YouTube: search custa 100/10.000 U → ~100 buscas/dia — não subir `COLLECT_LIMIT` sem ver cota; não rodar coleta manual durante o cron do worker (lock evita duplicata, mas evite `ja_em_execucao` confuso).
  3. **Limitações honestas (backlogs)**: circuit breaker existe no código mas **não está ligado** no fluxo de `collect` (só em testes) — 3 falhas não bloqueiam automaticamente; `INCR+EXPIRE` do rate-limit não atômico (janela pode ficar sem TTL); pausa 2–8s entre requests do design §7 não implementada (só backoff em falha); mascaramento: erro raiz às vezes escondido pelo `finally` do collect (backlog #1 da REVISAO).
  4. **Sintomas de ban/limite → ação**: `rate_limited` seguidos no YouTube → esperar reset diário da cota (meia-noite PT do Google); `blocked` no TikTok → suspeita de anti-bot, pausar 15+ min, trocar sessão/proxy; Instagram 400/401 → token expirou, rotacionar; muitos `network` → problema local/VPN/-proxy, não da plataforma.

- [ ] **Step 3: Gates**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/manual/anti-ban.md
git commit -m "docs(manual): guia anti-ban com protecoes reais e limitacoes honestas

Janelas/backoff/lock que ja existem, regras do operador por plataforma
e os 4 backlogs que afetam operacao direta (breaker, INCR+EXPIRE,
pausa 2-8s, mascaramento de erro)."
```

---

### Task 8: chaves-api.md — criar as chaves + fechamento

**Files:**
- Create: `docs/manual/chaves-api.md` (~180 linhas)
- Modify: `docs/manual/index.md` (se algum link interno faltar — checagem final)

**Interfaces:**
- Consumes: `docs/dev/credentials.md` (escopo, rotação, camadas de env — LINKAR, não copiar).
- Produces: capítulo final; fecha o manual.

- [ ] **Step 1: Conferir credentials.md e exemplos de env**

Run: `sed -n '1,120p' docs/dev/credentials.md && cat .devcontainer/secrets/secrets.env.example`

- [ ] **Step 2: Escrever `docs/manual/chaves-api.md`** com passo a passo por plataforma:
  1. **YouTube** (Data API v3): 1) acesse console.cloud.google.com → criar projeto; 2) APIs & Services → Library → habilitar "YouTube Data API v3"; 3) Credentials → Create credentials → API key; 4) restringir a chave (Application restriction: IP/apps que usar; API restriction: só YouTube Data API v3 — escopo mínimo); 5) copiar para `YOUTUBE_API_KEY` no env da camada certa (link → `credentials.md`); 6) verificar: `cf collect --platform youtube --limit 1` → se `rate_limited` com 403 logo de cara, chave mal habilitada/restrita. Cota: 10.000 U/dia.
  2. **Instagram** (Graph API): 1) developers.facebook.com → My Apps → Create App (Business type); 2) adicionar produto "Instagram Graph API"; 3) conta Instagram **Business/Creator** vinculada a uma Page do Facebook; 4) obter `IG_USER_ID` (Graph API Explorer / conta vinculada) e access token de longa duração (System User token com permissão `instagram_basic` — ou token de teste de 24h para começar); 5) preencher `INSTAGRAM_ACCESS_TOKEN` + `IG_USER_ID`; 6) verificar: `cf collect --platform instagram --limit 1` → `auth_expired` = token errado/expirado. Aviso: token de teste expira em 24h — produção precisa de token duradouro + rotação (tabela em `credentials.md`).
  3. **TikTok** (sem API oficial): 1) criar **conta secundária** (nunca a principal — regra da casa); 2) logar no navegador com essa conta; 3) extrair o cookie `ms_token` (DevTools → Application → Cookies → `ms_token`); 4) colar em `TIKTOK_MS_TOKEN`; 5) `TIKTOK_PROXY` opcional; 6) verificar: `cf collect --platform tiktok --limit 1` → `auth_expired` = token ausente/expirado (rotacionar), `blocked` = anti-bot (pausar). Aviso: coleta via sessão Playwright = risco de bloqueio; conta descartável é o seguro.
  4. **Onde colar** (link, não duplicar): tabela das 3 camadas (`secrets.env` worker / `app.env` python / `.env` raiz) → `docs/dev/credentials.md`; placeholders nunca commitados.
  5. **Verificação final**: `uv run python scripts/check_privacy.py` → exit 0 antes de subir qualquer serviço.
  6. Tabela sintoma → ação (reaproveitando credentials.md): repetir só o essencial e linkar a fonte.

- [ ] **Step 3: Conferir links do index.md**

Run: `grep -o "](.*\.md)" docs/manual/index.md` e checar que cada destino existe (`ls docs/manual/`).

- [ ] **Step 4: Gates (finais do manual)**

Run: `uv run mkdocs build --strict && uv run python scripts/check_privacy.py && uv run ruff check .`
Expected: exit 0 todos.

- [ ] **Step 5: Commit**

```bash
git add docs/manual/
git commit -m "docs(manual): guia de chaves API por plataforma e fechamento

Passo a passo YouTube Data API, Instagram Graph API e TikTok (ms_token),
sempre linkando credentials.md como fonte, com verificacao de cada chave
via cf collect e check_privacy como backstop."
```

---

## Notas de execução

- Tasks são independentes por capítulo, mas a ordem 1→8 segue os links internos (index precisa dos stubs; agentes/analise referenciam operacao/fluxo).
- Se `mkdocs --strict` reclamar de link quebrado entre commits, criar stub da Task pendente primeiro.
- Nenhuma task mexe em código Python; qualquer tentativa de "melhorar" a CLI está fora do escopo (spec: docs puros).
