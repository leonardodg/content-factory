# Design — content-factory (motor de inteligência de conteúdo parametrizado por segmento)

- **Data:** 2026-10-07
- **Status:** Aprovado pelo usuário (design por seções, brainstorming)
- **Próximo passo:** plano task-by-task em `docs/ai-plans/2026-10-07-fundacao-coletores-banco.md`
- **Substitui:** README/vision do prototype `InstagramReelsAnalyzer` (código legado vira referência de domínio, não base de implementação)

## 1. Visão

Sistema de **Creative Intelligence + Content Factory** parametrizado por segmento
(`segment = candles` hoje, `macrame` amanhã). Dado um segmento, o sistema coleta
conteúdo candidato nas plataformas, pontua oportunidades (viral score + trend decay)
e mantém o banco completo pronto para as próximas fases (abstração de padrões,
geração de ideias, roteiros, produção, feedback loop).

O agente **não copia vídeos** — identifica *por que* um vídeo funciona
(pattern) e deriva ideias originais do mecanismo (fases futuras).

## 2. Escopo desta entrega (fundação + coletores + banco)

**Entra:**

1. Tooling de qualidade: `uv`, ruff, `pyright strict` (0 erros), pre-commit pinnado, bandit, pip-audit
2. Git init → repo GitHub `content-factory` (privado) → CI GitHub Actions (quality + docs)
3. Pacote `content_factory` em camadas SOLID + CLI `cf` (envelope JSON de status)
4. Coletores: **YouTube** (Data API v3), **TikTok** (TikTok-Api v6 + Playwright), **Instagram** (Graph API oficial), **Web** (DuckDuckGo + enriquecimento de URLs)
5. Banco completo: PostgreSQL 16 + pgvector, schema Alembic com todas as entidades
6. Domain scoring: viral score + trend decay
7. Workers: arq + cron (coleta 2×/dia), lock Redis
8. Docs: 8 tipos completos + índice de roteamento + HANDOFF

**Não-objetivos (fases seguintes):**

- LLM: análise de padrões, geração de ideias/roteiros (colunas reservadas já nascem no schema)
- Transcrição (Whisper), análise visual/audio
- Geração de vídeo, pipeline editorial
- FastAPI (interface é CLI; API só quando houver painel/consulta externa)
- Instagram sem API oficial (scraping) — só Graph API
- GPU, E2E 00..09 completo de GPU (corte explícito: MVP sem GPU)

## 3. Decisões estruturais (sementes dos ADRs)

| # | Decisão | Escolha | Alternativas descartadas | Porquê |
|---|---|---|---|---|
| 1 | Banco | PostgreSQL 16 + **pgvector** | MongoDB 8; híbrido PG+Mongo | SQL + JSONB (metadata por plataforma) + vetores nativos; `alembic check` é gate da casa; agregações de score/decay via SQL |
| 2 | Fila | **Redis + arq** | RabbitMQ; sem fila | Leve, multiuso (fila + cache + rate-limit + lock); arq é async nativo; fan-out de RabbitMQ não existe no MVP |
| 3 | Descoberta TikTok | **TikTok-Api** (davidteather, v6) + cookies `ms_token` no `.env` | Creative Center scraping; Apify/RapidAPI; só URLs via agente | API privada com trending/search; atrás de `Protocol` p/ trocar se quebrar; sem custo no MVP |
| 4 | Instagram | **Graph API oficial** (hashtag top/recent) | Scraping | Seu brief: sem scraping agressivo; limitação de views de terceiros aceita e documentada |
| 5 | Interface | **CLI-first** (`cf`), dados no banco, JSON = envelope de status | API-first; CLI dumpando resultados | Consumidor primário = agentes de AI; banco é a fonte de verdade |
| 6 | Arquitetura | **Monolito modular** (camadas + Protocols) | Microserviços; scripts soltos | MVP com gates de qualidade; extensão por estratégia sem reescrita |
| 7 | Infra | **Compose único** `.devcontainer/docker-compose.yml` (1 container por responsabilidade) | infra.yml/app.yml separados | Padrão da casa (skill bootstrapping §8) |

## 4. Arquitetura em camadas

```text
cli/ + workers/     entrypoints (Typer, arq tasks) — só I/O de borda
        ↓ chama
app/                casos de uso: collect, score, status, segment
        ↓ depende de ports.py (Protocolos: Collector, VideoRepository, UnitOfWork, RateLimiter, Clock)
domain/             entidades, scoring, erros de negócio — PURO (sem I/O, sem datetime.now())
        ↑ implementado por
infra/              collectors/{youtube,tiktok,instagram,web}, db/ (SQLAlchemy 2 + Alembic), queue/ (arq), redis/
```

