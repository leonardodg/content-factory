# Bloco 5 — `viral-v2`, ranking de temas e mineração de hashtags (R9+R10+R11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Responder à pergunta do teste. O `fase-h` fica intocado como linha de base; ao lado, `viral-v2` mede engajamento de verdade (velocidade, taxa, desempenho relativo ao canal, alcance — normalizados por plataforma e segmento). Em cima dele, o **ranking de temas por termo e por origem**; e as hashtags dos vídeos do topo voltam ao pool.

**Architecture:** Três peças puras novas em `domain/` (`viral_v2`, `term_ranking`, `hashtag_mining`) + três tabelas (`video_scores_v2`, `term_reports`, e o pool ganha `hashtag:<plataforma>`) + dois use-cases (`app/score_v2.py`, `app/term_report.py`) e a mineração no fim do score. `video_scores.total`/`viral_score` do `fase-h` **nunca** são tocados.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy 2 async, Alembic, typer, pytest (asyncio), ruff, pyright strict.

## Global Constraints

- `domain/` puro; `app/` → portas; `infra/` implementa portas; `cli/worker → app → domain`.
- `pyright` strict 0 erros; `ruff` linha 100/aspas duplas; `ruff format` limpo; TDD.
- Envelope só com contagens; **dados no banco** (regra "dados no banco"); exportação CSV só **fora** do repositório.
- `make gates` antes de "pronto" (inclui `alembic check`).
- Migração aditiva: `0015` segue `0014` (Bloco 4).
- **Nunca escrever em `video_scores.total`/`viral_score`** como parte do `viral-v2`.
- Bootstrap com **seed fixa** (reprodutibilidade do pré-registro).

## Referência

Especificação §4 R9, R10, R11; §6 (regra de decisão, "top temas", `n < 5`); §9 (n mínimo por plataforma/segmento). **Depende dos Blocos 1–4** (pool, atribuição `video_discoveries`, remedição, `trend_score`).

## Decisões deste bloco

