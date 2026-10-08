# PRD — content-factory

## Visão e protagonista

**Quem usa:** operador de conteúdo (Leo) e agentes de AI de automação.
**Problema:** encontrar e priorizar conteúdo viral relevante para um segmento
exige checar várias fontes manualmente, sem ranking confiável nem histórico.
**Uma frase:** dado um segmento (`candles`), o sistema coleta candidatos de
YouTube/TikTok/Instagram/Web, pontua oportunidades (viral score + trend decay)
e mantém o banco completo — hoje; nas próximas frentes, abstrai padrões
criativos e gera ideias/roteiros originais (**deriva do mecanismo, nunca copia
o vídeo**).

## Escopo (frentes em ordem aprovada)

### 1. Fundação (esta entrega)
Tooling (uv/ruff/pyright strict/pre-commit/bandit/pip-audit), git + CI,
pacote em camadas SOLID, CLI `cf`, schema Alembic completo, docs 8 tipos,
devcontainer/compose padrão da casa, segurança e anti-bloqueio.

### 2. Coletores + scoring (esta entrega)
YouTube (API oficial), TikTok (TikTok-Api), Instagram (Graph API), Web
(DuckDuckGo + ingest de URLs); upsert idempotente; viral score + trend decay;
worker arq com cron 2×/dia; E2E `00_prereq` + `01`.

### 3. Análise IA → padrões (próxima)
Video → pattern (estrutura, trigger psicológico), Creative Pattern Library
com embeddings (pgvector já presente).

### 4. Ideias e roteiros (próxima)
Pattern + segmento → 10 ideias → roteiro estruturado (cenas/on-screen/CTA) →
production queue (hoje/amanhã/+7).

### 5. Feedback loop (próxima)
Published → performance_metrics → aprendizado → ajuste de pesos de score.

## Fora da v1 (não-objetivos)

- LLM/análise de padrões, geração de ideias e roteiros (nesta entrega)
- Transcrição (Whisper), análise visual/audio
- Geração de vídeo e pipeline editorial
- FastAPI/painel web
- Instagram sem API oficial (scraping)
- GPU, job de CI GPU, proxies obrigatórios
- MinIO/S3 (armazenamento de objetos)

## Critérios de sucesso (gate de release)

1. E2E `01` verde: `segment load → collect (fixtures) → score` com linhas em
   `videos/metrics/scores/collect_runs`
2. Gates DoD com saída anexada: pytest, ruff, pyright strict 0, `alembic
   check`, bandit, pip-audit, `mkdocs build --strict`, teste de privacidade
3. **Novo segmento = 1 YAML** sem código; **novo collector = 1 classe**
   sem tocar em domain/app
4. CI verde no PR; docs 8 tipos + HANDOFF + índice navegáveis

## Números e restrições

- Segmento inicial: **velas artesanais** (`segments/candles.yaml`)
- Coleta agendada: 2×/dia por segmento; rate-limits por plataforma (ver
  spec §7); cota YouTube rastreada em `collect_runs`
- Stack: Python 3.14, uv, PostgreSQL 16 + pgvector, Redis 7, arq, Playwright
  (só worker), Docker/devcontainers, GitHub Actions
- Restrição: sem GPU nesta fase; cookies TikTok via `.env` (conta secundária
  recomendada)
