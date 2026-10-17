# Bloco 1 — Filtro de termos, ranking do Suggest e pool (R1+R2+R4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar o peso fixo `0,5` do Suggest por um ranking determinístico, filtrar os termos fora do nicho/idioma e consolidar tudo num pool de termos por segmento — a fonte única que os blocos seguintes vão buscar.

**Architecture:** Três peças puras em `domain/` (`term_filter`, `kw_rank`, e os modelos) + uma tabela de pool (`segment_terms`) e uma de histórico de dias (`keyword_signal_days`) em `infra/`. O `research_pipeline` (app) passa a: expor a **posição** de cada sugestão (hoje descartada no provider), ranquear (posição + recorrência entre sementes + persistência entre dias), filtrar (idioma/âncora/bloqueio) e gravar o pool. Nada aqui toca `video_scores`, `viral_score` nem o q1 da web.

**Tech Stack:** Python 3.14, pydantic v2, SQLAlchemy 2 async, Alembic, pytest (asyncio), ruff, pyright strict.

## Global Constraints

- `domain/` é **puro** (sem I/O, sem `infra/`, sem `app/`); `app/` depende de portas; `infra/` implementa portas e é injetada nos entrypoints. Dependência unidirecional `cli/worker → app → domain`. (fonte: `CLAUDE.md`, `tests/app/test_layers.py`)
- `pyright` strict com **0 erros** — nunca rebaixar config.
- `ruff` (linha 100, aspas duplas) e `ruff format` limpos.
- TDD: teste falha → implementação mínima → teste passa → commit.
- Nada de segredo em arquivo rastreado; `scripts/check_privacy.py` reprova `path pessoal` (`/home/<user>/`) e credencial literal — **não usar caminhos absolutos de home em código, docs, commits ou mensagens**.
- Envelopes: a CLI devolve só `{ok, command, ...}`; **dados no banco** (`app/results.py`, `CLAUDE.md`).
- Gates antes de "pronto": `make gates` = `pytest` (não-integration) + `ruff` + `pyright` + `alembic check` + `bandit` + `pip-audit` + `mkdocs build --strict` + `privacy`.
- Migração é **aditiva** e numerada na sequência (`0011` segue `0010`); `alembic check` compara ORM ↔ migração.
- `term-filter-v1` é o filtro do pool/hashtags/descoberta. O `filter-v1`/`intent-v3` da branch web fica **só no q1** — os dois convivem, não se substituem.
- Versões fixas desta entrega: `term-filter-v1`, `kw-rank-v1`.

## Pré-requisito (revisão QA)

- **Rebase sobre a `main` com a F1 web mergeada** (`db3ad2e`, `4f56a0a`, `9e855a6`): este bloco usa `SegmentConfig.probe_terms` e `domain/suggest_probes.py`, que só existem nesses commits, e edita os mesmos arquivos (`research_pipeline.py`, `models.py`, os 3 YAMLs). Implementar sobre a `main` atual gera conflito e quebra o import.
- As sondas (`probe_terms`) continuam alimentando **só o Suggest**; as frases que elas geram passam pelo R1 como qualquer outra.

## Referência da especificação