- Pesos do `viral-v2` fixados como hipótese pré-registrada: velocidade **0,35**, engajamento **0,25**, relativo ao canal **0,25**, alcance **0,15**; janela **30 dias**.
- Normalização por **percentil dentro de `(platform, segment_id)`** na janela; `n` mínimo por grupo (`VIRAL_V2_MIN_POPULATION=5`). Grupo abaixo do mínimo: componentes calculados, `viral_score_v2 = NULL` e `verdict="insufficient"` (não inventa ranking com poucos vídeos).
- **Relevância vira filtro** (R1 no título/descrição) e **recência vira janela** (30 d) — nenhum dos dois é peso no `viral-v2`.
- **Componente ausente é excluído e os pesos renormalizam** (mesma regra do `fase-h`), nunca vira zero.
- `video_scores_v2` é **append-only** e separado de `video_scores`.
- `term_reports`: uma linha por (escopo, plataforma, chave) com versão, data de corte e veredito; IC 95% por bootstrap `seed=20261016`, 1.000 reamostragens.
- Hashtags: fonte = vídeos do segmento no **top 25% do `viral-v2`**; ranking por **frequência ponderada**; passam pelo R1; entram como `hashtag:<plataforma>`; no YouTube buscadas como `#tag`.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/viral_v2.py` (novo) | Componentes, percentis por grupo, pesos versionados, explicação. |
| `src/content_factory/domain/term_ranking.py` (novo) | Mediana, fração no top 10%, `engagement_score`, bootstrap CI, veredito. |
| `src/content_factory/domain/hashtag_mining.py` (novo) | Extração de hashtags, ranking ponderado pelo `viral-v2`. |
| `src/content_factory/domain/models.py` (editar) | `ViralV2Row`, `ViralV2Result`, `TermReportRow`. |
| `src/content_factory/app/ports.py` (editar) | `ViralV2Repository`, `TermReportRepository`. |
| `src/content_factory/infra/db/orm.py` (editar) | `VideoScoreV2Row`, `TermReportRow`. |
| `alembic/versions/0015_scores_v2_term_reports.py` (novo) | As duas tabelas. |
| `src/content_factory/infra/db/repositories.py` (editar) | Implementações + leitura das populações (janela, followers, atribuição, hashtags). |
| `src/content_factory/app/score_v2.py` (novo) | Use-case do `viral-v2` e da mineração de hashtags. |
| `src/content_factory/app/term_report.py` (novo) | Use-case do ranking de temas. |
| `src/content_factory/cli/main.py` (editar) | `cf term-report`. |
| `src/content_factory/infra/queue/worker.py` (editar) | `score_v2` no `run_score_job`; env `VIRAL_V2_*`. |
| `src/content_factory/app/ops_report.py` (editar) | (R13 no Bloco 6; aqui só o necessário para o aceite.) |
| `tests/...` | TDD por arquivo. |

---

### Task 1: `viral-v2` puro (`domain/viral_v2.py`)

**Files:**
- Create: `src/content_factory/domain/viral_v2.py`
- Test: `tests/domain/test_viral_v2.py`

**Interfaces:**
- Produces:
  - `VIRAL_V2_VERSION = "viral-v2"`; `WEIGHTS_V2 = {"velocity":0.35,"engagement":0.25,"channel_relative":0.25,"reach":0.15}`; `WINDOW_DAYS = 30`; `MIN_POPULATION = 5`
  - `@dataclass(frozen=True) class ViralInput: video_id: int; platform: str; segment_id: int; velocity_per_hour: float | None; engagement_rate: float | None; views: int | None; channel_followers: int | None`
  - `@dataclass(frozen=True) class ViralScore: video_id: int; total: float | None; velocity: float | None; engagement: float | None; channel_relative: float | None; reach: float | None; population_n: int; explanation: dict[str, object]`
  - `def percentile_ranks(values: Mapping[int, float]) -> dict[int, float]`
  - `def score_population(rows: Sequence[ViralInput]) -> list[ViralScore]`

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


def _row(vid: int, **kw: object) -> ViralInput:
    base: dict[str, object] = dict(
        video_id=vid,
        platform="youtube",
        segment_id=1,
        velocity_per_hour=10.0,
        engagement_rate=5.0,
        views=1000,
        channel_followers=10_000,
    )
    base.update(kw)
    return ViralInput(**base)  # type: ignore[arg-type]


def test_version_e_pesos_somam_1() -> None:
    assert VIRAL_V2_VERSION == "viral-v2"
    assert round(sum(WEIGHTS_V2.values()), 6) == 1.0
    assert WEIGHTS_V2["velocity"] == 0.35


def test_percentil_ranks_ordena_e_escala_0_1() -> None:
    ranks = percentile_ranks({1: 10.0, 2: 20.0, 3: 30.0})
    assert ranks[1] < ranks[2] < ranks[3]
    assert 0.0 <= ranks[1] and ranks[3] <= 1.0


def test_populacao_pequena_nao_tem_total() -> None:
    out = score_population([_row(1), _row(2)])
    assert all(s.total is None for s in out)
    assert all(s.population_n == 2 for s in out)


def test_grupo_separado_por_plataforma() -> None:
    rows = [_row(i) for i in range(1, 7)] + [_row(i, platform="instagram") for i in range(10, 16)]
    out = score_population(rows)
    assert all(s.total is not None for s in out)
    yt = [s for s in out if any(r.platform == "youtube" and r.video_id == s.video_id for r in rows)]
    assert len(yt) == 6


def test_maior_velocidade_maior_pontuacao_com_mesmo_resto() -> None:
    rows = [_row(i, velocity_per_hour=float(i)) for i in range(1, 8)]
    out = {s.video_id: s for s in score_population(rows)}
    assert out[7].total is not None and out[1].total is not None
    assert out[7].total > out[1].total


def test_componente_ausente_renormaliza_pesos() -> None:
    rows = [_row(i) for i in range(1, 7)]
    rows.append(_row(99, velocity_per_hour=None))
    out = {s.video_id: s for s in score_population(rows)}
    assert out[99].total is not None  # sem velocity, os outros 3 bastam
    assert out[99].velocity is None


def test_explicacao_diz_componentes_usados() -> None:
    out = score_population([_row(i) for i in range(1, 7)])
    assert "components_used" in out[0].explanation
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/domain/test_viral_v2.py -q` → FAIL.

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
    explanation: dict[str, object] = field(default_factory=dict)


