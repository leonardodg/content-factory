# Bloco 5 — `viral-v2`, ranking de temas/origens, top vídeos e hashtags (R9+R10+R11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Responder à pergunta do teste e entregar o objetivo do projeto: **quais vídeos são o conteúdo viral de qualidade do segmento, quais temas os trazem, e se as origens da web superam as keywords fixas**.
- O `fase-h` continua intocado como linha de base.
- Ao lado dele, o `viral-v2` mede o engajamento de verdade.
- Em cima do `viral-v2` vêm o ranking por termo, a **regra de decisão pré-registrada por origem**, o top de vídeos e as hashtags dos melhores vídeos, que voltam ao pool.

**Fase B:** este bloco e o Bloco 6 são implementados **durante** o teste. Tudo aqui é recalculável a partir do banco com `--as-of`, porque `video_metrics` e `video_discoveries` já estão sendo gravados desde a Fase A. A única coisa que muda o comportamento da coleta são as hashtags (vaga exploratória pré-registrada).

**Architecture:**
- Peças puras em `domain/`: `viral_v2`, `term_ranking`, `hashtag_mining`.
- Duas tabelas: `video_scores_v2` (append-only) e `term_reports`.
- Leitura da **população no corte** (`as_of`) no repositório.
- Dois use-cases: `app/score_v2.py` (worker: monitoramento + hashtags) e `app/term_report.py` (CLI: relatório reprodutível).
- O relatório **recalcula** o `viral-v2` no corte, em vez de juntar scores gravados em horários diferentes; percentis de momentos diferentes não se comparam.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy 2 async, Alembic, typer, pytest (asyncio), ruff, pyright strict.

## Global Constraints

- `domain/` puro; `app/` → portas; `infra/` implementa portas; `cli/worker → app → domain`.
- `pyright` strict 0 erros; `ruff` linha 100/aspas duplas; `ruff format` limpo; TDD; sem `assert` em produção (B101).
- Envelope só com contagens; **dados no banco**; exportação CSV só **fora** do repositório (o repositório é público e os títulos são de terceiros).
- `make gates` antes de "pronto" (inclui `alembic check`).
- Migração aditiva: `0015` segue `0014`.
- **Nunca escrever em `video_scores`** como parte do `viral-v2`.
- Bootstrap com **seed fixa** (`20261016`).

## Referência

Especificação §4 R9, R10, R11; §6 (regra de decisão, "top temas", n mínimo). Pré-registro: `docs/manual/pre-registro-viral-por-tema.md` (Bloco 4, Task 8). **Depende dos Blocos 1–4.**

## Decisões deste bloco (revisão QA)