`docs/ai-plans/2026-10-16-especificacao-viral-por-tema.md` §4 (R1, R2, R4) e §2 (estado atual). Ler antes de começar.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/domain/term_filter.py` (novo) | Filtro puro `term-filter-v1`: idioma, âncora, bloqueio + motivo do descarte. |
| `src/content_factory/domain/kw_rank.py` (novo) | `SuggestHit` + ranking puro `kw-rank-v1` (posição, recorrência, persistência). |
| `src/content_factory/domain/models.py` (editar) | `SegmentConfig.anchor_terms/blocked_terms`; `SegmentTerm`. |
| `segments/velas-artesanais.yaml`, `sabonetes-artesanais.yaml`, `resina-artesanal.yaml` (editar) | `anchor_terms` + `blocked_terms` por segmento. |
| `src/content_factory/infra/collectors/web_suggest.py` (editar) | Expor `(termo, semente, posição)` em vez de `KeywordSignal` com peso 0,5. |
| `src/content_factory/app/ports.py` (editar) | `KeywordExpander.expand → list[SuggestHit]`; portas `KeywordSignalRepository` (+dias) e `SegmentTermRepository`. |
| `src/content_factory/infra/db/orm.py` (editar) | `SegmentTermRow`, `KeywordSignalDayRow`. |
| `alembic/versions/0011_segment_terms_and_signal_days.py` (novo) | As duas tabelas novas. |
| `src/content_factory/infra/db/repositories.py` (editar) | `KeywordSignalRepositoryImpl` (+dias) e `SegmentTermRepositoryImpl`. |
| `src/content_factory/app/research_pipeline.py` (editar) | Filtrar + ranquear + gravar o pool no ciclo diário. |
| `src/content_factory/infra/queue/composition.py` (editar) | Injetar o repo do pool no `ResearchDeps`. |
| `tests/domain/test_term_filter.py`, `tests/domain/test_kw_rank.py`, `tests/infra/collectors/test_web_suggest.py`, `tests/app/test_research_pipeline.py`, `tests/infra/test_repositories.py` (editar/criar) | TDD. |

---

### Task 1: Filtro de termos puro (`term-filter-v1`)

**Files:**
- Create: `src/content_factory/domain/term_filter.py`
- Test: `tests/domain/test_term_filter.py`

**Interfaces:**
- Consumes: `content_factory.domain.text.{contains_phrase, word_tokens}`.
- Produces:
  - `TERM_FILTER_VERSION: str = "term-filter-v1"`
  - `@dataclass(frozen=True) class TermFilterConfig: anchors: tuple[str, ...] = (); blocked: tuple[str, ...] = ()`
  - `@dataclass(frozen=True) class TermVerdict: accepted: bool; reason: str | None`
  - `def filter_term(term: str, config: TermFilterConfig) -> TermVerdict` — frases (Suggest/Trends).
  - `def filter_hashtag(tag: str, config: TermFilterConfig) -> TermVerdict` — hashtags (R11): âncora/bloqueio por **substring** da tag normalizada, sem checagem de idioma.
  - `def filter_terms(terms: Sequence[str], config: TermFilterConfig) -> tuple[list[str], list[tuple[str, str]]]` — `(aceitos, [(termo, motivo)])`.

> **Revisão QA (16/10).** A 1ª versão deste plano reprovava por idioma quando faltava palavra-função PT. Medido com o código do plano: aceitava espanhol (`como hacer velas aromaticas`) e descartava PT curto sem acento (`velas aromaticas`, `vela`, `sabonete natural caseiro`); todas as hashtags (`velaaromatica`) caíam por `lang`. Regra corrigida: **idioma só reprova com evidência positiva** de outra língua (listas fechadas EN + ES); quem segura o nicho é a âncora. O código abaixo foi executado contra estes testes (pytest, pyright strict e ruff limpos).

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/domain/test_term_filter.py
import pytest

from content_factory.domain.term_filter import (
    TERM_FILTER_VERSION,
    TermFilterConfig,
    filter_hashtag,
    filter_term,
    filter_terms,
)

VELAS = TermFilterConfig(
    anchors=("vela", "velas", "pavio", "parafina", "cera"),
    blocked=("tendencia alcista", "bajista", "grafico", "ignicao", "motor", "martillo"),
)
SABONETES = TermFilterConfig(
    anchors=("sabonete", "sabonetes", "saboaria", "glicerinada", "saponificacao"),
    blocked=("natura", "phebo", "dove", "lux", "protex", "granado"),
)
RESINA = TermFilterConfig(
    anchors=("resina", "epoxi", "epoxy"),
    blocked=("odontologica", "dente", "dentes", "clareia", "dental", "ortodontia", "mdf"),
)


def test_version() -> None:
    assert TERM_FILTER_VERSION == "term-filter-v1"


# Casos REAIS do Suggest (QA de 16/10, sondas probe-v2) + PT curto sem acento,
# que o Suggest devolve com frequência e NÃO pode cair por idioma.
@pytest.mark.parametrize(
    ("cfg", "term"),
    [
        (VELAS, "velas aromaticas"),
        (VELAS, "vela aromática"),
        (VELAS, "vela"),
        (VELAS, "como fazer vela de soja"),
        (VELAS, "pavio de madeira para vela"),
        (VELAS, "velas decorativas para casamento"),
        (SABONETES, "sabonete natural caseiro"),
        (SABONETES, "sabonete artesanal como fazer"),
        (RESINA, "resina epoxi para iniciantes"),
        (RESINA, "mesa de resina"),
    ],
)
def test_aceita_pt_do_nicho(cfg: TermFilterConfig, term: str) -> None:
    assert filter_term(term, cfg).accepted


@pytest.mark.parametrize(
    ("cfg", "term", "reason"),
    [
        (VELAS, "como hacer velas aromaticas", "lang"),  # espanhol com "como"
        (VELAS, "velas caseras faciles paso a paso", "lang"),
        (VELAS, "vela martillo en tendencia bajista", "blocked"),  # bloqueio vence
        (VELAS, "candle making for beginners", "lang"),
        (VELAS, "significado espiritual da cor", "no_anchor"),
        (VELAS, "envelope revelar", "no_anchor"),  # palavra inteira, não substring
        (SABONETES, "sabonete natura ekos", "blocked"),
        (SABONETES, "handmade soap for beginners", "lang"),
        (RESINA, "resina nao clareia", "blocked"),
        (RESINA, "resin art for beginners tutorial", "lang"),
        (RESINA, "artesanato em mdf", "blocked"),
    ],
)
def test_rejeita_com_motivo(cfg: TermFilterConfig, term: str, reason: str) -> None:
    verdict = filter_term(term, cfg)
    assert verdict.accepted is False
    assert verdict.reason == reason


def test_sem_ancoras_configuradas_nao_reprova_ancora() -> None:
    assert filter_term("qualquer coisa em portugues", TermFilterConfig()).accepted


def test_filter_terms_devolve_rejeitados_com_motivo() -> None:
    aceitos, rejeitados = filter_terms(["vela", "candle for x", "presente"], VELAS)
    assert aceitos == ["vela"]
    assert rejeitados == [("candle for x", "lang"), ("presente", "no_anchor")]


def test_hashtag_casa_ancora_por_substring_sem_idioma() -> None:
    assert filter_hashtag("velaaromatica", VELAS).accepted
    assert filter_hashtag("#VelasArtesanais", VELAS).accepted
    assert filter_hashtag("decoracao", VELAS).reason == "no_anchor"
    assert filter_hashtag("sabonetenatura", SABONETES).reason == "blocked"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/domain/test_term_filter.py -q`
Expected: FAIL — `ModuleNotFoundError: content_factory.domain.term_filter`.

- [ ] **Step 3: Implementação mínima**