def percentile_ranks(values: Mapping[int, float]) -> dict[int, float]:
    """Percentil em [0,1] (`rank/(n−1)`; n=1 ⇒ 0.5). Empates: maior rank.

    `(rank − 1) / (n − 1)` colocaria o melhor em 1,0 e o pior em 0,0; usa o
    meio do intervalo para empates, sem depender da ordem de inserção.
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


def score_population(rows: Sequence[ViralInput]) -> list[ViralScore]:
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
            present = {vid: comps[name] for vid, comps in raw.items() if comps[name] is not None}
            ranks[name] = percentile_ranks(present)
        for r in group:
            comps: dict[str, float | None] = {
                name: ranks[name].get(r.video_id) for name in WEIGHTS_V2
            }
            available = {k: v for k, v in comps.items() if v is not None}
            total: float | None = None
            if available and n >= MIN_POPULATION:
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
                    "min_population": MIN_POPULATION,
                    "insufficient": n < MIN_POPULATION,
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

### Task 2: Ranking de temas puro (`domain/term_ranking.py`)

**Files:**
- Create: `src/content_factory/domain/term_ranking.py`
- Test: `tests/domain/test_term_ranking.py`

**Interfaces:**
- Produces:
  - `TERM_RANK_VERSION = "term-rank-v1"`; `ENGAGEMENT_WEIGHTS = {"top10_fraction":0.4,"median_velocity":0.3,"median_engagement":0.3}`; `MIN_N = 5`; `BOOTSTRAP_SEED = 20261016`; `BOOTSTRAP_RESAMPLES = 1000`
  - `@dataclass(frozen=True) class TermSample: video_id: int; term: str; origin: str; viral_v2: float | None; velocity: float | None; engagement: float | None; channel_relative: float | None`
  - `@dataclass(frozen=True) class TermSummary: key: str; scope: str; n: int; median_velocity: float | None; median_engagement: float | None; median_channel_relative: float | None; top10_fraction: float | None; engagement_score: float | None; ci_low: float | None; ci_high: float | None; verdict: str`
  - `def median(values: Sequence[float]) -> float | None`
  - `def bootstrap_ci(values: Sequence[float], seed: int = BOOTSTRAP_SEED, resamples: int = BOOTSTRAP_RESAMPLES) -> tuple[float | None, float | None]`
  - `def summarize(samples: Sequence[TermSample], *, scope: str, key: str, top10_threshold: float) -> TermSummary`
  - `def rank(samples: Sequence[TermSample], scope: str) -> list[TermSummary]`

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_term_ranking.py
from content_factory.domain.term_ranking import (
    BOOTSTRAP_SEED,
    MIN_N,
    TermSample,
    bootstrap_ci,
    median,
    rank,
    summarize,
)


def _samples(term: str, origin: str, n: int, base: float = 50.0) -> list[TermSample]:
    return [
        TermSample(
            video_id=i,
            term=term,
            origin=origin,
            viral_v2=base + i,
            velocity=float(i),
            engagement=1.0 + i,
            channel_relative=0.5,
        )
        for i in range(n)
    ]


def test_mediana() -> None:
    assert median([1.0, 3.0, 2.0]) == 2.0
    assert median([]) is None


def test_bootstrap_reprodutivel_e_com_limites() -> None:
    a = bootstrap_ci([1.0, 2.0, 3.0, 4.0, 5.0])
    b = bootstrap_ci([1.0, 2.0, 3.0, 4.0, 5.0])
    assert a == b and a[0] is not None and a[1] is not None
    assert a[0] <= 3.0 <= a[1]


def test_n_menor_que_5_e_insuficiente() -> None:
    s = summarize(_samples("vela", "seed", 4), scope="term", key="vela", top10_threshold=90.0)
    assert s.verdict == "insufficient"
    assert s.engagement_score is None


def test_termo_suficiente_tem_score_e_ci() -> None:
    s = summarize(_samples("vela", "seed", 10), scope="term", key="vela", top10_threshold=50.0)
    assert s.verdict != "insufficient"
    assert s.n == 10 and s.engagement_score is not None and s.ci_low is not None


def test_fraction_top10_conta_certo() -> None:
    samples = _samples("vela", "seed", 4, base=100.0)  # todos acima do limiar
    s = summarize(samples, scope="term", key="vela", top10_threshold=50.0)
    assert s.top10_fraction == 1.0


def test_rank_ordena_por_engagement_score_e_joga_insuficiente_para_o_fim() -> None:
    samples = _samples("forte", "seed", 10, base=100.0) + _samples("fraco", "seed", 10, base=1.0)
    samples += _samples("poucos", "suggest", 3)
    out = rank(samples, scope="term")
    assert out[-1].verdict == "insufficient"
    assert out[0].key == "forte"


def test_semente_de_bootstrap_esta_congelada() -> None:
    assert BOOTSTRAP_SEED == 20261016 and MIN_N == 5
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/term_ranking.py
"""Ranking de temas (R10) — puro, com bootstrap de seed FIXA e n mínimo.