| Decisão | Por quê |
|---|---|
| Pesos do `viral-v2`: velocidade 0,35, engajamento 0,25, relativo ao canal 0,25, alcance 0,15. Percentil dentro de `(plataforma, segmento)`; n mínimo 5 por grupo; componente ausente renormaliza | Hipótese pré-registrada (não muda durante o teste). |
| **Velocidade** = Δviews/Δhoras entre a última medição até o corte e a anterior **com pelo menos 1 h de intervalo** | Evita o par da mesma transação (ver Bloco 3) e o ruído de remedições próximas. |
| **Relevância vira filtro, e o filtro é sobre título + tags** (âncora), com idioma via `videos.language` (prefixo `pt` ou ausente) | A 1ª versão aplicava `filter_term` em título + **descrição**. Medido: uma descrição PT com "Follow me ... for more" caía por idioma. Descrições do YouTube têm inglês de rodapé o tempo todo. |
| **Recência vira janela**: `published_at` dentro de `as_of − 30 d` | Spec R9. |
| **Unidade = vídeo único por chave** | O mesmo vídeo achado pelo mesmo termo em 16 coletas contava 16 vezes (pseudorreplicação). |
| **Regra por origem = a do pré-registro** (diferença de medianas X − `seed`, IC 95% bootstrap, n ≥ 30; `better`/`worse`/`directional`/`insufficient`) | A 1ª versão comparava cada grupo com o limiar do top 10% (`leader`/`trailing`), que **não é** a regra pré-registrada. |
| **Top vídeos** (escopo `video`, top 20 por plataforma e segmento) no mesmo relatório | É o entregável do objetivo ("conteúdo viral de qualidade"); a especificação citava no D+4 e nenhum plano implementava. |
| Hashtags: `filter_hashtag` (substring, sem idioma), top 10 por plataforma ativas, as demais marcadas `excluded`/`stale`; ordenação por `engagement_score`; busca como `#tag` (Bloco 4) | Medido: com `filter_term`, **todas** as hashtags compostas (`velaaromatica`) caíam por `lang`; o pool de hashtags ficaria vazio. |

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/viral_v2.py` (novo) | Componentes, percentis por grupo, pesos versionados, explicação. |
| `src/content_factory/domain/term_ranking.py` (novo) | Vídeo único por chave, medianas, top 10%, `engagement_score`, regra por origem com bootstrap. |
| `src/content_factory/domain/hashtag_mining.py` (novo) | Extração, ranking ponderado pelo `viral-v2`, R1 em modo hashtag, top K. |
| `src/content_factory/domain/models.py` (editar) | `PopulationRow`, `TermReportRecord`. |
| `src/content_factory/app/ports.py` (editar) | `ViralV2Repository`, `TermReportRepository`, `PopulationReader`. |
| `src/content_factory/infra/db/orm.py` (editar) | `VideoScoreV2Row`, `TermReportRow`. |
| `alembic/versions/0015_scores_v2_term_reports.py` (novo) | As duas tabelas. |
| `src/content_factory/infra/db/repositories.py` (editar) | Implementações + SQL da população no corte e das atribuições. |
| `src/content_factory/app/population.py` (novo) | `PopulationRow` → `ViralInput` (relevância, velocidade, engajamento) — compartilhado por score e relatório. |
| `src/content_factory/app/score_v2.py` (novo) | Use-case do worker: grava `viral-v2` e minera hashtags. |
| `src/content_factory/app/term_report.py` (novo) | Use-case do relatório no corte. |
| `src/content_factory/cli/main.py` (editar) | `cf term-report`. |
| `src/content_factory/infra/queue/worker.py`, `composition.py` (editar) | `score_v2` no `run_score_job`. |
| `tests/...` | TDD por arquivo. |

---

### Task 1: `viral-v2` puro (`domain/viral_v2.py`)

**Files:**
- Create: `src/content_factory/domain/viral_v2.py`
- Test: `tests/domain/test_viral_v2.py`

**Interfaces:**
- Produces: `VIRAL_V2_VERSION`, `WEIGHTS_V2`, `WINDOW_DAYS = 30`, `MIN_POPULATION = 5`, `ViralInput`, `ViralScore`, `percentile_ranks(values: Mapping[int, float]) -> dict[int, float]`, `score_population(rows, min_population=MIN_POPULATION) -> list[ViralScore]`.

> A 1ª versão tinha dois erros de pyright strict: `field(default_factory=dict)` sem tipo, e `percentile_ranks` recebendo `dict[int, float | None]`. Também havia um docstring que contradizia o código (o empate pega a média das posições, não o maior rank). O código abaixo foi executado contra estes testes (pytest, pyright strict e ruff limpos).

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_viral_v2.py
from content_factory.domain.viral_v2 import (
    VIRAL_V2_VERSION,
    WEIGHTS_V2,
    ViralInput,
    percentile_ranks,
    score_population,
)


def _row(
    vid: int,
    *,
    platform: str = "youtube",
    velocity: float | None = 10.0,
    engagement: float | None = 5.0,
    views: int | None = 1000,
    followers: int | None = 10_000,
) -> ViralInput:
    return ViralInput(vid, platform, 1, velocity, engagement, views, followers)


def test_version_e_pesos_somam_1() -> None:
    assert VIRAL_V2_VERSION == "viral-v2"
    assert round(sum(WEIGHTS_V2.values()), 6) == 1.0


def test_percentis_pior_0_melhor_1_e_empate_na_media() -> None:
    assert percentile_ranks({1: 10.0, 2: 20.0, 3: 30.0}) == {1: 0.0, 2: 0.5, 3: 1.0}
    assert percentile_ranks({1: 5.0, 2: 5.0}) == {1: 0.5, 2: 0.5}


def test_populacao_pequena_nao_tem_total() -> None:
    assert all(s.total is None for s in score_population([_row(1), _row(2)]))


def test_min_population_parametrizavel() -> None:
    rows = [_row(i) for i in range(1, 4)]
    assert all(s.total is not None for s in score_population(rows, min_population=3))


def test_grupos_separados_por_plataforma() -> None:
    rows = [_row(i) for i in range(1, 7)] + [_row(i, platform="instagram") for i in range(10, 16)]
    out = score_population(rows)
    assert len(out) == 12 and all(s.population_n == 6 for s in out)


def test_maior_velocidade_maior_pontuacao_com_mesmo_resto() -> None:
    out = {
        s.video_id: s for s in score_population([_row(i, velocity=float(i)) for i in range(1, 8)])
    }
    assert (out[7].total or 0.0) > (out[1].total or 0.0)


def test_componente_ausente_renormaliza_pesos() -> None:
    rows = [_row(i) for i in range(1, 7)] + [_row(99, velocity=None)]
    out = {s.video_id: s for s in score_population(rows)}
    assert out[99].total is not None and out[99].velocity is None
    used = out[99].explanation["components_used"]
    assert isinstance(used, list) and "velocity" not in used
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/viral_v2.py
"""Score de viralização `viral-v2` (R9) — puro, experimental e versionado.

Roda AO LADO do `fase-h` (linha de base intocada). Todos os componentes são
percentis DENTRO de `(platform, segment_id)` na janela do teste: plataformas
com escalas diferentes não se misturam. Relevância vira FILTRO (R1, no app) e
recência vira JANELA (30 d, no app) — nenhuma das duas é peso aqui.

Componente ausente é excluído e os pesos renormalizam (mesma regra do
`fase-h`): ausência não é zero.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

VIRAL_V2_VERSION = "viral-v2"
WEIGHTS_V2: dict[str, float] = {
    "velocity": 0.35,
    "engagement": 0.25,
    "channel_relative": 0.25,
    "reach": 0.15,
}
WINDOW_DAYS = 30
MIN_POPULATION = 5


@dataclass(frozen=True)
class ViralInput:
    video_id: int
    platform: str
    segment_id: int
    velocity_per_hour: float | None
    engagement_rate: float | None
    views: int | None
    channel_followers: int | None


@dataclass(frozen=True)
class ViralScore:
    video_id: int
    total: float | None
    velocity: float | None
    engagement: float | None
    channel_relative: float | None
    reach: float | None
    population_n: int
    explanation: dict[str, object] = field(default_factory=dict[str, object])


def percentile_ranks(values: Mapping[int, float]) -> dict[int, float]:
    """Percentil em [0,1]: pior = 0, melhor = 1 (`posição/(n−1)`); n=1 ⇒ 0,5.

    Empates recebem a MÉDIA das posições que ocupam — não depende da ordem
    de inserção.
    """
    n = len(values)
    if n == 0:
        return {}
    if n == 1:
        return {next(iter(values)): 0.5}
    order = sorted(values.items(), key=lambda kv: kv[1])
    out: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and order[j + 1][1] == order[i][1]:
            j += 1
        rank = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            out[order[k][0]] = rank
        i = j + 1
    return out


def _log1p(value: float | None) -> float | None:
    return None if value is None or value <= 0 else math.log1p(value)


def _raw_components(r: ViralInput) -> dict[str, float | None]:
    followers = r.channel_followers
    relative = (
        r.views / followers
        if r.views is not None and followers is not None and followers > 0
        else None
    )
    return {
        "velocity": _log1p(r.velocity_per_hour),
        "engagement": r.engagement_rate,
        "channel_relative": _log1p(relative),
        "reach": _log1p(float(r.views) if r.views is not None else None),
    }


def score_population(
    rows: Sequence[ViralInput], min_population: int = MIN_POPULATION
) -> list[ViralScore]:
    """Pontua TODOS os vídeos, normalizando por `(platform, segment_id)`."""
    groups: dict[tuple[str, int], list[ViralInput]] = {}
    for r in rows:
        groups.setdefault((r.platform, r.segment_id), []).append(r)

    out: dict[int, ViralScore] = {}
    for group in groups.values():
        raw = {r.video_id: _raw_components(r) for r in group}
        n = len(group)
        ranks: dict[str, dict[int, float]] = {}
        for name in WEIGHTS_V2:
            present = {vid: v for vid, comps in raw.items() if (v := comps[name]) is not None}
            ranks[name] = percentile_ranks(present)
        for r in group:
            comps: dict[str, float | None] = {
                name: ranks[name].get(r.video_id) for name in WEIGHTS_V2
            }
            available = {k: v for k, v in comps.items() if v is not None}
            total: float | None = None
            if available and n >= min_population:
                weight_sum = sum(WEIGHTS_V2[k] for k in available)
                total = round(sum(available[k] * WEIGHTS_V2[k] for k in available) / weight_sum, 6)
            out[r.video_id] = ViralScore(
                video_id=r.video_id,
                total=total,
                velocity=comps["velocity"],
                engagement=comps["engagement"],
                channel_relative=comps["channel_relative"],
                reach=comps["reach"],
                population_n=n,
                explanation={
                    "formula_version": VIRAL_V2_VERSION,
                    "components_used": sorted(available),
                    "population_n": n,
                    "min_population": min_population,
                    "insufficient": n < min_population,
                },
            )
    return list(out.values())
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/viral_v2.py tests/domain/test_viral_v2.py
git commit -m "feat(domain): viral-v2 — percentis por plataforma/segmento e pesos versionados (R9)"
```