```python
# src/content_factory/domain/term_filter.py
"""Filtro de termos `term-filter-v1` (R1) — puro, sem I/O.

Precedência DOCUMENTADA do motivo: `blocked` → `lang` → `no_anchor`.

Idioma só reprova com EVIDÊNCIA POSITIVA de outra língua (listas fechadas de
inglês e espanhol). Ausência de palavra-função PT NÃO reprova: o Suggest
devolve frases curtas sem acento ("velas aromaticas"), que são PT. Quem
segura o nicho é a âncora.

Hashtags (`filter_hashtag`) não têm espaços nem palavras-função: âncora e
bloqueio casam por SUBSTRING da tag normalizada e não há checagem de idioma
(o idioma vem do vídeo de origem, R14).

O `filter-v1`/`intent-v3` da branch web NÃO é substituído por este: aquele
vive só no protocolo q1; este governa pool, hashtags e descoberta.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from content_factory.domain.text import contains_phrase, word_tokens

TERM_FILTER_VERSION = "term-filter-v1"
REASON_ORDER: tuple[str, ...] = ("blocked", "lang", "no_anchor")

# Marcadores de OUTRA língua — palavras que não existem (normalizadas) em PT.
EN_MARKERS = frozenset(
    {"for", "the", "and", "with", "of", "how", "to", "make", "making", "diy", "best", "easy"}
    # substantivos EN do nicho (revisão QA do PR #50): inglês sem palavra-função
    # ("soy wax candles") caía como no_anchor — descarte certo, motivo errado
    | {"candle", "candles", "wick", "wax", "soap", "soaps", "resin", "handmade", "homemade"}
)
ES_MARKERS = frozenset(
    {
        "y",
        "el",
        "los",
        "las",
        "del",
        "con",
        "en",
        "hacer",
        "caseras",
        "caseros",
        "faciles",
        "paso",
        "una",
        "jabon",
        "jabones",
        "veladora",
        "veladoras",
    }
)
NON_PT_PHRASES: tuple[str, ...] = ("de la", "para la", "para el", "en la")


@dataclass(frozen=True)
class TermFilterConfig:
    anchors: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()


@dataclass(frozen=True)
class TermVerdict:
    accepted: bool
    reason: str | None  # None | "blocked" | "lang" | "no_anchor"


def _is_other_language(words: list[str]) -> bool:
    if any(w in EN_MARKERS or w in ES_MARKERS for w in words):
        return True
    return any(contains_phrase(words, p) for p in NON_PT_PHRASES)


def _verdict(checks: dict[str, bool]) -> TermVerdict:
    reason = next((r for r in REASON_ORDER if checks.get(r, False)), None)
    return TermVerdict(accepted=reason is None, reason=reason)


def filter_term(term: str, config: TermFilterConfig) -> TermVerdict:
    words = word_tokens(term)
    return _verdict(
        {
            "blocked": any(contains_phrase(words, b) for b in config.blocked),
            "lang": _is_other_language(words),
            "no_anchor": bool(config.anchors)
            and not any(contains_phrase(words, a) for a in config.anchors),
        }
    )


def _compact(text: str) -> str:
    return "".join(word_tokens(text))


def filter_hashtag(tag: str, config: TermFilterConfig) -> TermVerdict:
    compact = _compact(tag)
    return _verdict(
        {
            "blocked": any(_compact(b) in compact for b in config.blocked),
            "no_anchor": bool(config.anchors)
            and not any(_compact(a) in compact for a in config.anchors),
        }
    )


def filter_terms(
    terms: Sequence[str], config: TermFilterConfig
) -> tuple[list[str], list[tuple[str, str]]]:
    """(aceitos, [(termo, motivo)] dos rejeitados) — ordem de entrada preservada."""
    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []
    for term in terms:
        verdict = filter_term(term, config)
        if verdict.reason is None:
            accepted.append(term)
        else:
            rejected.append((term, verdict.reason))
    return accepted, rejected
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/domain/test_term_filter.py -q`
Expected: PASS (25 passed).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/term_filter.py tests/domain/test_term_filter.py
git commit -m "feat(domain): term-filter-v1 — idioma por evidência, âncora e bloqueio (R1)"
```

---

### Task 2: Ranking do Suggest puro (`kw-rank-v1`)

**Files:**
- Create: `src/content_factory/domain/kw_rank.py`
- Test: `tests/domain/test_kw_rank.py`

**Interfaces:**
- Consumes: nada de `infra`.
- Produces:
  - `KW_RANK_VERSION: str = "kw-rank-v1"`
  - `@dataclass(frozen=True) class SuggestHit: term: str; seed: str; position: int` (posição 1-based na lista daquela semente)
  - `def rank_suggestions(hits: Sequence[SuggestHit], days_seen: Mapping[str, int]) -> dict[str, float]` — `termo → peso em [0,1]`.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/domain/test_kw_rank.py
from content_factory.domain.kw_rank import KW_RANK_VERSION, SuggestHit, rank_suggestions


def test_version() -> None:
    assert KW_RANK_VERSION == "kw-rank-v1"


def test_posicao_1_vale_mais_que_posicao_10() -> None:
    top = rank_suggestions([SuggestHit("vela", "velas", 1)], {})
    low = rank_suggestions([SuggestHit("vela", "velas", 10)], {})
    assert top["vela"] > low["vela"]


def test_recorrencia_entre_sementes_soma() -> None:
    uma = rank_suggestions([SuggestHit("vela", "s1", 1)], {})
    tres = rank_suggestions([SuggestHit("vela", s, 1) for s in ("s1", "s2", "s3")], {})
    assert tres["vela"] > uma["vela"]


def test_recorrencia_satura_no_cap() -> None:
    tres = rank_suggestions([SuggestHit("vela", s, 1) for s in ("s1", "s2", "s3")], {})
    cinco = rank_suggestions([SuggestHit("vela", s, 1) for s in ("s1", "s2", "s3", "s4", "s5")], {})
    assert tres["vela"] == cinco["vela"]


def test_persistencia_entre_dias_soma() -> None:
    hoje = rank_suggestions([SuggestHit("vela", "s1", 1)], {"vela": 1})
    semana = rank_suggestions([SuggestHit("vela", "s1", 1)], {"vela": 7})
    assert semana["vela"] > hoje["vela"]


def test_peso_sempre_no_intervalo_unitario() -> None:
    hits = [SuggestHit("vela", "s1", 1), SuggestHit("vela", "s2", 2)]
    peso = rank_suggestions(hits, {"vela": 7})["vela"]
    assert 0.0 <= peso <= 1.0


def test_termo_sem_dias_conhecidos_assume_1() -> None:
    assert rank_suggestions([SuggestHit("vela", "s1", 1)], {})["vela"] > 0
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/domain/test_kw_rank.py -q`
Expected: FAIL — `ModuleNotFoundError: content_factory.domain.kw_rank`.

- [ ] **Step 3: Implementação mínima**