Verdictos: `insufficient` (n < MIN_N — nunca vencedor), `leader`
(IC 95% da mediana todo acima do limiar do top 10%), `directional`
(IC cruza o limiar), `trailing` (todo abaixo).
"""

import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

TERM_RANK_VERSION = "term-rank-v1"
ENGAGEMENT_WEIGHTS: dict[str, float] = {
    "top10_fraction": 0.4,
    "median_velocity": 0.3,
    "median_engagement": 0.3,
}
MIN_N = 5
BOOTSTRAP_SEED = 20261016
BOOTSTRAP_RESAMPLES = 1000


@dataclass(frozen=True)
class TermSample:
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
    scope: str  # "term" | "origin"
    n: int
    median_velocity: float | None
    median_engagement: float | None
    median_channel_relative: float | None
    top10_fraction: float | None
    engagement_score: float | None
    ci_low: float | None
    ci_high: float | None
    verdict: str


def median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def bootstrap_ci(
    values: Sequence[float], seed: int = BOOTSTRAP_SEED, resamples: int = BOOTSTRAP_RESAMPLES
) -> tuple[float | None, float | None]:
    """IC 95% (percentis 2,5 e 97,5) da mediana — seed fixa: reprodutível."""
    if not values:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    medians = sorted(
        statistics.median([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    lo = medians[int(0.025 * resamples)]
    hi = medians[min(int(0.975 * resamples), resamples - 1)]
    return round(lo, 6), round(hi, 6)


def _score(components: dict[str, float | None]) -> float | None:
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None
    weight_sum = sum(ENGAGEMENT_WEIGHTS[k] for k in available)
    return round(sum(available[k] * ENGAGEMENT_WEIGHTS[k] for k in available) / weight_sum, 6)


def summarize(
    samples: Sequence[TermSample], *, scope: str, key: str, top10_threshold: float
) -> TermSummary:
    n = len(samples)
    viral = [s.viral_v2 for s in samples if s.viral_v2 is not None]
    top = [v for v in viral if v >= top10_threshold]
    frac = round(len(top) / len(viral), 6) if viral else None
    med_v = median([s.velocity for s in samples if s.velocity is not None])
    med_e = median([s.engagement for s in samples if s.engagement is not None])
    med_rel = median([s.channel_relative for s in samples if s.channel_relative is not None])
    if n < MIN_N:
        return TermSummary(
            key=key,
            scope=scope,
            n=n,
            median_velocity=med_v,
            median_engagement=med_e,
            median_channel_relative=med_rel,
            top10_fraction=frac,
            engagement_score=None,
            ci_low=None,
            ci_high=None,
            verdict="insufficient",
        )
    ci_low, ci_high = bootstrap_ci(viral)
    if ci_low is not None and ci_low >= top10_threshold:
        verdict = "leader"
    elif ci_high is not None and ci_high < top10_threshold:
        verdict = "trailing"
    else:
        verdict = "directional"
    return TermSummary(
        key=key,
        scope=scope,
        n=n,
        median_velocity=med_v,
        median_engagement=med_e,
        median_channel_relative=med_rel,
        top10_fraction=frac,
        engagement_score=_score(
            {"top10_fraction": frac, "median_velocity": med_v, "median_engagement": med_e}
        ),
        ci_low=ci_low,
        ci_high=ci_high,
        verdict=verdict,
    )


def rank(samples: Sequence[TermSample], scope: str) -> list[TermSummary]:
    """Agrupa por termo (escopo `term`) ou origem (escopo `origin`)."""
    viral = [s.viral_v2 for s in samples if s.viral_v2 is not None]
    threshold = (
        statistics.quantiles(sorted(viral), n=10)[8]
        if len(viral) >= 10
        else max(viral, default=0.0)
    )
    groups: dict[str, list[TermSample]] = {}
    for s in samples:
        key = s.term if scope == "term" else s.origin
        groups.setdefault(key, []).append(s)
    summaries = [
        summarize(group, scope=scope, key=key, top10_threshold=threshold)
        for key, group in groups.items()
    ]
    return sorted(
        summaries,
        key=lambda s: (s.verdict == "insufficient", -(s.engagement_score or 0.0), s.key),
    )
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/term_ranking.py tests/domain/test_term_ranking.py
git commit -m "feat(domain): term-rank-v1 — medianas, top 10%, IC por bootstrap e veredito (R10)"
```

---

### Task 3: Mineração de hashtags pura (`domain/hashtag_mining.py`)

**Files:**
- Create: `src/content_factory/domain/hashtag_mining.py`
- Test: `tests/domain/test_hashtag_mining.py`

**Interfaces:**
- Produces:
  - `HASHTAG_MINE_VERSION = "hashtag-mine-v1"`; `TOP_FRACTION = 0.25`
  - `@dataclass(frozen=True) class HashtagSource: video_id: int; platform: str; viral_v2: float | None; raw: Mapping[str, object]; title: str = ""; description: str = ""`
  - `@dataclass(frozen=True) class MinedHashtag: tag: str; platform: str; weight: float; videos: int`
  - `def extract_hashtags(source: HashtagSource) -> list[str]`
  - `def mine(sources: Sequence[HashtagSource]) -> list[MinedHashtag]`

- [ ] **Step 1: Testes que falham**

```python
# tests/domain/test_hashtag_mining.py
from content_factory.domain.hashtag_mining import (
    HashtagSource,
    extract_hashtags,
    mine,
)

YT_RAW = {
    "snippet": {
        "tags": ["velasartesanais", "decoracao"],
        "description": "Aprenda #velaaromatica e #Dica",
    },
}


def test_extrai_snippet_tags_e_hashtags_do_texto() -> None:
    src = HashtagSource(
        video_id=1, platform="youtube", viral_v2=90.0, raw=YT_RAW, title="Vela #velaDeSoja"
    )
    tags = extract_hashtags(src)
    assert "velasartesanais" in tags and "velaaromatica" in tags
    assert "veladesoja" in tags and "dica" in tags


def test_mineracao_usa_so_o_top_25_por_cento() -> None:
    sources = [
        HashtagSource(video_id=1, platform="youtube", viral_v2=100.0, raw=YT_RAW),
        HashtagSource(
            video_id=2, platform="youtube", viral_v2=10.0, raw={"snippet": {"tags": ["fraco"]}}
        ),
        HashtagSource(video_id=3, platform="youtube", viral_v2=90.0, raw=YT_RAW),
        HashtagSource(
            video_id=4, platform="youtube", viral_v2=5.0, raw={"snippet": {"tags": ["fraco"]}}
        ),
    ]
    out = mine(sources)
    assert all(h.tag != "fraco" for h in out)


def test_peso_por_frequencia_vezes_viral_v2() -> None:
    sources = [
        HashtagSource(video_id=1, platform="youtube", viral_v2=100.0, raw=YT_RAW),
        HashtagSource(video_id=2, platform="youtube", viral_v2=50.0, raw=YT_RAW),
    ]
    out = {h.tag: h for h in mine(sources)}
    assert out["velasartesanais"].videos == 2
    assert out["velasartesanais"].weight > out.get("dica", out["velasartesanais"]).weight - 1e-9


def test_video_sem_viral_v2_nao_entra() -> None:
    out = mine([HashtagSource(video_id=1, platform="youtube", viral_v2=None, raw=YT_RAW)])
    assert out == []
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
# src/content_factory/domain/hashtag_mining.py
"""Mineração de hashtags (R11) — pura.

