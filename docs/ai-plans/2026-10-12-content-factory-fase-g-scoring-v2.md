# Fase G — Scoring v2: None vs Zero + cobertura de métricas (estrutural)

**Goal:** vídeos sem uma métrica (ex.: Instagram sem `views`, YouTube sem
`shares`) não são mais penalizados como se tivessem desempenho zero.
`VideoMetrics` passa a distinguir métrica ausente (`None`) de métrica
zero (`0`), e o `ScoreResult` registra quais métricas estavam disponíveis.

**Escopo deliberadamente parcial:** o roadmap prevê também velocidade por
diferença entre snapshots e recalibração de pesos — ambos dependem de
dados reais acumulados em `video_metrics` (Fase F recém-mergeada). Este
plano cobre só a parte **estrutural** que não precisa de calibração.

**Pré-requisito:** Fase F mergeada (F1 já grava `video_metrics`).

### Task G1: `VideoMetrics` com `int | None`

**Files:**
- Modify: `src/content_factory/domain/models.py` (`VideoMetrics`)
- Modify: `src/content_factory/infra/db/repositories.py` (`_snapshot`)
- Modify: `src/content_factory/domain/scoring.py` (`engagement_rate`, `viral_score`)
- Test: `tests/domain/test_scoring.py`, `tests/infra/test_repositories.py`

`VideoMetrics` campos mudam de `int = 0` para `int | None = None`.
`_snapshot()` para de colapsar `m.views or 0` — passa `None` direto.
`engagement_rate` trata `views is None` como "sem dados" (retorna `None`,
não `0.0`). `viral_score` trata componente com `None` como "sem dados"
(exclui da média em vez de contar como zero).

- [x] RED → GREEN
- [x] Commit: `feat(scoring): VideoMetrics distingue metrica ausente (None) de zero`

### Task G2: cobertura de métricas no ScoreResult

**Files:**
- Modify: `src/content_factory/domain/models.py` (`ScoreResult`)
- Modify: `src/content_factory/domain/scoring.py` (`viral_score`)
- Modify: `src/content_factory/infra/db/orm.py` (`VideoScoreRow` se necessário)
- Test: `tests/domain/test_scoring.py`

`ScoreResult` ganha `metrics_available: list[str]` (ex. `["views","likes"]`)
— quais métricas o snapshot tinha de verdade. Permite auditar cobertura
por plataforma sem query extra.

- [x] RED → GREEN
- [x] Commit: `feat(scoring): ScoreResult registra cobertura de metricas disponiveis`

### Task G3: gates + docs + HANDOFF

- [x] Gates completos.
- [x] `docs/HANDOFF.md` + roadmap (G parcialmente concluído — estrutural).
- [x] Commit final.

### Task G4: bugfix pós-code-review (2 achados confirmados)

Code review da Fase G (sessão separada, com reprodução real contra
Postgres) achou 2 problemas no que G1/G2 entregaram:

**Achado 1 (crítico, CONFIRMADO):** `video_scores.performance`/`engagement`
continuavam `NOT NULL` no banco — G1 mudou `ScoreResult` para aceitar
`None` nesses campos, mas nenhuma migração tornou as colunas nullable.
Resultado: `cf score` falhava com `NotNullViolation` para TODO vídeo
ainda sem captura em `video_metrics` (o caso normal entre descoberta e
primeira coleta de métrica) — `score()` engolia a exceção e devolvia
`scored=0`. A própria correção da Fase G fazia esses vídeos não serem
pontuados em absoluto. Reproduzido e confirmado contra Postgres real
antes da correção.

- Migração `0005_video_scores_nullable_performance_engagement.py`.
- `VideoScoreRow.performance`/`.engagement` → `float | None`.
- Teste de integração real (`test_score_upsert_accepts_none_performance_and_engagement`)
  cobrindo exatamente o caminho que faltava (RED confirmado antes do fix).

**Achado 2 (plausível, confirmado como gap real, corrigido com escopo
conservador):** `engagement_rate` tratava "nenhuma submétrica de
engajamento reportada" (likes/comments/shares/saves todos `None`) como
`0.0`, não `None` — mesma categoria de bug que G1 corrigiu para `views`,
mas não propagada para as submétricas. Avaliei e **rejeitei** a correção
óbvia (renormalizar pelos pesos 1/2/3/4 quando só parte das submétricas
está disponível): testado numericamente, isso infla o resultado ~3x
(5.9% → 18% no mesmo vídeo, só removendo `shares`/`saves`) porque os
pesos são força de sinal por ação, não fração de um total a extrapolar
— seria calibrar sem dados reais, o que o roadmap já proíbe
explicitamente. Fix aplicado: só o caso simétrico e não-especulativo
(TODAS as submétricas ausentes → `None`); cobertura parcial continua
somando só o disponível, sem extrapolar, com o motivo documentado no
código-fonte.

- [x] RED → GREEN nos dois achados.
- [x] Gates completos: pytest 194 passed (real Postgres), ruff 0,
  pyright 0, alembic check ok, bandit 0.
- [x] Commit.