---

### Task 2: Ranking de temas e regra por origem (`domain/term_ranking.py`)

**Files:**
- Create: `src/content_factory/domain/term_ranking.py`
- Test: `tests/domain/test_term_ranking.py`

**Interfaces:**
- Produces: `TERM_RANK_VERSION`, `ENGAGEMENT_WEIGHTS`, `MIN_N_TERM = 5`, `MIN_N_ORIGIN = 30`, `BASELINE_ORIGIN = "seed"`, `BOOTSTRAP_SEED`, `BOOTSTRAP_RESAMPLES`, `TermSample`, `TermSummary`, `OriginComparison`, `median`, `top10_threshold`, `summarize`, `rank_terms`, `bootstrap_diff_ci`, `compare_origins`.

> Executado na revisão QA contra estes testes (pytest, pyright strict e ruff limpos).

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_term_ranking.py
from content_factory.domain.term_ranking import (
    BOOTSTRAP_SEED,
    MIN_N_ORIGIN,
    MIN_N_TERM,
    TermSample,
    bootstrap_diff_ci,
    compare_origins,
    rank_terms,
)


def _s(term: str, origin: str, n: int, base: float, start: int = 0) -> list[TermSample]:
    return [TermSample(start + i, term, origin, base + i, float(i), 1.0 + i, 0.5) for i in range(n)]


def test_constantes_congeladas() -> None:
    assert (BOOTSTRAP_SEED, MIN_N_TERM, MIN_N_ORIGIN) == (20261016, 5, 30)


def test_mesmo_video_repetido_conta_uma_vez() -> None:
    repetido = [TermSample(1, "vela", "seed", 50.0, 1.0, 1.0, 0.5)] * 16
    out = rank_terms(repetido + _s("vela", "seed", 3, 60, start=100))
    assert out[0].n == 4 and out[0].verdict == "insufficient"


def test_rank_terms_ordena_e_insuficiente_vai_para_o_fim() -> None:
    out = rank_terms(
        _s("forte", "suggest", 10, 80)
        + _s("fraco", "suggest", 10, 1, 100)
        + _s("poucos", "seed", 3, 50, 200)
    )
    assert out[0].key == "forte" and out[-1].verdict == "insufficient"
    assert out[0].top10_fraction is not None and out[0].engagement_score is not None


def test_origem_melhor_pior_direcional_e_insuficiente() -> None:
    def verdict(x_base: float, seed_base: float, n_x: int = 40) -> str:
        samples = _s("x", "suggest", n_x, x_base) + _s("y", "seed", 40, seed_base, 1000)
        return compare_origins(samples)[0].verdict

    assert verdict(60, 10) == "better"
    assert verdict(0, 60) == "worse"
    assert verdict(10, 10) == "directional"
    assert verdict(90, 10, n_x=10) == "insufficient"  # n < 30


def test_sobreposicao_com_seed_e_reportada() -> None:
    out = compare_origins(_s("x", "suggest", 30, 60) + _s("y", "seed", 30, 60))
    assert out[0].overlap == 30