Fonte: só os vídeos do segmento no TOP 25% do `viral-v2` (não todos). O peso
de cada hashtag é a soma do `viral-v2` dos vídeos que a usam — frequência
PONDERADA pelo desempenho, não contagem crua.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

HASHTAG_MINE_VERSION = "hashtag-mine-v1"
TOP_FRACTION = 0.25
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
    tags = snippet.get("tags")
    if not isinstance(tags, list):
        return []
    return [str(t) for t in tags]


def _text_hashtags(text: str) -> list[str]:
    return [m.casefold() for m in _HASHTAG_RE.findall(text)]


def extract_hashtags(source: HashtagSource) -> list[str]:
    """YouTube: `snippet.tags` + `#tags` do título e da descrição."""
    tags = [t.casefold() for t in _snippet_tags(source.raw)]
    tags += _text_hashtags(source.title)
    tags += _text_hashtags(source.description)
    return list(dict.fromkeys(t for t in tags if t))


def mine(sources: Sequence[HashtagSource]) -> list[MinedHashtag]:
    scored = [s for s in sources if s.viral_v2 is not None]
    if not scored:
        return []
    cutoff = sorted((s.viral_v2 or 0.0) for s in scored)[int((1 - TOP_FRACTION) * len(scored))]
    top = [s for s in scored if (s.viral_v2 or 0.0) >= cutoff]
    weight: dict[tuple[str, str], float] = {}
    videos: dict[tuple[str, str], int] = {}
    for source in top:
        for tag in extract_hashtags(source):
            key = (source.platform, tag)
            weight[key] = weight.get(key, 0.0) + (source.viral_v2 or 0.0)
            videos[key] = videos.get(key, 0) + 1
    out = [
        MinedHashtag(tag=tag, platform=platform, weight=round(w, 6), videos=videos[(platform, tag)])
        for (platform, tag), w in weight.items()
    ]
    return sorted(out, key=lambda h: (-h.weight, h.tag))
```

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/hashtag_mining.py tests/domain/test_hashtag_mining.py
git commit -m "feat(domain): hashtag-mine-v1 — extração e ranking ponderado pelo viral-v2 (R11)"
```

---