```python
# src/content_factory/domain/kw_rank.py
"""Ranking do Suggest `kw-rank-v1` (R2) — puro e determinístico.

Substitui o peso fixo 0,5. Peso em [0,1] = média ponderada de:

- **posição**: média de `1/posição` entre as sementes que trouxeram a frase;
- **recorrência**: nº de sementes distintas / cap;
- **persistência**: nº de dias distintos (últimos 7, **incluindo hoje** — o app grava o dia de hoje ANTES de ler) / 7.

Pesos da combinação são constantes versionadas: bumpar `KW_RANK_VERSION`
quando mudarem.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

KW_RANK_VERSION = "kw-rank-v1"
W_POSITION = 0.5
W_RECURRENCE = 0.3
W_PERSISTENCE = 0.2
RECURRENCE_CAP = 3  # 3+ sementes distintas satura a recorrência
PERSISTENCE_DAYS = 7


@dataclass(frozen=True)
class SuggestHit:
    term: str
    seed: str
    position: int  # 1-based na lista daquela semente


def rank_suggestions(hits: Sequence[SuggestHit], days_seen: Mapping[str, int]) -> dict[str, float]:
    by_term: dict[str, list[SuggestHit]] = {}
    for hit in hits:
        by_term.setdefault(hit.term, []).append(hit)

    weights: dict[str, float] = {}
    for term, term_hits in by_term.items():
        position = sum(1.0 / h.position for h in term_hits) / len(term_hits)
        recurrence = min(len({h.seed for h in term_hits}) / RECURRENCE_CAP, 1.0)
        persistence = min(days_seen.get(term, 1) / PERSISTENCE_DAYS, 1.0)
        weights[term] = round(
            W_POSITION * position + W_RECURRENCE * recurrence + W_PERSISTENCE * persistence, 6
        )
    return weights
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/domain/test_kw_rank.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/kw_rank.py tests/domain/test_kw_rank.py
git commit -m "feat(domain): kw-rank-v1 — posição, recorrência e persistência (R2)"
```

---

### Task 3: `SegmentConfig` ganha âncoras/bloqueios; modelo `SegmentTerm`

**Files:**
- Modify: `src/content_factory/domain/models.py`
- Test: `tests/domain/test_models.py` (editar)

**Interfaces:**
- Produces:
  - `SegmentConfig.anchor_terms: list[str] = []`, `SegmentConfig.blocked_terms: list[str] = []`
  - `class SegmentTerm(BaseModel)`: `segment_id: int`; `term: str`; `origin: str`; `kw_rank: float | None = None`; `trend_score: float | None = None`; `engagement_score: float | None = None`; `status: str = "active"`; `exclude_reason: str | None = None`; `first_seen_at: datetime | None = None`; `last_seen_at: datetime | None = None`; `rules_version: str = ""`.

- [ ] **Step 1: Teste que falha**

```python
# tests/domain/test_models.py  (adicionar)
from content_factory.domain.models import SegmentConfig, SegmentTerm


def test_segment_config_tem_ancoras_e_bloqueios_por_padrao() -> None:
    cfg = SegmentConfig(slug="s", name="S", platforms=["youtube"], keywords=["k"])
    assert cfg.anchor_terms == []
    assert cfg.blocked_terms == []


def test_segment_term_valores_por_padrao() -> None:
    t = SegmentTerm(segment_id=1, term="vela", origin="suggest")
    assert t.status == "active"
    assert t.exclude_reason is None
    assert t.kw_rank is None
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/domain/test_models.py -q` → FAIL (`anchor_terms`/`SegmentTerm` inexistentes).

- [ ] **Step 3: Implementar em `models.py`**

Adicionar em `SegmentConfig`, logo após `probe_terms`:

```python
    # R1: âncoras do nicho (ex.: vela, pavio, cera). Frase sem nenhuma âncora
    # é descartada. Vazio = checagem de âncora desligada para o segmento.
    anchor_terms: list[str] = Field(default_factory=list)
    # R1: lista curta de bloqueio por segmento (marcas/termos fora do nicho).
    blocked_terms: list[str] = Field(default_factory=list)
```

Adicionar ao fim de `models.py`:

```python
class SegmentTerm(BaseModel):
    """Termo do pool do segmento (R4) — fonte única das buscas."""

    segment_id: int
    term: str
    origin: str  # seed | suggest | trends | hashtag:youtube | hashtag:instagram | hashtag:tiktok
    kw_rank: float | None = None
    trend_score: float | None = None
    engagement_score: float | None = None
    status: str = "active"  # active | excluded
    exclude_reason: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    rules_version: str = ""
```

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/domain/test_models.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): anchor_terms/blocked_terms no segmento e modelo SegmentTerm (R1/R4)"
```

---

### Task 4: Âncoras e bloqueios nos YAMLs dos 3 segmentos

**Files:**
- Modify: `segments/velas-artesanais.yaml`, `segments/sabonetes-artesanais.yaml`, `segments/resina-artesanal.yaml`
- Test: `tests/app/test_segment.py` (editar)

**Interfaces:**
- Consumes: `SegmentConfig.anchor_terms`, `SegmentConfig.blocked_terms`.
- Produces: os 3 YAMLs válidos com `anchor_terms`/`blocked_terms`.

- [ ] **Step 1: Teste que falha**

```python
# tests/app/test_segment.py  (adicionar)
from pathlib import Path

from content_factory.app.segment import load_segment_file

_REPO = Path(__file__).resolve().parents[2]


def test_segmentos_pt_tem_ancoras_e_bloqueios() -> None:
    for slug in ("velas-artesanais", "sabonetes-artesanais", "resina-artesanal"):
        cfg = load_segment_file(_REPO / "segments" / f"{slug}.yaml")
        assert cfg.anchor_terms, slug
        assert cfg.blocked_terms, slug
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/app/test_segment.py::test_segmentos_pt_tem_ancoras_e_bloqueios -q` → FAIL (listas vazias).

- [ ] **Step 3: Editar os YAMLs** (acrescentar após `keywords:`)

`segments/velas-artesanais.yaml`:
```yaml
anchor_terms: [vela, velas, pavio, parafina, cera]
blocked_terms: [tendencia alcista, bajista, grafico, ignicao, motor, martillo, carro, boticario, granado]
```
`segments/sabonetes-artesanais.yaml`:
```yaml
anchor_terms: [sabonete, sabonetes, saboaria, glicerinada, saponificacao]
blocked_terms: [natura, phebo, dove, lux, protex, granado, boticario, marcas]
```
`segments/resina-artesanal.yaml`:
```yaml
anchor_terms: [resina, epoxi, epoxy]
blocked_terms: [odontologica, odontologia, dente, dentes, clareia, dental, ortodontia, mdf, composta, piso, 3d]
```

> Âncoras de resina sem `molde`/`pigmento`/`encapsulada`: são genéricas (`molde de silicone para bolo` passaria). `mdf` e `clareia` vêm do QA real de 16/10 (consultas fora do nicho no sorteio cego). Os YAMLs já têm `probe_terms` (F1 web) — acrescentar, não substituir.

- [ ] **Step 4: Rodar e ver passar** — o teste acima PASS.

- [ ] **Step 5: Commit**

```bash
git add segments/velas-artesanais.yaml segments/sabonetes-artesanais.yaml \
        segments/resina-artesanal.yaml tests/app/test_segment.py