- **Regra:** dependência em unidirecional `cli/workers → app → domain`; `infra` implementa ports e é injetado nos entrypoints (DI manual por construtor/registry — sem framework).
- **Domínio puro:** sem SQL/HTTP/arquivos/print; `now` é parâmetro onde o tempo importa; erros de negócio tipados.
- **Troca de estratégia:** TikTok-Api quebra → nova implementação de `Collector`, zero mudança em `app/`/`domain/`.

### 4.1 Layout do pacote

```text
src/content_factory/
├── domain/
│   ├── models.py        # Segment, Creator, Video, VideoMetrics, ScoreResult...
│   ├── scoring.py       # viral_score(), trend_decay() — puros
│   └── errors.py        # SegmentNotFound, CollectorError, AuthExpired...
├── app/
│   ├── ports.py         # Protocols (Collector, repositories, RateLimiter, Clock)
│   ├── collect.py       # orquestra coleção por segmento/plataforma
│   ├── score.py         # recalcula scores a partir de metrics
│   └── status.py        # consolidação operacional
├── infra/
│   ├── collectors/{youtube,tiktok,instagram,web}.py
│   ├── db/              # engine, modelos ORM, repositórios, alembic env
│   ├── queue/           # arq tasks + cron
│   └── ratelimit.py     # token bucket em Redis
├── cli/                 # Typer: comandos cf
└── config.py            # pydantic-settings (env 2 camadas)
segments/candles.yaml    # primeiro segmento
tests/                   # espelho 1:1 de src/
```

### 4.2 CLI (contrato com agentes de AI)

| Comando | Função |
|---|---|
| `cf segment load <arquivo.yaml>` | valida (pydantic) e grava/atualiza segmento |
| `cf collect --segment <slug> [--platform youtube,tiktok,instagram,web] [--limit N]` | coleta → upsert idempotente |
| `cf score --segment <slug>` | recalcula viral score + trend decay |
| `cf ingest --url <url> [--segment <slug>]` | agente fornece URL → enriquece (metadados) e grava candidato |
| `cf status --segment <slug>` | resumo operacional |

- **Envelope JSON único:** `{"ok": true, "command": ..., "counts": {...}, "duration_s": ...}` ou `{"ok": false, "error": {"code": "...", "message": "..."}}`
- **Exit codes:** `0` sucesso · `2` falha parcial (alguma plataforma falhou) · `1` falha total
- Resultados/volumes ficam **no banco**; agentes leem o banco (não há dump de dados via CLI)

### 4.3 Fórmula de score (MVP)

Componentes **computáveis de dados**: `performance`, `velocity`, `engagement`,
`relevance` (match de keywords do segmento), `recency/trend_decay`.
Ajustes de peso por componente: **TRD em `docs/specs/`** (fase de definição
antes da task de scoring). `replication`/`production` entram como colunas
NULL e passam a valer na fase IA com **renormalização** (documentada no TRD).

## 5. Modelo de dados (PostgreSQL 16 + pgvector)

```text
segments ──< video_segments >── videos >── creators
                                ├── video_metrics      (timeseries: 1 linha/captura)
                                ├── video_scores       (timeseries: score por captura)
                                ├── transcripts        (texto + segments JSONB)
                                └── creative_patterns (origin_video_id, embedding)
                                       └── ideas  (embedding)
                                              └── scripts (scenes JSONB)
                                                     └── production_jobs
                                                            └── published_videos
                                                                   └── performance_metrics
collect_runs  (auditoria de cada execução de collector)
```