### Task 4: Tabelas `video_scores_v2` e `term_reports` (migração `0015`)

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`, `src/content_factory/domain/models.py`
- Create: `alembic/versions/0015_scores_v2_term_reports.py`
- Test: `tests/infra/test_orm.py` (editar)

**Interfaces:**
- Produces:
  - `VideoScoreV2Row`: `video_id`, `segment_id`, `computed_at`, `viral_score_v2` (nullable), `velocity`, `engagement`, `channel_relative`, `reach`, `population_n`, `components_available` JSONB, `explanation` JSONB, `formula_version`, `window_days`
  - `TermReportRow`: `segment_id`, `computed_at`, `scope`, `platform`, `key`, `origin`, `n`, `median_velocity`, `median_engagement`, `median_channel_relative`, `top10_fraction`, `engagement_score`, `ci_low`, `ci_high`, `verdict`, `rules_version`, `version`, `as_of`
  - Modelos pydantic: `ViralV2Row`, `TermReportRow` (em `domain/models.py`).

- [ ] **Step 1: Teste que falha**

```python
def test_tabelas_do_bloco_5() -> None:
    from content_factory.infra.db.orm import TermReportRow, VideoScoreV2Row

    assert "viral_score_v2" in {c.name for c in VideoScoreV2Row.__table__.columns}
    assert {"scope", "key", "verdict", "ci_low", "ci_high"} <= {
        c.name for c in TermReportRow.__table__.columns
    }
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar ORM + migração `0015`** (revision `0015`, down `0014`)

`video_scores_v2` append-only com índices `(segment_id, computed_at)` e `(video_id, computed_at)`; `term_reports` com `(segment_id, computed_at)` e `(segment_id, scope)`. `key` é nome de coluna reservado em alguns dialetos → use `report_key` no ORM e `sa.Column("report_key", ...)` na migração (mantenha consistência ORM↔migração; o `alembic check` compara nomes).

- [ ] **Step 4: Rodar e ver passar + `uv run alembic check`** → PASS, sem drift.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/orm.py src/content_factory/domain/models.py \
        alembic/versions/0015_scores_v2_term_reports.py tests/infra/test_orm.py
git commit -m "feat(db): migração 0015 — video_scores_v2 e term_reports (R9/R10)"
```

---

### Task 5: Repositórios do `viral-v2`, do relatório e das populações

**Files:**
- Modify: `src/content_factory/app/ports.py`
- Modify: `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Produces:
  - `ViralV2Repository.append_many(rows: Sequence[ViralV2Row]) -> int`
  - `TermReportRepository.append_many(segment_id: int, rows: Sequence[TermReportRow]) -> int`
  - `ViralV2PopulationRepository.population(segment_id: int, window_days: int) -> list[ViralV2PopulationRow]` — por vídeo: `video_id`, `platform`, `views`, `likes`, `comments`, `shares`, `saves`, `followers`, `velocity_per_hour` (par de snapshots mais recentes), `title`, `description`, `published_at`
  - `ViralV2PopulationRepository.attributed_samples(segment_id: int) -> list[TermSampleRow]` — join `video_discoveries` (term, origin) × último `video_scores_v2`
  - `ViralV2PopulationRepository.hashtag_sources(segment_id: int) -> list[HashtagSourceRow]` — vídeos do YouTube com `raw`, `title`, `description`, último `viral_score_v2`

- [ ] **Step 1: Teste que falha** — integração: `append_many` grava 2 linhas em `video_scores_v2` **sem** tocar `video_scores`; `population` devolve só vídeos dos últimos 30 dias; `attributed_samples` traz termo/origem de `video_discoveries`.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

`population` reusa o padrão `DISTINCT ON` de `latest_for_segment` (duas capturas mais recentes por vídeo) e o join com `creators` para `followers_count`:

```python
    async def population(self, segment_id: int, window_days: int) -> list[ViralV2PopulationRow]:
        since = datetime.now(UTC) - timedelta(days=window_days)
        # ... DISTINCT ON (video_id) captura mais recente + rn=2 para a anterior
        # ... velocity = (views - prev_views) / horas entre capturas
        # ... JOIN creators em videos.creator_id para followers
```

`attributed_samples`:

```python
stmt = (
    select(
        VideoDiscoveryRow.term,
        VideoDiscoveryRow.term_origin,
        VideoScoreV2Row.viral_score_v2,
        VideoScoreV2Row.velocity,
        VideoScoreV2Row.engagement,
        VideoScoreV2Row.channel_relative,
        VideoDiscoveryRow.video_id,
    )
    .join(VideoScoreV2Row, VideoScoreV2Row.video_id == VideoDiscoveryRow.video_id)
    .where(VideoDiscoveryRow.segment_id == segment_id)
)
```

