# TRD — Scoring (viral score + trend decay)

> **Status:** requisito técnico da fase B (plano task 16 — use-case
> `score` + `cf score`; o núcleo puro `domain/scoring.py` já existe e está
> testado). Estado real: [HANDOFF](../HANDOFF.md).

## Fronteira técnica

**Dentro:** fórmula pura em `domain/scoring.py` (`viral_score`,
`engagement_rate`, `trend_decay`), o use-case `app/score.py` que busca as
métricas mais recentes do segmento e persiste em `video_scores`, e o
comando `cf score`.

**Não muda:** pesos atuais (mudam só com este TRD revisado +
renormalização), schema (colunas `replication_score`/`production_score`
já nascem `NULL`), coletores (só fornecem `VideoMetrics`).

## Fórmula (MVP)

Componentes computáveis de dados (spec §4.3), pesos somam 1.0:

| Componente | Peso | Definição |
|---|---|---|
| `performance` | 0.25 | `min(views / 1_000_000 × 100, 100)` |
| `velocity` | 0.20 | idade ≤1d → 100 · ≤3d → 80 · ≤7d → 50 · >7d → 20 |
| `engagement` | 0.15 | `min(engagement_rate × 10, 100)`; taxa = `(likes×1 + comments×2 + shares×3 + saves×4) / views × 100` (0 se `views=0`) — 10%+ satura em 100 |
| `relevance` | 0.20 | `keywords encontradas / len(keywords)` em título+descrição (case-insensitive, substring) × 100 |
| `recency` | 0.20 | `trend_decay`: ≤7d → `low` → 100 · ≤30d → `medium` → 50 · >30d → `high` → 15 · sem data → `none` → 50 |

- `total = Σ(componente × peso)`, arredondado em 2 casas, sempre em `[0, 100]`.
- **Regra zero:** `views=0 ∧ likes=0 ∧ relevance=0` → `total = 0.0`
  (nada de pontuar vazio).
- `trend_decay` é gravado como rótulo (`none|low|medium|high`) além do
  componente numérico.

### Reservados p/ fase IA (renormalização)

`replication_score` e `production_score` ficam `NULL` no schema e **não**
entram na fórmula hoje. Quando entrarem, os pesos dos 5 componentes
existentes são **renormalizados** (soma 1.0) — documentado aqui por ser
mudança de contrato de score, não ajuste fino.

## Requisitos e critérios de aceitação

- [ ] R1 `score(deps)` grava **1 linha em `video_scores` por vídeo** do
      segmento (métrica mais recente: `video_metrics ORDER BY captured_at
      DESC LIMIT 1` por vídeo) — *teste `tests/app/test_score.py::test_score_writes_one_result_per_video`*
- [ ] R2 Todo `total ∈ [0, 100]` — *mesmo teste*
- [ ] R3 Métricas zeradas + sem relevance → `total == 0.0` — *teste
      `test_zero_metrics_totals_zero`*
- [ ] R4 Relevância conta keywords do segmento no título/descrição
      (2/2 → `relevance == 100.0`) — *teste `test_relevance_counts_keyword_hits`*
- [ ] R5 Fórmula pura (sem I/O, `now` é parâmetro) coberta por
      `tests/domain/test_scoring.py` — pesos exatos da tabela acima
- [ ] R6 Repositório em falha não derruba o lote → `ScoreOutcome(ok,
      partial, scored)` mapeia para envelope + exit `0|2|1` — *testes do
      use-case* (task 16)
- [ ] R7 `cf score --segment <slug>` inexistente →
      `{"error": {"code": "segment_not_found"}}` exit 1 — *teste de CLI*

## Interfaces e dependências

| Item | Detalhe |
|---|---|
| Ports novos | `ScoreRepository.upsert(video_id, segment_id, result, computed_at)`; `VideoRepository.latest_for_segment(segment_id) -> list[VideoSnapshot]` |
| DTO | `VideoSnapshot(video_id, title, description, published_at, metrics)` |
| Saída pura | `ScoreResult(total, performance, velocity, engagement, relevance, recency, trend_decay)` |
| Tabela | `video_scores` (timeseries: 1 linha por captura; `viral_score` indexado DESC) |
| CLI | `cf score --segment <slug>` → envelope `{"ok": …, "scored": N}` ([TRD CLI](trd-cli.md)) |
| Deps | `domain.scoring` (já em pé), clock injetável (testes fixam `now`) |

## Pré-condições antes de código

- [x] Núcleo puro + pesos definidos (task 3) e schema `video_scores`
      (task 4) — **não redesenhar** (spec §4.3: pesos vêm do TRD).
- Tasks 10–11 (registry/collect) para haver métricas coletadas;
  fixtures de métricas nos testes de integração.

## Riscos e limites técnicos

| Risco | Limitação consciente |
|---|---|
| Instagram não expõe views | `performance` desses vídeos tende a 0 com métricas zeradas — mitigação "scoring adaptativo" fica p/ fase IA (spec §13); documentado aqui para não virar bug-surpresa |
| velocity sensível à idade | buckets fixos (não exponencial) no MVP — calibrar com dados reais antes de mudar |
| Peso errado quebra histórico | pesos versionados neste TRD; renormalização explícita (acima) ao adicionar componentes |
| Sem dados de benchmark | IVFFlat/HNSW e pesos finos só com dados reais (spec §5) |