git commit -m "feat(segments): anchor_terms e blocked_terms nos 3 segmentos PT (R1)"
```

---

### Task 5: Suggest expõe posição — porta `KeywordExpander → list[SuggestHit]`

**Files:**
- Modify: `src/content_factory/infra/collectors/web_suggest.py`
- Modify: `src/content_factory/app/ports.py`
- Modify: `src/content_factory/app/research_pipeline.py` (ajuste do tipo; comportamento completo na Task 8)
- Test: `tests/infra/collectors/test_web_suggest.py` (editar)

**Interfaces:**
- Consumes: `content_factory.domain.kw_rank.SuggestHit`.
- Produces: `KeywordExpander.expand(seeds: list[str], depth: int) -> list[SuggestHit]`; `WebSuggestProvider.expand` devolve um `SuggestHit` por `(semente, sugestão)` com `position` 1-based.

- [ ] **Step 1: Teste que falha**

```python
# tests/infra/collectors/test_web_suggest.py  (adicionar/reusar transport fake)
from content_factory.infra.collectors.web_suggest import WebSuggestProvider


def test_expand_expoe_posicao_e_semente() -> None:
    # transport fake devolve, para cada semente, a lista ["a", "b"]
    provider = WebSuggestProvider(transport=_ok_transport({"s1": ["a", "b"]}))
    hits = asyncio.run(provider.expand(["s1"], 0))
    assert [(h.term, h.seed, h.position) for h in hits] == [
        ("a", "s1", 1),
        ("b", "s1", 2),
    ]
```

> Use o `_ok_transport` já existente no arquivo de teste (o mesmo seam `transport: Callable[[httpx.Request], httpx.Response]`). Se o helper atual devolver uma única lista, crie um `dict` de semente→lista no próprio teste.

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/infra/collectors/test_web_suggest.py -q` → FAIL (não há `.position`).

- [ ] **Step 3: Implementar**

Em `web_suggest.py`: trocar `_fetch_all`/`_map` por:

```python
from content_factory.domain.kw_rank import SuggestHit

    def _fetch_all(self, seeds: list[str]) -> list[SuggestHit]:
        """Sugestões por semente, COM posição (1-based). Dedupe por (semente, termo)."""
        out: list[SuggestHit] = []
        for seed in seeds:
            seen: set[str] = set()
            for position, sug in enumerate(self._fetch_one(seed), start=1):
                key = sug.casefold().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(SuggestHit(term=sug, seed=seed, position=position))
        return out
```

`expand` passa a devolver `list[SuggestHit]` (sem `_map`/`weight`):

```python
async def expand(self, seeds: list[str], depth: int) -> list[SuggestHit]:
    _ = depth
    self._breaker.check()
    if not seeds:
        return []
    try:
        return await asyncio.to_thread(self._fetch_all, seeds)
    except NetworkError, RateLimited, UpstreamChanged:
        self._breaker.record_failure()
        raise
    except Exception as exc:
        self._breaker.record_failure()
        raise self._classify(exc) from exc
    finally:
        ...  # mantém o record_success atual no caminho feliz
```

> Mantenha `record_success()` só no sucesso (não em `finally`); o `finally` acima é só ilustrativo da troca — o corpo real é igual ao atual, apenas o retorno muda.

Em `app/ports.py`:

```python
from content_factory.domain.kw_rank import SuggestHit


class KeywordExpander(Protocol):
    """Descoberta de sugestões relacionadas (Google Suggest)."""

    platform: ClassVar[str]

    async def expand(self, seeds: list[str], depth: int) -> list[SuggestHit]: ...
```

- [ ] **Step 4: Rodar e ver passar** — o teste novo PASS. Em `research_pipeline.py`, ajuste mínimo para o novo tipo (o ciclo completo vem na Task 8): a fase web passa a receber `list[SuggestHit]`.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/collectors/web_suggest.py \
        src/content_factory/app/ports.py \
        src/content_factory/app/research_pipeline.py \
        tests/infra/collectors/test_web_suggest.py
git commit -m "feat(web_suggest): expõe posição da sugestão por semente (R2)"
```

---

### Task 6: Tabelas `segment_terms` e `keyword_signal_days` (migração `0011`)

**Files:**
- Modify: `src/content_factory/infra/db/orm.py`
- Create: `alembic/versions/0011_segment_terms_and_signal_days.py`
- Test: `tests/infra/test_orm.py` (editar)

**Interfaces:**
- Produces: `SegmentTermRow`, `KeywordSignalDayRow` com as colunas abaixo.

- [ ] **Step 1: Teste que falha**

```python
# tests/infra/test_orm.py  (adicionar)
from content_factory.infra.db.orm import KeywordSignalDayRow, SegmentTermRow