(com `DISTINCT ON` no par `(video_id, term)` para o score mais recente por vídeo).

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ports.py src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(db): repositórios do viral-v2, do relatório e das populações (R9/R10/R11)"
```

---

### Task 6: Use-case `score_v2` + mineração (R9/R11); ligar no `score_job`

**Files:**
- Create: `src/content_factory/app/score_v2.py`
- Modify: `src/content_factory/infra/queue/worker.py`, `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_score_v2.py` (novo), `tests/infra/queue/test_worker.py` (editar)

**Interfaces:**
- Produces:
  - `@dataclass class ScoreV2Deps: populations: ViralV2PopulationRepository; scores_v2: ViralV2Repository; terms: SegmentTermRepository; segment: Segment; clock: Callable[[], datetime] = ...`
  - `async def score_v2(deps: ScoreV2Deps) -> ScoreV2Outcome` (`scored`, `skipped_relevance`, `insufficient`, `hashtags_pooled`)
  - `_score_v2_impl` no worker, chamado após o commit do `fase-h` (transação própria).

- [ ] **Step 1: Teste que falha**

```python
async def test_score_v2_filtra_relevancia_e_grava() -> None:
    deps = _score_v2_deps(
        population=[_pop(1, "como fazer vela"), _pop(2, "candle making for beginners")]
    )
    out = await score_v2(deps)
    assert out.scored >= 1
    assert out.skipped_relevance == 1  # "candle ..." fora do nicho (R1)
    assert deps.scores_v2.saved  # linhas em video_scores_v2
    assert deps.terms.pooled_hashtags == []  # sem viral_v2 suficiente, sem hashtags
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
async def score_v2(deps: ScoreV2Deps) -> ScoreV2Outcome:
    window = int(os.getenv("VIRAL_V2_WINDOW_DAYS", str(WINDOW_DAYS)))
    rows = await deps.populations.population(deps.segment.id, window)
    seg = deps.segment
    cfg = TermFilterConfig(anchors=tuple(seg.anchor_terms), blocked=tuple(seg.blocked_terms))
    # R9: relevância vira FILTRO — fora do nicho não entra no ranking
    kept = [r for r in rows if filter_term(f"{r.title} {r.description}", cfg).accepted]
    skipped_relevance = len(rows) - len(kept)
    inputs = [
        ViralInput(
            video_id=r.video_id,
            platform=r.platform,
            segment_id=seg.id,
            velocity_per_hour=r.velocity_per_hour,
            engagement_rate=r.engagement_rate,
            views=r.views,
            channel_followers=r.followers,
        )
        for r in kept
    ]
    scores = score_population(inputs)
    # vídeos filtrados também são gravados (total=None) — a ausência fica auditável
    all_rows = scores + [
        ViralScore(
            video_id=r.video_id,
            total=None,
            velocity=None,
            engagement=None,
            channel_relative=None,
            reach=None,
            population_n=0,
            explanation={"filtered": "relevance"},
        )
        for r in rows
        if r.video_id not in {s.video_id for s in scores}
    ]
    await deps.scores_v2.append_many(_to_v2_rows(all_rows, seg.id, deps.clock()))
    # R11: hashtags do top 25% voltam ao pool como hashtag:<plataforma>
    sources = await deps.populations.hashtag_sources(seg.id)
    mined = mine(sources)
    pooled = 0
    for platform in {h.platform for h in mined}:
        tags = [h for h in mined if h.platform == platform]
        kept_tags, _ = filter_terms([h.tag for h in tags], cfg)
        kept_set = set(kept_tags)
        terms = [
            SegmentTerm(
                segment_id=seg.id,
                term=h.tag,
                origin=f"hashtag:{platform}",
                engagement_score=h.weight,
                rules_version=HASHTAG_MINE_VERSION,
            )
            for h in tags
            if h.tag in kept_set
        ]
        if terms:
            pooled += await deps.terms.upsert_many(seg.id, terms)
    return ScoreV2Outcome(
        scored=len(all_rows),
        skipped_relevance=skipped_relevance,
        insufficient=sum(1 for s in scores if s.total is None),
        hashtags_pooled=pooled,
    )
```

No worker, em `run_score_job._one`, após o commit do `fase-h` e fora do `if web_score_enabled`:

```python
            try:
                await _score_v2_impl(bundle.score_v2)
                await bundle.uow.commit()
            except Exception:
                logger.exception("score viral-v2 falhou — %s (fase-h mantido)", segment.slug)
                await bundle.uow.rollback()
```

Envs: `VIRAL_V2_WINDOW_DAYS=30`, `VIRAL_V2_MIN_POPULATION=5`.

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_score_v2.py tests/infra/queue/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/score_v2.py src/content_factory/infra/queue/worker.py \
        src/content_factory/infra/queue/composition.py \
        tests/app/test_score_v2.py tests/infra/queue/test_worker.py
git commit -m "feat(score): viral-v2 ao lado do fase-h + mineração de hashtags (R9/R11)"
```

---

### Task 7: Use-case `term_report` e CLI `cf term-report` (R10)