def test_bootstrap_reprodutivel() -> None:
    a, b = [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]
    assert bootstrap_diff_ci(a, b) == bootstrap_diff_ci(a, b)
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/term_ranking.py
"""Ranking de temas e veredito por origem (R10, §6) — puro, seed FIXA.

Unidade de análise = VÍDEO ÚNICO por chave (termo ou origem): o mesmo vídeo
achado pelo mesmo termo em 16 coletas conta 1 vez (sem pseudorreplicação).
Um vídeo achado por duas origens conta nas duas (sobreposição reportada).

Duas saídas:
- `rank_terms`: por termo, `n` (vídeos únicos), medianas, fração no top 10%
  do `viral-v2`, `engagement_score`; `n < MIN_N_TERM` ⇒ `insufficient`
  (nunca vencedor).
- `compare_origins`: regra PRÉ-REGISTRADA — origem X vs `seed`: diferença
  das medianas do `viral-v2` com IC 95% por bootstrap; `n < MIN_N_ORIGIN`
  em X ou em `seed` ⇒ `insufficient`; limite inferior > 0 ⇒ `better`;
  limite superior < 0 ⇒ `worse`; senão `directional`.
"""

import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

TERM_RANK_VERSION = "term-rank-v1"
ENGAGEMENT_WEIGHTS: dict[str, float] = {
    "top10_fraction": 0.4,
    "median_velocity": 0.3,
    "median_engagement": 0.3,
}
MIN_N_TERM = 5
MIN_N_ORIGIN = 30
BASELINE_ORIGIN = "seed"
BOOTSTRAP_SEED = 20261016
BOOTSTRAP_RESAMPLES = 1000


@dataclass(frozen=True)
class TermSample:
    """Uma atribuição: vídeo × termo × origem, com o `viral-v2` no corte."""

    video_id: int
    term: str
    origin: str
    viral_v2: float | None
    velocity: float | None
    engagement: float | None
    channel_relative: float | None


@dataclass(frozen=True)
class TermSummary:
    key: str
    n: int
    median_viral_v2: float | None
    median_velocity: float | None
    median_engagement: float | None
    median_channel_relative: float | None
    top10_fraction: float | None
    engagement_score: float | None
    verdict: str  # "ranked" | "insufficient"


@dataclass(frozen=True)
class OriginComparison:
    origin: str
    n: int
    baseline_n: int
    median_viral_v2: float | None
    baseline_median: float | None
    diff: float | None
    ci_low: float | None
    ci_high: float | None
    overlap: int  # vídeos que também vieram da `seed`
    verdict: str  # "better" | "worse" | "directional" | "insufficient"


def median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _unique_by_video(samples: Iterable[TermSample]) -> list[TermSample]:
    seen: dict[int, TermSample] = {}
    for s in samples:
        seen.setdefault(s.video_id, s)
    return list(seen.values())


def _group(samples: Sequence[TermSample], by: str) -> dict[str, list[TermSample]]:
    groups: dict[str, list[TermSample]] = {}
    for s in samples:
        groups.setdefault(s.term if by == "term" else s.origin, []).append(s)
    return {k: _unique_by_video(v) for k, v in groups.items()}


def top10_threshold(samples: Sequence[TermSample]) -> float | None:
    """Percentil 90 do `viral-v2` dos vídeos ÚNICOS do recorte."""
    viral = [s.viral_v2 for s in _unique_by_video(samples) if s.viral_v2 is not None]
    if len(viral) < 10:
        return None
    return statistics.quantiles(sorted(viral), n=10)[8]


def _score(components: dict[str, float | None]) -> float | None:
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None
    weight_sum = sum(ENGAGEMENT_WEIGHTS[k] for k in available)
    return round(sum(v * ENGAGEMENT_WEIGHTS[k] for k, v in available.items()) / weight_sum, 6)


def summarize(group: Sequence[TermSample], key: str, threshold: float | None) -> TermSummary:
    viral = [s.viral_v2 for s in group if s.viral_v2 is not None]
    frac = (
        round(sum(1 for v in viral if v >= threshold) / len(viral), 6)
        if viral and threshold is not None
        else None
    )
    med_v = median([s.velocity for s in group if s.velocity is not None])
    med_e = median([s.engagement for s in group if s.engagement is not None])
    insufficient = len(group) < MIN_N_TERM
    return TermSummary(
        key=key,
        n=len(group),
        median_viral_v2=median(viral),
        median_velocity=med_v,
        median_engagement=med_e,
        median_channel_relative=median(
            [s.channel_relative for s in group if s.channel_relative is not None]
        ),
        top10_fraction=frac,
        engagement_score=None
        if insufficient
        else _score({"top10_fraction": frac, "median_velocity": med_v, "median_engagement": med_e}),
        verdict="insufficient" if insufficient else "ranked",
    )


def rank_terms(samples: Sequence[TermSample]) -> list[TermSummary]:
    threshold = top10_threshold(samples)
    out = [summarize(g, k, threshold) for k, g in _group(samples, "term").items()]
    return sorted(
        out, key=lambda s: (s.verdict == "insufficient", -(s.engagement_score or 0.0), s.key)
    )