| Tabela | Campos essenciais |
|---|---|
| `segments` | slug UNIQUE, name, **config JSONB** (audience, languages, platforms, content_types, keywords, subnichos) |
| `creators` | platform, platform_creator_id, username, followers_count, raw JSONB — `UNIQUE(platform, platform_creator_id)` |
| `videos` | platform, platform_video_id, creator_id FK, url, title, description, published_at, duration_s, language, thumbnail_url, **raw JSONB**, transcript_id FK nullable — `UNIQUE(platform, platform_video_id)` |
| `video_segments` | PK(video_id, segment_id), discovery_source, relevance_score, discovered_at |
| `video_metrics` | video_id FK, captured_at, views, likes, comments, shares, saves, followers_at_capture — **nullable** (Instagram não expõe views de terceiros) |
| `video_scores` | video_id + segment_id, computed_at, viral_score, performance, velocity, engagement, relevance, recency, trend_decay, **replication_score NULL, production_score NULL** (reservadas p/ fase IA) |
| `transcripts` | video_id, source, language, text, segments JSONB |
| `creative_patterns` | name, description, hook, psychology[], visual_structure[], best_for[], origin_video_id, embedding vector(1536) |
| `ideas` | segment_id, pattern_id, title, summary, status, embedding vector(1536) |
| `scripts` | idea_id, title, duration_s, hook, scenes JSONB, cta, production_difficulty, status |
| `production_jobs` | script_id, status, scheduled_for, assets JSONB |
| `published_videos` | script_id, platform, url, published_at |
| `performance_metrics` | published_video_id, captured_at, views, likes, comments, shares, saves, retention_pct, completion_rate, followers_gained |
| `collect_runs` | collector, segment_id, status, started_at, finished_at, found/inserted/updated/failed, quota_used, retries, error_code |

**Decisões:**
- Métricas e scores são **append-only timeseries** → velocity = delta entre capturas; histórico preserva o feedback loop futuro
- `config`/`raw` em JSONB + índice GIN; extensão `vector` criada já, embeddings preenchidos na fase IA
- Índices: `(platform, published_at)`, `(video_id, captured_at)`, `viral_score DESC`; IVFFlat/HNSW só com dados reais
- Schema nasce de Alembic; `alembic check` no DoD; data-model e diagram gerados do schema real

## 6. Coletores

Todos implementam `Collector` (Protocol) e devolvem `CandidateVideo` (DTO validado por pydantic) → upsert idempotente por `UNIQUE(platform, platform_video_id)`.

| | Descoberta | Métricas | Cuidados |
|---|---|---|---|
| **YouTube** | `search.list` (keywords do segmento, `publishedAfter`, order=viewCount) → `videos.list` + `channels.list` | views/likes/comments, seguidores | cota diária rastreada (custo por chamada) |
| **TikTok** | trending + hashtag + busca (TikTok-Api v6) | stats do objeto video | `ms_token` com TTL, circuit breaker, proxy opcional via env |
| **Instagram** | Graph API `ig_hashtag_search` → `top_media`/`recent_media` | likes/comments/views **null** | rate via `X-App-Usage`, só oficial |
| **Web** | DuckDuckGo (sem chave) por keywords; enriquece URLs fornecidas por agentes | — | guarda SSRF no fetch |

Hashtags/queries do segmento derivam de `config.keywords` + `subnichos`.

## 7. Segurança e anti-bloqueio (aprovado)

**Segredos:** `.env` gitignorado; `.env.example` versionado vazio; `.devcontainer/secrets/` gitignored por inteiro com `*.example` versionados; teste de privacidade no CI; redação de logs (`sessionid`, `ms_token`, `access_token`, cookies); segredos nunca em `ARG`/`ENV` de Dockerfile.

**Rede/containers:** Postgres e Redis em rede interna sem porta pública (dev: `127.0.0.1` explícito se precisar); containers non-root; Redis sem senha aceito **só** por rede interna — registrado no ADR da fila.

**Aplicação:** SSRF (allowlist http/https, bloqueio IP privado/link-local/metadata `169.254.169.254`, timeout + tamanho máx.); validação pydantic de YAML/args/payloads; SQL só parametrizado via ORM.

**Anti-bloqueio:** token bucket por plataforma no Redis (YouTube cota diária; TikTok N req/min + pausa 2–8 s; Instagram janela `X-App-Usage`); backoff exponencial ±jitter (max 5); **circuit breaker** após 3 falhas → `collect_runs.status='blocked'`; lock de execução única por collector; fingerprints Playwright reais (UA coerente com browser); `ms_token` expirado → erro `auth_expired` claro (sem retry cego); proxy opcional via `TIKTOK_PROXY`.

**Supply chain:** bandit + pip-audit no CI/DoD, `uv lock` pinnado, hooks pre-commit pinnados.

**Credenciais:** conta secundária recomendada p/ TikTok (documentado); tokens YouTube/IG com escopo mínimo e rotação em `docs/dev/`.

## 8. Resiliência e tratamento de erros

- Erros classificados e gravados em `collect_runs`: `auth_expired`, `rate_limited`, `blocked`, `upstream_changed`, `network`, `invalid_payload`
- Retries/backoff/circuit breaker **só na fronteira** (infra/collector); domínio nunca retrya
- Falha parcial: plataformas independentes seguem; exit code `2`
- Upsert idempotente → reexecução segura
- Agendamento: cron do arq (2×/dia por segmento) + execução manual via CLI; lock Redis impede dupla execução