**Files:**
- Create: `src/content_factory/app/term_report.py`
- Modify: `src/content_factory/cli/main.py`
- Test: `tests/app/test_term_report.py` (novo), `tests/cli/test_term_report.py` (novo)

**Interfaces:**
- Produces:
  - `async def build_term_report(segment_slug: str, platform: str | None, deps: TermReportDeps) -> TermReportOutcome`
  - `TermReportOutcome.as_counts()` → `{"terms": n, "origins": n, "insufficient": n, "rows_saved": n}`
  - CLI `cf term-report --segment <slug> [--platform] [--out <csv>]` — grava no banco e devolve só o envelope; `--out` escreve CSV **fora** do repositório.

- [ ] **Step 1: Teste que falha**

```python
async def test_term_report_grava_por_termo_e_por_origem() -> None:
    deps = _term_report_deps(samples=_samples(...))
    out = await build_term_report("velas-artesanais", None, deps)
    assert out.as_counts()["terms"] >= 1 and out.as_counts()["origins"] >= 1
    assert {r.scope for r in deps.reports.saved} == {"term", "origin"}
    assert all(r.version == "term-rank-v1" for r in deps.reports.saved)
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

`build_term_report`: lê `attributed_samples(segment_id)` (termo/origem + `viral_v2`/componentes), opcionalmente filtra por plataforma, chama `term_ranking.rank(samples, "term")` e `rank(samples, "origin")`, serializa para `TermReportRow` (como `as_of=deps.clock()`, `version=TERM_RANK_VERSION`, `rules_version=TERM_FILTER_VERSION`) e `append_many`.

CLI segue o padrão de `score-sample` (`_emit`, `exit_code_for`); o CSV é escrito no caminho de `--out` (documentar "fora do repositório — o repositório é público e os títulos são de terceiros").

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_term_report.py tests/cli/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/term_report.py src/content_factory/cli/main.py \
        tests/app/test_term_report.py tests/cli/test_term_report.py
git commit -m "feat(report): cf term-report — ranking por termo e por origem (R10)"
```

---

### Task 8: Encerrar o bloco — gates e aceite

- [ ] **Step 1: Gates** — `make gates` verde.

- [ ] **Step 2: Aceite R9**

```sql
-- fase-h intocado pelo viral-v2
SELECT max(formula_version) FROM video_scores;                                  -- esperado: 'fase-h'
SELECT count(*) FROM video_scores_v2;
SELECT count(*) FROM video_scores_v2 WHERE explanation->>'insufficient' = 'true';
-- e: explicação por componente presente em 100% das linhas
SELECT count(*) FROM video_scores_v2 WHERE jsonb_typeof(explanation) = 'object';
```

- [ ] **Step 3: Aceite R10** — `cf term-report --segment velas-artesanais` (D+1, dados reais) devolve envelope com contagens; conferir no banco linhas com `verdict` e `ci_low`/`ci_high`; termo com `n < 5` sai como `insufficient`.

- [ ] **Step 4: Aceite R11**

```sql
SELECT origin, count(*) FROM segment_terms WHERE origin LIKE 'hashtag:%' GROUP BY origin;
SELECT term, engagement_score FROM segment_terms
WHERE origin = 'hashtag:youtube' ORDER BY engagement_score DESC LIMIT 10;
-- esperado: tags filtradas com score, após o 1º score com viral-v2
```

- [ ] **Step 5: Commit de encerramento** — `git commit -am "docs(plans): bloco 5 concluído — R9/R10/R11 verificados" || true`

---

## Auto-revisão da spec (R9/R10/R11)

- **R9** ✓ componentes normalizados por plataforma+segmento (Task 1), velocidade/engajamento/relativo ao canal/alcance com os pesos pré-registrados, relevância como filtro (Task 6) e recência como janela 30 d (Task 6), `viral-v2` em tabela própria e **nunca** em `video_scores.total` (Tasks 4-6), explicação por componente (Task 1).
- **R10** ✓ `n`, medianas, fração no top 10%, `engagement_score` versionado no pool (`set_trend_scores`→reuso do pool; aqui gravado por `engagement_score`), IC 95% bootstrap seed fixa, `n < 5` = "insuficiente" (Task 2), por termo **e por origem** (Task 7), CLI que grava no banco e devolve só o envelope, CSV opcional fora do repositório (Task 7).
- **R11** ✓ top 25% do `viral-v2` (Task 3), YouTube `snippet.tags` + `#` de título/descrição, filtro R1, ranking por frequência ponderada, entrada no pool como `hashtag:<plataforma>` (Tasks 3/6). Instagram só referência e TikTok fora (a mineração por plataforma existe; as fontes dessas plataformas entram quando houver busca/token).
- Fora deste bloco: R13 (observabilidade) e R14 (idioma) no Bloco 6.
