# Design — Manual de operação content-factory (guide-start + runbook de agente)

- **Data**: 2026-10-09
- **Status**: aprovado em design (seções 1–2), aguardando revisão do spec
- **Escopo**: documentação pura em `docs/manual/` (8 arquivos) + nav do mkdocs. Sem código novo.

## Objetivo

Um manual completo de operação do content-factory para dois públicos: o **humano**
(quer entender a lógica das buscas, rodar o serviço, configurar chaves, evitar ban)
e o **agente de IA** (operador diário: verificar status, acompanhar logs, analisar
resultados, decidir escalonamento). Origem: pedido do usuário em 2026-10-09 após a
entrega da fundação (PR #1).

## Decisões tomadas (brainstorming)

1. **Leitor**: humano + agente — manual em `docs/manual/` e um capítulo-runbook
   (`agentes.md`) em formato que o agente segue direto.
2. **Agendamento diário**: **worker arq existente** (cron 08:00/20:00 BRT) é quem
   dispara a busca; o agente monitora e analisa, não agenda.
3. **Análise**: **queries SQL prontas** para copiar/colar (sem comando novo `cf report`).
4. **Estrutura**: abordagem A — manual em capítulos (aprovada pelo usuário).

## Arquivos

```
docs/manual/
├── index.md          # Roteamento + diagrama do pipeline + tabela verdade dos comandos cf
├── guide-start.md    # Primeiro passo: do zero ao primeiro coletor verde
├── fluxo-busca.md    # Lógica das buscas por plataforma + workflow até o banco
├── operacao.md       # Subir serviços, status, logs, worker/cron
├── agentes.md        # Runbook do agente de IA (operador diário)
├── analise.md        # Queries SQL prontas de análise
├── anti-ban.md       # Segurança operacional / não levar ban
└── chaves-api.md     # Como criar chaves: YouTube, Instagram, TikTok
```

`mkdocs.yml` nav ganha seção "Manual" com os 8 arquivos; `mkdocs build --strict`
deve continuar verde. `docs/roteamento.md` ganha linha apontando para o manual.

## Conteúdo por capítulo

### 1. `index.md` (~80 linhas)
- Tabela de roteamento ("quero X" → capítulo Y).
- Diagrama do pipeline: `segmento YAML → cf collect → videos/video_segments/collect_runs → cf score → video_scores → cf status`.
- Tabela verdade dos comandos `cf` (ping, segment load, collect, ingest, score, status)
  com flags, exit codes 0/2/1 e envelope JSON.

### 2. `guide-start.md` (~150 linhas)
Passos numerados e copiáveis, com saída esperada de cada comando:
1. Pré-requisitos (Docker, chaves — link a `chaves-api.md`).
2. Copiar `.devcontainer/env/app.env.example` → `app.env` e
   `.devcontainer/secrets/secrets.env.example` → `secrets.env`.
3. `docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python`
   + `uv run alembic upgrade head`.
4. `cf ping` → `cf segment load segments/candles.yaml` →
   `cf collect --platform web --limit 3` → verificar `{"ok": true, …, "inserted": 3}`.
5. `cf score --segment candles` → `cf status --segment candles`.
6. Primeiras falhas comuns: exit 2 = parcial (chaves faltando etc.), códigos de erro.
- CLI roda **dentro** do container: `docker exec cf-python uv run cf …`
  (Redis não publica porta no host).
- Link para `docs/dev/dev-environment.md` (público contribuidor, não operador).

### 3. `fluxo-busca.md` (~200 linhas)
- O que acontece quando `cf collect` roda, passo a passo:
  lock Redis `collect:{segment}` → leitura do segmento → por plataforma:
  `limiter.acquire` → `collector.discover` → upsert idempotente
  (`videos` ON CONFLICT + `video_segments`) → `collect_runs` auditado no `finally`.
- Por plataforma:
  - **YouTube**: YouTube Data API v3 (`search` custo 100 + `videos` custo 1);
    janela 100/dia; `403` → `rate_limited`.
  - **TikTok**: TikTok-Api + Playwright headless + cookie `ms_token`;
    `EmptyResponseException` → retry backoff `2**attempt + jitter`, máx. 5 → `blocked`;
    sem token/expirado → `auth_expired`.
  - **Instagram**: Graph API `hashtags/top_media`; janela 20/60s;
    `400/401` → `auth_expired`; `X-App-Usage` só informativo; erros redigidos (`redact`).
  - **Web**: DuckDuckGo via `ddgs`, sem chave; janela 10/60s;
    `platform_video_id = sha1(url)`.
- Workflow até o banco: tabelas escritas (`segments` lida; `videos`,
  `video_segments`, `collect_runs` escritas) e depois `cf score` →
  `video_scores` (append timeseries, SAVEPOINT por vídeo).
- **Verdade documentada**: `video_metrics` não é capturada por produção ainda;
  `cf score` usa LEFT JOIN com zero-fill — scores atuais são placeholder até a
  fase de captura de métricas.

### 4. `operacao.md` (~150 linhas)
- Subir stack base e worker (perfil `worker`): comandos compose literais.
- **Status dos serviços**: `docker compose ps`, `cf ping`, `cf status --segment X`,
  como confirmar Postgres/Redis.
- **Logs**: `docker compose logs -f worker` (erros de job, startup, shutdown),
  `docker logs cf-python`, diagnóstico via `collect_runs.error_code`.
- Worker/cron: horários (`08:00`/`20:00` collect, `08:30`/`21:30` score, BRT),
  env `COLLECT_SEGMENT`, `COLLECT_PLATFORMS`, `COLLECT_LIMIT`, `COLLECT_CRON_HOURS`
  (default embutido `08,20` — `defaults.env` não está montado, limitação conhecida).
- Parada/restart/atualização (rebuild após mudanças de código).

### 5. `agentes.md` (~180 linhas) — runbook do agente
- **Modelo**: worker cuida do agendamento; agente = operador + analista.
- **Rotina diária** (passos + comandos exatos + critérios):
  1. Saúde: `cf ping`, `docker compose ps`, `cf status` — ok vs investigar.
  2. Resultado da última coleta: query em `collect_runs` + tabela decisão por
     `error_code` (`auth_expired` → rotacionar chave; `rate_limited` → esperar;
     `blocked` → não insistir 15 min; `network` → retry manual).
  3. Scores: query top-10 `viral_score`.
  4. Escalonamento: quando o agente age vs reporta ao humano.
- **Contrato**: envelope JSON da CLI + exit codes (0 segue / 2 avalia / 1 para).
- **Segurança do agente**: nunca logar secrets; nunca retry forçado em `blocked`;
  nunca 2 colets simultâneos (lock já protege); acesso ao banco só via
  `docker exec` (nunca expor porta Postgres além de 127.0.0.1).
- **Setup**: o que incluir no prompt/system do agente (link pro capítulo; o repo
  já tem `CLAUDE.md`/`AGENTS.md` apontando ao HANDOFF).

### 6. `analise.md` (~150 linhas)
Acesso: `docker exec -it cf-postgres psql -U contentfactory -d contentfactory`.
Cada query com 1 linha de "o que isso responde":
- Top `viral_score` por segmento (título, plataforma, relevância).
- Evolução da timeseries `video_scores`.
- Saúde das coletas: ok/failed/blocked por plataforma (janela 7 dias).
- Vídeos sem captura de métricas (zero-fill) — aviso do gap.
- Diagnóstico por `error_code` em `collect_runs`.

### 7. `anti-ban.md` (~120 linhas)
- O que o sistema já faz: janelas Redis (YT 100/dia, TT 6/min, IG 20/min,
  fallback 10/min), backoff exponencial ±jitter (máx. 5), lock anti-simultâneo,
  `redact` de tokens, retry do TikTok.
- Regras do operador: conta secundária TikTok; não explodir `--limit` (max 50);
  YouTube cota 10.000 U/dia (100 searches); `auth_expired` = rotacionar, não insistir.
- **Limitações honestas** (backlogs da REVISAO): circuit breaker não ligado no
  `collect`; `INCR+EXPIRE` não atômico; pausa 2–8s do design §7 não implementada;
  mascaramento de erro no `finally` do collect.
- Sintomas iniciais de ban → ações.

### 8. `chaves-api.md` (~180 linhas)
- **YouTube**: Google Cloud Console → projeto → YouTube Data API v3 → API key
  restringida à API → `YOUTUBE_API_KEY` → verificação com `cf collect`.
- **Instagram**: developers.facebook.com → app → Graph API → conta Business/Creator
  → token (system user longa duração; rotação) → `INSTAGRAM_ACCESS_TOKEN` +
  `IG_USER_ID` → verificação.
- **TikTok**: sem API oficial → **conta secundária** → extrair cookie `ms_token` →
  `TIKTOK_MS_TOKEN` → `auth_expired` = rotacionar. Aviso anti-bot.
- Onde colar (3 camadas de env): **link** para `docs/dev/credentials.md`
  (fonte da verdade; não duplicar) + `check_privacy.py` como verificação final.
- Tabela sintoma → ação de rotação (reaproveitando credentials.md).

## Decisões transversais

- **Nav/mkdocs**: seção "Manual" no `mkdocs.yml`; `mkdocs --strict` verde é gate.
- **Sem duplicação**: `chaves-api.md` e `guide-start.md` linkam
  `docs/dev/credentials.md` e `docs/dev/dev-environment.md` respectivamente.
- **Sem código novo**: só docs + nav (+ linha em `roteamento.md`).
- **Gaps documentados, não escondidos**: `video_metrics` sem captura; 4 backlogs
  operacionais da REVISAO; `defaults.env` não montado; breaker desconectado.
- **Idioma**: português, padrão dos docs existentes.

## Fora de escopo

- Comando `cf report` ou qualquer mudança de CLI/código.
- Ligar o circuit breaker, corrigir `INCR+EXPIRE`, captura de `video_metrics`
  (backlogs separados).
- Reescrever `docs/dev/*` existentes (apenas links).

## Critérios de aceitação

1. 8 arquivos em `docs/manual/` com o conteúdo descrito acima.
2. `mkdocs build --strict` verde; nav atualizado; `roteamento.md` linka o manual.
3. Todo comando mostrado no manual testado literalmente (mesmo transcrição do
   playtest E2E já validado); exit codes e envelopes conferem com `trd-cli.md`.
4. Nenhum segredo/placeholder indevido — `scripts/check_privacy.py` verde.
5. Capítulos `agentes.md` e `analise.md` usáveis por um operador (humano ou IA)
   sem ler os demais (autossuficientes com links de contexto).