def bootstrap_diff_ci(
    a: Sequence[float],
    b: Sequence[float],
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[float, float]:
    """IC 95% (percentis 2,5/97,5) de mediana(a) − mediana(b); reamostra cada grupo."""
    rng = random.Random(seed)
    diffs = sorted(
        statistics.median(rng.choices(a, k=len(a))) - statistics.median(rng.choices(b, k=len(b)))
        for _ in range(resamples)
    )
    return round(diffs[int(0.025 * resamples)], 6), round(diffs[int(0.975 * resamples) - 1], 6)


def compare_origins(samples: Sequence[TermSample]) -> list[OriginComparison]:
    groups = _group(samples, "origin")
    base = [s.viral_v2 for s in groups.get(BASELINE_ORIGIN, []) if s.viral_v2 is not None]
    base_ids = {s.video_id for s in groups.get(BASELINE_ORIGIN, [])}
    out: list[OriginComparison] = []
    for origin, group in sorted(groups.items()):
        if origin == BASELINE_ORIGIN:
            continue
        values = [s.viral_v2 for s in group if s.viral_v2 is not None]
        med, base_med = median(values), median(base)
        ci_low = ci_high = diff = None
        if med is None or base_med is None or min(len(values), len(base)) < MIN_N_ORIGIN:
            verdict = "insufficient"
        else:
            diff = round(med - base_med, 6)
            ci_low, ci_high = bootstrap_diff_ci(values, base)
            verdict = "better" if ci_low > 0 else "worse" if ci_high < 0 else "directional"
        out.append(
            OriginComparison(
                origin=origin,
                n=len(values),
                baseline_n=len(base),
                median_viral_v2=med,
                baseline_median=base_med,
                diff=diff,
                ci_low=ci_low,
                ci_high=ci_high,
                overlap=sum(1 for s in group if s.video_id in base_ids),
                verdict=verdict,
            )
        )
    return out
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/term_ranking.py tests/domain/test_term_ranking.py
git commit -m "feat(domain): term-rank-v1 — vídeo único, ranking de temas e regra por origem (R10/§6)"
```

---

### Task 3: Mineração de hashtags pura (`domain/hashtag_mining.py`)

**Files:**
- Create: `src/content_factory/domain/hashtag_mining.py`
- Test: `tests/domain/test_hashtag_mining.py`

**Interfaces:**
- Consumes: `content_factory.domain.term_filter.{TermFilterConfig, filter_hashtag}` (Bloco 1).
- Produces: `HASHTAG_MINE_VERSION`, `TOP_FRACTION = 0.25`, `TOP_K = 10`, `HashtagSource`, `MinedHashtag`, `extract_hashtags`, `mine(sources, config) -> list[MinedHashtag]`.

> Executado na revisão QA contra estes testes.

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_hashtag_mining.py
from content_factory.domain.hashtag_mining import HashtagSource, extract_hashtags, mine
from content_factory.domain.term_filter import TermFilterConfig

VELAS = TermFilterConfig(anchors=("vela", "velas"), blocked=("bajista",))
RAW = {"snippet": {"tags": ["velas artesanais", "decoracao"], "description": "x"}}


def test_extrai_tags_sem_espaco_e_hashtags_do_titulo() -> None:
    tags = extract_hashtags(HashtagSource(1, "youtube", 9.0, RAW, title="Vela #VelaDeSoja"))
    assert {"velasartesanais", "veladesoja", "decoracao"} <= set(tags)


def test_so_top_25_por_cento_e_so_tags_do_nicho() -> None:
    fraca = {"snippet": {"tags": ["velafraca"]}}
    sources = [
        HashtagSource(i, "youtube", float(i), RAW if i >= 8 else fraca) for i in range(1, 11)
    ]
    tags = {h.tag for h in mine(sources, VELAS)}
    assert "velasartesanais" in tags
    assert "velafraca" not in tags  # fora do top 25%
    assert "decoracao" not in tags  # sem âncora


def test_sem_viral_v2_nao_entra_e_top_k_por_plataforma() -> None:
    assert mine([HashtagSource(1, "youtube", None, RAW)], VELAS) == []
    muitas = {"snippet": {"tags": [f"vela{i}" for i in range(30)]}}
    assert len(mine([HashtagSource(1, "youtube", 10.0, muitas)], VELAS)) == 10
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/hashtag_mining.py
"""Mineração de hashtags (R11) — pura.

Fonte: só os vídeos do segmento no TOP 25% do `viral-v2`. Peso = soma do
`viral-v2` dos vídeos que usam a tag (frequência PONDERADA). Passa pelo R1
em modo hashtag (`filter_hashtag`: âncora/bloqueio por substring, sem idioma).
Só as `TOP_K` melhores por plataforma ficam ativas no pool.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from content_factory.domain.term_filter import TermFilterConfig, filter_hashtag

HASHTAG_MINE_VERSION = "hashtag-mine-v1"
TOP_FRACTION = 0.25
TOP_K = 10
_HASHTAG_RE = re.compile(r"#(\w{2,50})", re.UNICODE)


@dataclass(frozen=True)
class HashtagSource:
    video_id: int
    platform: str
    viral_v2: float | None
    raw: Mapping[str, object]
    title: str = ""
    description: str = ""


@dataclass(frozen=True)
class MinedHashtag:
    tag: str
    platform: str
    weight: float
    videos: int


def _snippet_tags(raw: Mapping[str, object]) -> list[str]:
    snippet = raw.get("snippet")
    if not isinstance(snippet, Mapping):
        return []
    tags = cast(Mapping[str, object], snippet).get("tags")
    if not isinstance(tags, list):
        return []
    return [str(t) for t in cast(list[object], tags)]


def extract_hashtags(source: HashtagSource) -> list[str]:
    """`snippet.tags` (sem espaço) + `#tags` do título/descrição, minúsculas e sem `#`."""
    tags = [t.casefold().replace(" ", "") for t in _snippet_tags(source.raw)]
    for text in (source.title, source.description):
        tags += [m.casefold() for m in _HASHTAG_RE.findall(text)]
    return list(dict.fromkeys(t for t in tags if t))


def mine(sources: Sequence[HashtagSource], config: TermFilterConfig) -> list[MinedHashtag]:
    scored = [s for s in sources if s.viral_v2 is not None]
    if not scored:
        return []
    values = sorted(s.viral_v2 for s in scored if s.viral_v2 is not None)
    cutoff = values[int((1 - TOP_FRACTION) * len(values))]
    weight: dict[tuple[str, str], float] = {}
    videos: dict[tuple[str, str], int] = {}
    for source in scored:
        if source.viral_v2 is None or source.viral_v2 < cutoff:
            continue
        for tag in extract_hashtags(source):
            if not filter_hashtag(tag, config).accepted:
                continue
            key = (source.platform, tag)
            weight[key] = weight.get(key, 0.0) + source.viral_v2
            videos[key] = videos.get(key, 0) + 1
    ranked = sorted(
        (
            MinedHashtag(tag=t, platform=p, weight=round(w, 6), videos=videos[(p, t)])
            for (p, t), w in weight.items()
        ),
        key=lambda h: (h.platform, -h.weight, h.tag),
    )
    out: list[MinedHashtag] = []
    per_platform: dict[str, int] = {}
    for h in ranked:
        if per_platform.get(h.platform, 0) < TOP_K:
            out.append(h)
            per_platform[h.platform] = per_platform.get(h.platform, 0) + 1
    return out
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/hashtag_mining.py tests/domain/test_hashtag_mining.py
git commit -m "feat(domain): hashtag-mine-v1 — top 25%, filtro de hashtag e top 10 (R11)"
```

---

### Task 4: Tabelas `video_scores_v2` e `term_reports` (migração `0015`)

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`
- Create: `alembic/versions/0015_scores_v2_term_reports.py`
- Test: `tests/infra/test_orm.py` (editar)

**Interfaces:**
- Produces:
  - `VideoScoreV2Row` (append-only): `id`, `video_id`, `segment_id`, `computed_at`, `as_of`, `viral_score_v2` (nullable), `velocity`, `engagement`, `channel_relative`, `reach`, `population_n`, `explanation` JSONB, `formula_version`, `window_days`. Índices `(segment_id, computed_at)` e `(video_id, computed_at)`.
  - `TermReportRow`: `id`, `report_id` (UUID do relatório: todas as linhas de uma execução), `segment_id`, `platform`, `scope` (`term` | `origin` | `video`), `report_key`, `n`, `baseline_n`, `median_viral_v2`, `median_velocity`, `median_engagement`, `median_channel_relative`, `top10_fraction`, `engagement_score`, `diff`, `ci_low`, `ci_high`, `overlap`, `verdict`, `rank`, `version`, `rules_version`, `as_of`, `computed_at`. Índices `(segment_id, as_of)` e `report_id`.
  - Use `report_key`, não `key`, como nome de coluna (mesmo nome no ORM e na migração).

- [ ] **Step 1–5:** teste das colunas → FAIL → ORM + migração `0015` (down `0014`) → PASS + `uv run alembic check` sem drift → commit `feat(db): migração 0015 — video_scores_v2 e term_reports (R9/R10)`.

---

### Task 5: População no corte e atribuições (repositório)

**Files:**
- Modify: `src/content_factory/domain/models.py`, `src/content_factory/app/ports.py`, `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Produces:
  - `class PopulationRow(BaseModel)`: `video_id`, `platform`, `platform_video_id`, `title`, `language: str | None`, `tags: list[str]`, `published_at: datetime | None`, `views`, `likes`, `comments`, `shares`, `saves` (todos `int | None`, da última medição ≤ `as_of`), `captured_at: datetime`, `prev_views: int | None`, `prev_captured_at: datetime | None` (medição anterior com intervalo ≥ 1 h), `followers: int | None` (`followers_at_capture` da última medição, senão `creators.followers_count`), `raw: dict[str, object]`, `description: str`.
  - `PopulationReader.population(segment_id: int, as_of: datetime, window_days: int) -> list[PopulationRow]`
  - `PopulationReader.attributions(segment_id: int, platform: str, as_of: datetime) -> list[tuple[int, str, str]]`: pares **distintos** `(video_id, term, origin)` com `discovered_at ≤ as_of` e `origin <> 'unattributed'`.
  - `ViralV2Repository.append_many(segment_id: int, as_of: datetime, scores: Sequence[ViralScore]) -> int`
  - `TermReportRepository.append_many(rows: Sequence[TermReportRecord]) -> int`

- [ ] **Step 1: Testes que falham** (integração). Semear 1 segmento com:
  - vídeo A: medições em t−10 h, t−5 h e t (mesma transação que a coleta: 2 linhas em t) → `prev_*` = a de t−5 h, nunca a de t;
  - vídeo B: publicado há 40 dias → fora da janela de 30 d;
  - vídeo C: só medido **depois** do `as_of` → fora;
  - atribuições: A por "vela" (`seed`) em 3 coletas → **1** par `(A, "vela", "seed")`; A por "vela" com origem `unattributed` → fora.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — `population` em SQL explícito (CTE):

```sql
WITH m AS (
  SELECT vm.*, row_number() OVER (PARTITION BY vm.video_id ORDER BY vm.captured_at DESC, vm.id DESC) AS rn
  FROM video_metrics vm
  JOIN video_segments vs ON vs.video_id = vm.video_id AND vs.segment_id = :segment_id
  WHERE vm.captured_at <= :as_of
), last AS (SELECT * FROM m WHERE rn = 1),
prev AS (
  SELECT DISTINCT ON (m.video_id) m.video_id, m.views, m.captured_at
  FROM m JOIN last l ON l.video_id = m.video_id
  WHERE m.captured_at <= l.captured_at - interval '1 hour'
  ORDER BY m.video_id, m.captured_at DESC
)
SELECT v.id, v.platform, v.platform_video_id, v.title, v.description, v.language, v.raw,
       v.published_at, l.views, l.likes, l.comments, l.shares, l.saves, l.captured_at,
       p.views AS prev_views, p.captured_at AS prev_captured_at,
       coalesce(l.followers_at_capture, c.followers_count) AS followers
FROM last l
JOIN videos v ON v.id = l.video_id
LEFT JOIN prev p ON p.video_id = l.video_id
LEFT JOIN creators c ON c.id = v.creator_id
WHERE v.published_at >= :as_of - make_interval(days => :window_days)
  AND (v.removed_at IS NULL OR v.removed_at > :as_of)
```

Use `text()` com bind params (nunca f-string). `tags` = `raw.snippet.tags` lido em Python, com `isinstance`.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit** — `feat(db): população do viral-v2 no corte e atribuições distintas (R9/R10)`.

---

### Task 6: População → `ViralInput` (relevância, velocidade, engajamento)

**Files:**
- Create: `src/content_factory/app/population.py`
- Test: `tests/app/test_population.py`

**Interfaces:**
- Consumes: `PopulationRow`, `Segment`, `contains_phrase`/`word_tokens`, `filter_hashtag`, `scoring.engagement_rate` (mesma fórmula do `fase-h`), `ViralInput`.
- Produces:
  - `def is_relevant(row: PopulationRow, segment: Segment) -> bool`: idioma (`language` com prefixo `pt` ou `None`), sem bloqueio no título, e âncora no **título** (palavra inteira) **ou** em alguma tag (`filter_hashtag`). A descrição **não** entra.
  - `def to_viral_input(row: PopulationRow, segment_id: int) -> ViralInput`: velocidade = `(views − prev_views) / horas` quando houver par (≥ 1 h, `None` caso contrário; negativo vira 0); engajamento = `engagement_rate(VideoMetrics(...))`.
  - `def build_population(rows, segment) -> tuple[list[ViralInput], int]`: `(entradas relevantes, descartadas por relevância)`.

- [ ] **Step 1: Testes que falham** — casos literais:
  - título PT do nicho com descrição contendo "Follow me on Instagram for more" → **relevante** (era o falso negativo medido);
  - `language="en"` → fora;
  - título sem âncora, mas tag `velasartesanais` → relevante;
  - título com termo bloqueado → fora;
  - `prev_*` ausente → `velocity_per_hour is None`.

- [ ] **Step 2–5:** FAIL → implementar → PASS → commit `feat(app): população do viral-v2 — relevância por título/tags e velocidade (R9)`.

---

### Task 7: `score_v2` no worker + mineração de hashtags (R9/R11)

**Files:**
- Create: `src/content_factory/app/score_v2.py`
- Modify: `src/content_factory/infra/queue/worker.py`, `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_score_v2.py` (novo), `tests/infra/queue/test_worker.py` (editar)

**Interfaces:**
- Produces:
  - `@dataclass class ScoreV2Deps: population: PopulationReader; scores_v2: ViralV2Repository; terms: SegmentTermRepository; segment: Segment; clock: Callable[[], datetime]; window_days: int = 30; min_population: int = 5`
  - `@dataclass class ScoreV2Outcome: scored: int; skipped_relevance: int; insufficient: int; hashtags_active: int; hashtags_stale: int`
  - `async def score_v2(deps: ScoreV2Deps) -> ScoreV2Outcome`

- [ ] **Step 1: Testes que falham**
  - população com 6 relevantes + 1 em inglês → `scored == 6`, `skipped_relevance == 1`, nada gravado em `video_scores`;
  - top 25% com tags do nicho → `deps.terms` recebe `SegmentTerm(origin="hashtag:youtube", engagement_score=peso, rules_version=HASHTAG_MINE_VERSION)` e `set_status("hashtag:youtube", keep=top10, reason="stale")`;
  - população abaixo do mínimo → nenhuma hashtag (sem `viral-v2`, sem mineração).

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
async def score_v2(deps: ScoreV2Deps) -> ScoreV2Outcome:
    seg, as_of = deps.segment, deps.clock()
    rows = await deps.population.population(seg.id, as_of, deps.window_days)
    inputs, skipped = build_population(rows, seg)
    scores = score_population(inputs, deps.min_population)
    await deps.scores_v2.append_many(
        seg.id, as_of, scores
    )  # só relevantes; skipped vai no envelope
    by_id = {r.video_id: r for r in rows}
    sources = [
        HashtagSource(
            video_id=s.video_id,
            platform=by_id[s.video_id].platform,
            viral_v2=s.total,
            raw=by_id[s.video_id].raw,
            title=by_id[s.video_id].title,
            description=by_id[s.video_id].description,
        )
        for s in scores
    ]
    cfg = TermFilterConfig(anchors=tuple(seg.anchor_terms), blocked=tuple(seg.blocked_terms))
    mined = mine(sources, cfg)
    active = stale = 0
    for platform in sorted({h.platform for h in mined}):
        tags = [h for h in mined if h.platform == platform]
        origin = f"hashtag:{platform}"
        active += await deps.terms.upsert_many(
            seg.id,
            [
                SegmentTerm(
                    segment_id=seg.id,
                    term=h.tag,
                    origin=origin,
                    engagement_score=h.weight,
                    rules_version=HASHTAG_MINE_VERSION,
                )
                for h in tags
            ],
        )
        stale += await deps.terms.set_status(seg.id, origin, [h.tag for h in tags], "stale")
    return ScoreV2Outcome(
        scored=len(scores),
        skipped_relevance=skipped,
        insufficient=sum(1 for s in scores if s.total is None),
        hashtags_active=active,
        hashtags_stale=stale,
    )
```

No worker, em `run_score_job._one`, **depois** do commit do `fase-h` e fora do `if web_score_enabled`, em transação própria. É o mesmo padrão do score web: a falha não reverte o `fase-h`.

```python
            try:
                v2 = await _score_v2_impl(bundle.score_v2)
                await bundle.uow.commit()
            except Exception:
                logger.exception("score viral-v2 falhou — %s (fase-h mantido)", segment.slug)
                await bundle.uow.rollback()
                result["viral_v2"] = "failed"
            else:
                result["viral_v2"] = dataclasses.asdict(v2)
```

Envs: `VIRAL_V2_WINDOW_DAYS=30`, `VIRAL_V2_MIN_POPULATION=5`, lidas na composição. Não usar `os.getenv` dentro do use-case.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_score_v2.py tests/infra/queue/ -q` → PASS.

- [ ] **Step 5: Commit** — `feat(score): viral-v2 ao lado do fase-h + hashtags no pool (R9/R11)`.

---

### Task 8: `cf term-report` — temas, veredito por origem e top vídeos (R10)

**Files:**
- Create: `src/content_factory/app/term_report.py`
- Modify: `src/content_factory/cli/main.py`
- Test: `tests/app/test_term_report.py` (novo), `tests/cli/test_term_report.py` (novo)

**Interfaces:**
- Produces:
  - `async def build_term_report(segment_slug: str, platform: str, as_of: datetime, deps: TermReportDeps) -> TermReportOutcome`
  - `TermReportOutcome.as_counts()` → `{"terms": n, "terms_ranked": n, "origins": n, "origins_decided": n, "videos": n, "rows_saved": n, "report_id": str}`
  - CLI: `cf term-report --segment <slug> --as-of <ISO com fuso> [--platform youtube] [--top-videos 20] [--out <csv>]`. `--as-of` é **obrigatório**, com fuso, como o `cf web-eval`. `--out` dentro do repositório é recusado (`out_inside_repo`).

- [ ] **Step 1: Testes que falham**
  - população sintética com 2 origens (40 vídeos `suggest` melhores, 40 `seed`) → linha `scope="origin"`, `report_key="suggest"`, `verdict="better"`, com `diff`, `ci_low > 0`, `baseline_n=40`;
  - o mesmo vídeo atribuído 16 vezes ao mesmo termo conta `n=1`;
  - `scope="video"`: 20 linhas, `rank` 1..20, ordenadas por `viral_score_v2` desc, `report_key = platform_video_id`;
  - mesma entrada e mesmo `as_of` produzem as mesmas linhas (reprodutível; só `report_id`/`computed_at` mudam);
  - CLI: `--as-of` sem fuso → envelope `{"ok": false, "error": "as_of_without_tz"}`; stdout só com o envelope.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — `build_term_report`:
  1. `population(as_of)`;
  2. `build_population`;
  3. `score_population`, recalculado no corte;
  4. `attributions(platform, as_of)`;
  5. `TermSample` por par `(video, term, origin)`, com o `viral-v2`/componentes recalculados;
  6. `rank_terms`, `compare_origins` e o top N de vídeos;
  7. `TermReportRecord`s com o mesmo `report_id`, `version=TERM_RANK_VERSION`, `rules_version=f"{TERM_FILTER_VERSION}+{VIRAL_V2_VERSION}"`, e `as_of`;
  8. `append_many`.

  O `engagement_score` dos termos `ranked` também vai para `segment_terms.engagement_score` (R4/R10) das linhas `suggest`/`seed`, com `UPDATE` só desse campo. O CSV (`--out`) traz as três seções, com títulos; ele é para uso local, fora do repositório.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_term_report.py tests/cli/ -q` → PASS.

- [ ] **Step 5: Commit** — `feat(report): cf term-report — temas, veredito por origem e top vídeos no corte (R10)`.

---

### Task 9: Encerrar o bloco — gates e aceite (Fase B)

- [ ] **Step 1: Gates** — `make gates` verde.

- [ ] **Step 2: Deploy da Fase B** (orquestrador; janela sem cron, fila vazia): migração `0015`, imagem, fumaça do `score`. Anotar no pré-registro a **data de início da vaga de hashtag**: é um commit de adendo, sem mudar mais nada.

- [ ] **Step 3: Aceite R9**

```sql
SELECT max(formula_version) FROM video_scores;            -- 'fase-h' (intocado)
SELECT count(*), count(viral_score_v2) FROM video_scores_v2 WHERE computed_at > now() - interval '6 hours';
SELECT count(*) FROM video_scores_v2 WHERE jsonb_typeof(explanation) <> 'object';  -- 0
```

- [ ] **Step 4: Aceite R10 (D+1 e D+4, dados reais)** — `cf term-report --segment <slug> --as-of <corte>` para os 3 segmentos: envelope com contagens; no banco, linhas `term`/`origin`/`video` do mesmo `report_id`; termo com `n < 5` como `insufficient`; origem com `n < 30` como `insufficient`. **Rodar 2 vezes com o mesmo `--as-of` e comparar** (devem ser idênticas, exceto `report_id`/`computed_at`).

- [ ] **Step 5: Aceite R11**

```sql
SELECT origin, status, count(*) FROM segment_terms WHERE origin LIKE 'hashtag:%' GROUP BY 1, 2;
SELECT term, engagement_score FROM segment_terms
WHERE origin = 'hashtag:youtube' AND status = 'active' ORDER BY engagement_score DESC;
-- esperado: até 10 ativas, todas do nicho; na coleta seguinte, uma busca `hashtag:youtube` com `#tag`
SELECT term, results FROM search_queries WHERE origin = 'hashtag:youtube' ORDER BY id DESC LIMIT 3;
```

- [ ] **Step 6: Commit de encerramento** — `git commit -am "docs(plans): bloco 5 concluído — R9/R10/R11 verificados" || true`

---

## Auto-revisão da spec (R9/R10/R11/§6)

- **R9** ✓ componentes por plataforma e segmento; pesos pré-registrados; velocidade com intervalo mínimo; relevância como filtro (título + tags + idioma do vídeo) e recência como janela; `viral-v2` em tabela própria, nunca em `video_scores`; explicação por componente (Tasks 1, 4–7).
- **R10** ✓ `n` de vídeos **únicos**, medianas, top 10%, `engagement_score` no pool, IC por bootstrap com seed fixa, `n < 5` como insuficiente (Task 2); **regra de decisão por origem exatamente como no pré-registro**, com `n ≥ 30` (Task 2); relatório no corte, reprodutível e gravado no banco, com envelope só de contagens e CSV fora do repositório (Task 8).
- **Objetivo do projeto** ✓ top vídeos por plataforma e segmento no corte (Task 8, escopo `video`).
- **R11** ✓ top 25% do `viral-v2`; `snippet.tags` + `#` do título e da descrição; R1 em modo hashtag; peso por frequência ponderada; top 10 ativas, demais `stale`; busca como `#tag` (Tasks 3, 7; vaga no Bloco 4).
