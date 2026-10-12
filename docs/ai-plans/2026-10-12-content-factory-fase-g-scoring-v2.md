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