## 9. Infra Docker/devcontainer (padrão da casa)

```yaml
# .devcontainer/docker-compose.yml — 1 arquivo, 1 rede content-factory-net
services:
  python:     # devcontainer padrão (Dockerfile alvo "dev")
  worker:     # arq + cron; alvo "worker" (herda dev + playwright chromium)
  postgres:   # pgvector/pgvector:pg16, volume, sem porta pública
  redis:      # redis:7-alpine, sem porta pública
```

- `devcontainer.json`: `dockerComposeFile`, `service: python`, `workspaceFolder: /app`, `remoteUser: appuser`, mounts gitconfig/ssh; **sem features**
- Dockerfile único multi-stage: `base → dev → worker`
- Env em 2 camadas: `env_file:` (`.devcontainer/env/app.env` gitignored, dentro do container) × `${VAR}` interpolação (`.env` raiz gitignored + `.env.example`)
- `.devcontainer/readme.md` com tabela: componente | versão | POR QUÊ

## 10. Qualidade

**Testes:** `tests/` espelha `src/` 1:1; TDD por task; mock só na fronteira (fixtures HTTP gravadas); núcleo roda de verdade; integração com Postgres+Redis reais (service containers no CI / compose de teste); E2E `00_prereq` (gates) + `01` smoke (segmento → collect fixture → score → linhas no banco); `PLANO_TESTE_E2E.md`.

**DoD (evidência anexada):** `pytest -q` · `ruff check` + `ruff format --check` · `pyright` strict 0 · `alembic check` · `bandit` · `pip-audit` · `mkdocs build --strict` · teste de privacidade · CI verde no PR.

**CI:** `.github/workflows/ci.yml` (quality + services pgvector/redis) e `docs.yml` (mkdocs strict). **Corte explícito:** job GPU não se aplica (MVP sem GPU).

## 11. Documentação (8 tipos + suporte)

| Tipo | Dir | Conteúdo desta entrega |
|---|---|---|
| ADR | `docs/adr/` | 0001-postgres-pgvector · 0002-redis-arq · 0003-coletores-oficiais-tiktok-api · 0004-cli-first (templates copiados da skill) |
| architecture | `docs/architecture/` | estado atual + Mermaid + fases |
| PLAN | `docs/ai-plans/` | plano task-by-task **antes do código** |
| data-model | `docs/data-model/` | gerado dos modelos + migrations |
| diagram | `docs/diagram/` | ER/arquitetura `.html+.svg+.png` do schema real (gate `alembic check`) |
| dev | `docs/dev/` | ambiente, novo collector, novo segmento, rotação de credenciais |
| specs | `docs/specs/` | TRD por feature (collectors, scoring, CLI) |
| PRD | `docs/produto/` | visão, frentes, não-objetivos, critérios de sucesso |
| Suporte | `docs/` | índice "Se você quer… → Vá para" + **HANDOFF.md** |

MkDocs `--strict`; docs geradas só por script.

## 12. Critérios de aceitação

1. E2E `01` verde: `cf segment load candles.yaml` → `cf collect` (fixtures gravadas — CI roda sem chaves) → `cf score` → linhas em `videos`/`metrics`/`scores`/`collect_runs`; coleta **com APIs reais** = playtest manual documentado em `PLANO_TESTE_E2E.md`
2. Todos os gates DoD com saída anexada (contagens reais)
3. **Novo segmento = 1 arquivo YAML** (nenhum código)
4. **Novo collector = 1 classe + registro** (nenhum toque em domain/app)
5. Nenhum segredo/path pessoal em arquivo rastreado (teste de privacidade)
6. Docs 8 tipos + HANDOFF + índice no ar; `mkdocs build --strict` verde

## 13. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| TikTok anti-bot/`EmptyResponseException` | circuit breaker, backoff, proxy opcional, Protocol p/ trocar estratégia (Apify depois) |
| Quota YouTube | rastreio de cota em `collect_runs`, orçamento por run |
| Instagram sem views de terceiros | métricas nullable + scoring adaptativo por disponibilidade |
| Imagem worker pesada (Playwright) | alvo multi-stage; só worker leva chromium |
| `ms_token` expira | erro `auth_expired` + instrução de rotação em `docs/dev/` |

## 14. Próximas fases (fora do escopo)

Analisador AI (video → pattern) → Idea Generator → Script Writer → Production Director → transcrição → feedback loop (published → performance → learning) → FastAPI/painel → geração de vídeo (gerador trocável).