def test_tabelas_do_pool_existem() -> None:
    assert SegmentTermRow.__tablename__ == "segment_terms"
    assert KeywordSignalDayRow.__tablename__ == "keyword_signal_days"
    assert {c.name for c in SegmentTermRow.__table__.columns} >= {
        "segment_id",
        "term",
        "origin",
        "kw_rank",
        "trend_score",
        "engagement_score",
        "status",
        "exclude_reason",
        "first_seen_at",
        "last_seen_at",
        "rules_version",
    }
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/infra/test_orm.py -q` → FAIL.

- [ ] **Step 3: Implementar ORM + migração**

Em `orm.py`:

```python
class SegmentTermRow(Base):
    """Pool de termos do segmento (R4). Único por (segment_id, term, origin).

    A origem faz parte da chave: a mesma frase pode ser seed E sugestão (o
    Suggest devolve a própria seed), e uma hashtag pode coincidir com um termo.
    Com chave só em (segment_id, term) o upsert trocava a origem e rebaixava a
    seed (revisão QA).
    """

    __tablename__ = "segment_terms"
    __table_args__ = (
        UniqueConstraint("segment_id", "term", "origin", name="uq_segment_term"),
        Index("ix_segment_terms_segment_status", "segment_id", "status"),
        Index("ix_segment_terms_segment_origin", "segment_id", "origin"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    term: Mapped[str] = mapped_column(String(200))
    origin: Mapped[str] = mapped_column(String(32))
    kw_rank: Mapped[float | None] = mapped_column(Float)
    trend_score: Mapped[float | None] = mapped_column(Float)
    engagement_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="active")
    exclude_reason: Mapped[str | None] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    rules_version: Mapped[str] = mapped_column(String(32), default="")


class KeywordSignalDayRow(Base):
    """Dias distintos em que um termo do Suggest apareceu (R2 — persistência)."""

    __tablename__ = "keyword_signal_days"
    __table_args__ = (UniqueConstraint("segment_id", "keyword", "source", "seen_on"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    keyword: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32))
    seen_on: Mapped[date] = mapped_column(Date)
```

(`from datetime import date` e `Date` no import do SQLAlchemy.)

`alembic/versions/0011_segment_terms_and_signal_days.py` (mesmo estilo de `0010`):

```python
"""segment_terms + keyword_signal_days (pool e persistência do Suggest: R4/R2)

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "segment_terms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("kw_rank", sa.Float(), nullable=True),
        sa.Column("trend_score", sa.Float(), nullable=True),
        sa.Column("engagement_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exclude_reason", sa.String(length=32), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "term", "origin", name="uq_segment_term"),
    )
    op.create_index("ix_segment_terms_segment_status", "segment_terms", ["segment_id", "status"])
    op.create_index("ix_segment_terms_segment_origin", "segment_terms", ["segment_id", "origin"])
    op.create_table(
        "keyword_signal_days",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("seen_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "keyword", "source", "seen_on"),
    )


def downgrade() -> None:
    op.drop_table("keyword_signal_days")
    op.drop_index("ix_segment_terms_segment_origin", table_name="segment_terms")
    op.drop_index("ix_segment_terms_segment_status", table_name="segment_terms")
    op.drop_table("segment_terms")
```

- [ ] **Step 4: Rodar e ver passar + sincronia ORM↔migração**

Run: `uv run pytest tests/infra/test_orm.py -q` → PASS
Run: `uv run alembic check` → "No new upgrade operations detected." (sem drift)

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/infra/db/orm.py alembic/versions/0011_segment_terms_and_signal_days.py tests/infra/test_orm.py
git commit -m "feat(db): migração 0011 — segment_terms e keyword_signal_days (R4/R2)"
```

---

### Task 7: Repositórios do pool e dos dias do Suggest

**Files:**
- Modify: `src/content_factory/app/ports.py`
- Modify: `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — precisa Postgres de pé; marque `@pytest.mark.integration` se o arquivo já for de integração)

**Interfaces:**
- Produces:
  - `KeywordSignalRepository.record_days(segment_id: int, keywords: Sequence[str], source: str, day: date) -> int`
  - `KeywordSignalRepository.days_seen(segment_id: int, source: str, days: int, today: date) -> dict[str, int]`
  - `SegmentTermRepository` (Protocol): `upsert_many(segment_id, terms: Sequence[SegmentTerm]) -> int` (devolve linhas gravadas); `top_by_origin(segment_id, origin, limit, order_by: Literal["kw_rank", "trend_score", "engagement_score"] = "kw_rank") -> list[SegmentTerm]` (só `active`, NULL por último, desempate por `term`); `counts_by_origin(segment_id) -> dict[str, int]`; `all_for_segment(segment_id, status: str | None = None) -> list[SegmentTerm]`; `set_status(segment_id, origin, keep: Sequence[str], reason: str) -> int` (marca `excluded` com `reason` os termos ativos da origem fora de `keep` — usado pelas hashtags no Bloco 5)

- [ ] **Step 1: Teste que falha** (repositório em memória já existe? não — teste o SQL)
Adicione um teste de integração (`@pytest.mark.integration`, banco `TEST_DATABASE_URL` descartável) que: cria segmento, faz `upsert_many` de 3 termos (1 ativo, 1 excluído com motivo, e a MESMA frase como `seed` e `suggest`), lê `counts_by_origin` e `all_for_segment`, confere que a seed continua `seed` (2 linhas para a frase), que `upsert_many` de novo atualiza `last_seen_at`/scores sem duplicar, que **`first_seen_at` não muda** no conflito, e que `top_by_origin(..., order_by="engagement_score")` ordena por esse campo.

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/infra/test_repositories.py -q -k segment_term` → FAIL.

- [ ] **Step 3: Implementar**

Em `repositories.py`:

```python
class SegmentTermRepositoryImpl(SegmentTermRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_many(self, segment_id: int, terms: Sequence[SegmentTerm]) -> int:
        now = datetime.now(UTC)
        for t in terms:
            stmt = pg_insert(SegmentTermRow).values(
                segment_id=segment_id,
                term=t.term,
                origin=t.origin,
                kw_rank=t.kw_rank,
                trend_score=t.trend_score,
                engagement_score=t.engagement_score,
                status=t.status,
                exclude_reason=t.exclude_reason,
                first_seen_at=now,
                last_seen_at=now,
                rules_version=t.rules_version,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["segment_id", "term", "origin"],
                set_={
                    "kw_rank": stmt.excluded.kw_rank,
                    "trend_score": stmt.excluded.trend_score,
                    "engagement_score": stmt.excluded.engagement_score,
                    "status": stmt.excluded.status,
                    "exclude_reason": stmt.excluded.exclude_reason,
                    "last_seen_at": now,
                    "rules_version": stmt.excluded.rules_version,
                },
            )
            await self._s.execute(stmt)
        await self._s.flush()
        return len(terms)

    async def top_by_origin(
        self,
        segment_id: int,
        origin: str,
        limit: int,
        order_by: Literal["kw_rank", "trend_score", "engagement_score"] = "kw_rank",
    ) -> list[SegmentTerm]:
        # A ordem depende da origem: suggest por kw_rank, a vaga trends por
        # trend_score, hashtags por engagement_score (sem isso as hashtags
        # sairiam em ordem ALFABÉTICA — revisão QA).
        column = {
            "kw_rank": SegmentTermRow.kw_rank,
            "trend_score": SegmentTermRow.trend_score,
            "engagement_score": SegmentTermRow.engagement_score,
        }[order_by]
        stmt = (
            select(SegmentTermRow)
            .where(
                SegmentTermRow.segment_id == segment_id,
                SegmentTermRow.origin == origin,
                SegmentTermRow.status == "active",
            )
            .order_by(column.desc().nullslast(), SegmentTermRow.term)
            .limit(limit)
        )
        return [_segment_term(r) for r in (await self._s.execute(stmt)).scalars().all()]

    async def counts_by_origin(self, segment_id: int) -> dict[str, int]:
        stmt = (
            select(SegmentTermRow.origin, func.count(SegmentTermRow.id))
            .where(SegmentTermRow.segment_id == segment_id)
            .group_by(SegmentTermRow.origin)
        )
        return {str(o): int(n) for o, n in (await self._s.execute(stmt)).all()}

    async def all_for_segment(
        self, segment_id: int, status: str | None = None
    ) -> list[SegmentTerm]:
        stmt = select(SegmentTermRow).where(SegmentTermRow.segment_id == segment_id)
        if status is not None:
            stmt = stmt.where(SegmentTermRow.status == status)
        rows = (await self._s.execute(stmt.order_by(SegmentTermRow.term))).scalars().all()
        return [_segment_term(r) for r in rows]
```

Implemente também `set_status` (UPDATE ... `status='excluded'`, `exclude_reason=reason` WHERE origem = `origin`, ativo e `term NOT IN keep`). Adicione o helper `_segment_term(row) -> SegmentTerm` e, em `KeywordSignalRepositoryImpl`:

```python
async def record_days(
    self, segment_id: int, keywords: Sequence[str], source: str, day: date
) -> int:
    for kw in keywords:
        stmt = (
            pg_insert(KeywordSignalDayRow)
            .values(segment_id=segment_id, keyword=kw, source=source, seen_on=day)
            .on_conflict_do_nothing(index_elements=["segment_id", "keyword", "source", "seen_on"])
        )
        await self._s.execute(stmt)
    await self._s.flush()
    return len(keywords)


async def days_seen(self, segment_id: int, source: str, days: int, today: date) -> dict[str, int]:
    # `today` vem do clock do app (determinismo em teste), não de datetime.now
    since = today - timedelta(days=days - 1)
    stmt = (
        select(KeywordSignalDayRow.keyword, func.count(func.distinct(KeywordSignalDayRow.seen_on)))
        .where(
            KeywordSignalDayRow.segment_id == segment_id,
            KeywordSignalDayRow.source == source,
            KeywordSignalDayRow.seen_on >= since,
        )
        .group_by(KeywordSignalDayRow.keyword)
    )
    return {str(k): int(n) for k, n in (await self._s.execute(stmt)).all()}
```

Declare as duas portas em `app/ports.py` (`SegmentTermRepository` com os 4 métodos; os 2 métodos novos em `KeywordSignalRepository`).

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/infra/test_repositories.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ports.py src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(db): repositórios do pool de termos e dos dias do Suggest (R4/R2)"
```

---

### Task 8: Filtrar + ranquear + gravar o pool no `research_pipeline` (R1/R2/R4)

**Files:**
- Modify: `src/content_factory/app/research_pipeline.py`
- Modify: `src/content_factory/app/ports.py` (`NoOpSegmentTermRepository`)
- Modify: `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_research_pipeline.py` (editar), `tests/stubs.py` (fakes)

**Interfaces:**
- Consumes: `filter_terms`, `rank_suggestions`, `SuggestHit`, `TermFilterConfig`, `SegmentTerm`, `KeywordSignalRepository.record_days/days_seen`, `SegmentTermRepository`.
- Produces:
  - `ResearchDeps.terms: SegmentTermRepository = field(default_factory=NoOpSegmentTermRepository)`.
  - Counts **planos** (o tipo é `dict[str, int | bool]` — um `dict` aninhado não passa no pyright strict): `terms_active`, `terms_excluded`, `filter_discards_blocked`, `filter_discards_lang`, `filter_discards_no_anchor`.

- [ ] **Step 1: Testes que falham** (fakes em `tests/stubs.py`: `FakeSegmentTerms` com `.saved`, `FakeKeywordSignals` com `.days` — sem `...`, fakes completos e tipados)

```python
async def test_research_filtra_ranqueia_e_grava_o_pool() -> None:
    deps = research_deps(  # helper existente do arquivo, estendido com terms/signals fakes
        suggest_hits=[
            SuggestHit("como fazer vela aromática", "velas", 1),  # aceita
            SuggestHit("candle making for beginners", "velas", 2),  # lang
            SuggestHit("presente original", "velas", 3),  # no_anchor
        ],
        segment=_segment(anchor_terms=["vela"], blocked_terms=["grafico"]),
    )
    out = await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    assert out.counts["filter_discards_lang"] == 1
    assert out.counts["filter_discards_no_anchor"] == 1
    by_key = {(t.term, t.origin): t for t in deps.terms.saved}
    aceita = by_key[("como fazer vela aromática", "suggest")]
    assert aceita.status == "active" and aceita.kw_rank is not None and aceita.kw_rank != 0.5
    excluida = by_key[("presente original", "suggest")]
    assert excluida.status == "excluded" and excluida.exclude_reason == "no_anchor"
    assert all(t.status == "active" for t in deps.terms.saved if t.origin == "seed")


async def test_research_grava_o_dia_antes_de_ler_a_persistencia() -> None:
    deps = research_deps(suggest_hits=[SuggestHit("vela de soja", "velas", 1)])
    await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    # o termo de hoje conta 1 dia na persistência (não 0, nem "default 1" sem gravar)
    assert deps.signals.days[("vela de soja", deps.clock().date())] == 1


async def test_research_so_manda_ao_trends_e_as_redes_termos_aceitos() -> None:
    deps = research_deps(suggest_hits=[SuggestHit("candle for beginners", "velas", 1)])
    await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    assert "candle for beginners" not in deps.trends.requested_keywords
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/app/test_research_pipeline.py -q` → FAIL.

- [ ] **Step 3: Implementar** — ordem dentro de `run_research_cycle`, logo após `web_raw`:

```python
today = deps.clock().date()
cfg = TermFilterConfig(anchors=tuple(segment.anchor_terms), blocked=tuple(segment.blocked_terms))

# 1) persistência: grava HOJE antes de ler (senão o dia corrente nunca conta)
distinct_terms = list(dict.fromkeys(h.term for h in web_raw))
await deps.signals.record_days(segment.id, distinct_terms, "web_suggest", today)
days_seen = await deps.signals.days_seen(segment.id, "web_suggest", 7, today)
weights = rank_suggestions(web_raw, days_seen)

# 2) filtro R1 ANTES de truncar, do Trends e das redes
accepted, rejected = filter_terms(list(weights), cfg)
web_signals = deps.guard.truncate(
    [KeywordSignal(keyword=t, source="web_suggest", weight=weights[t]) for t in accepted],
    "web",
)  # agora o truncate (por peso) tem sentido: antes todo peso era 0,5
counts["web_found"] = len(web_signals)

terms = [
    SegmentTerm(
        segment_id=segment.id,
        term=s.keyword,
        origin="suggest",
        kw_rank=s.weight,
        rules_version=f"{TERM_FILTER_VERSION}+{KW_RANK_VERSION}",
    )
    for s in web_signals
]
terms += [
    SegmentTerm(
        segment_id=segment.id,
        term=term,
        origin="suggest",
        kw_rank=weights[term],
        status="excluded",
        exclude_reason=reason,
        rules_version=TERM_FILTER_VERSION,
    )
    for term, reason in rejected
]
terms += [  # seeds nunca são excluídas (R4)
    SegmentTerm(segment_id=segment.id, term=k, origin="seed", rules_version=TERM_FILTER_VERSION)
    for k in segment.keywords
]
await deps.terms.upsert_many(segment.id, terms)
counts["terms_active"] = len(web_signals) + len(segment.keywords)
counts["terms_excluded"] = len(rejected)
for reason in ("blocked", "lang", "no_anchor"):
    counts[f"filter_discards_{reason}"] = sum(1 for _, r in rejected if r == reason)
```

Ajustes obrigatórios decorrentes:
- O bloco do Trends **continua** com os 5 de maior peso (o Bloco 4 troca por lotes com âncora), mas agora sobre `web_signals` **aceitos e ranqueados**.
- `keyword_signals` grava só os aceitos, com `weight = kw_rank` (o `top_for_segment` do q1 web continua funcionando e passa a ver pesos distintos).
- `keywords_for_social` = seeds + aceitos (como hoje, só que filtrados). O Bloco 2 decide o que a fase social do research faz no teste.
- `NoOpSegmentTermRepository` em `ports.py` (padrão do `NoOpWebHitRepository`), e `terms=SegmentTermRepositoryImpl(session)` no `ResearchDeps(...)` de `composition.py`.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/app/test_research_pipeline.py tests/app/test_research_pipeline_e2e.py -q` → PASS (todos, inclusive os antigos).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/research_pipeline.py src/content_factory/app/ports.py \
        src/content_factory/infra/queue/composition.py tests/app/test_research_pipeline.py tests/stubs.py
git commit -m "feat(research): filtra, ranqueia e grava o pool de termos (R1/R2/R4)"
```

---

### Task 9: Encerrar o bloco — gates e verificação de aceite

- [ ] **Step 1: Gates completos**

Run: `make gates`
Expected: `pytest` verde, `ruff` limpo, `pyright` 0 erros, `alembic check` sem drift, `bandit`/`pip-audit` ok, `mkdocs build --strict` ok, `privacidade ok`.

- [ ] **Step 2: Aceite de R2 no banco (QA, primeiro research depois do deploy)**

```sql
-- deixa de haver peso único em web_suggest
SELECT count(DISTINCT weight) AS pesos_distintos, min(weight), max(weight)
FROM keyword_signals WHERE source = 'web_suggest';
-- esperado: pesos_distintos > 1
```

- [ ] **Step 3: Aceite de R1 (amostra real do dia seguinte)**

**Varredura completa** (revisão QA do PR #50 — a amostra de 13 não pegou marcas e outros sentidos): listar TODOS os termos `suggest` ativos por segmento e procurar marcas, outros sentidos (dental, piso, impressão 3D, ignição) e EN/ES; cada achado vira bloqueio no YAML + caso literal em `tests/app/test_segment.py`. Além disso, checar no pool: ≤1 em 13 termos fora do nicho/idioma por segmento (amostra cega de 13 termos ativos `suggest` por segmento, sorteio com seed fixa); e o inverso — descartes `lang`/`no_anchor` revisados à mão: PT do nicho descartado é falso negativo e também conta. Se falhar, **reportar** com os termos (não afrouxar nem apertar o filtro sem nova versão).

- [ ] **Step 4: Aceite de R4**

```sql
SELECT origin, status, count(*) FROM segment_terms GROUP BY 1, 2 ORDER BY 1, 2;
-- esperado: seeds ativas + termos suggest ativos + excluídos COM exclude_reason não nulo
SELECT count(*) FROM segment_terms WHERE status = 'excluded' AND exclude_reason IS NULL;  -- 0
SELECT count(*) FROM segment_terms WHERE origin = 'seed' AND status <> 'active';         -- 0
```

- [ ] **Step 5: Commit de encerramento (se sobrou doc)**

```bash
git commit -am "docs(plans): bloco 1 concluído — R1/R2/R4 verificados" || true
```

---

## Auto-revisão da spec (R1/R2/R4)

- **R1** ✓ `term-filter-v1` puro (idioma só por evidência positiva EN/ES, âncora palavra inteira, bloqueio por segmento, modo hashtag por substring, motivo registrado) — Tasks 1, 4, 8; casos reais do QA de 16/10 como teste literal. *Integração com o `q1` fica com a branch web* (decisão: dois filtros convivem); o filtro é reutilizável e exportado por `domain/term_filter.py`.
- **R2** ✓ posição exposta (Task 5), ranking puro (Task 2), persistência entre dias via `keyword_signal_days` (Tasks 6-7), peso no `keyword_signals` (Task 8).
- **R4** ✓ `segment_terms` com chave `(segment_id, term, origin)` (Task 6), repo com ordem por origem (Task 7), seeds nunca excluídas e excluídos sempre com motivo (Task 8).
- Fora deste bloco: R3 (Trends aplicado, Bloco 4) preenche `trend_score`; R5/R6 (Bloco 2/4) consomem o pool.
