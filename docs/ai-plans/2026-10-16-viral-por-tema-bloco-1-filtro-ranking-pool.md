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
- Consumes: `content_factory.domain.text.{contains_phrase, normalize, word_tokens}`.
- Produces:
  - `TERM_FILTER_VERSION: str = "term-filter-v1"`
  - `@dataclass(frozen=True) class TermFilterConfig: anchors: tuple[str, ...] = (); blocked: tuple[str, ...] = ()`
  - `@dataclass(frozen=True) class TermVerdict: accepted: bool; reason: str | None`
  - `def filter_term(term: str, config: TermFilterConfig) -> TermVerdict`
  - `def filter_terms(terms: Sequence[str], config: TermFilterConfig) -> tuple[list[str], dict[str, int]]` — devolve `(aceitos, contagem_por_motivo)`.

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/domain/test_term_filter.py
from content_factory.domain.term_filter import (
    TERM_FILTER_VERSION,
    TermFilterConfig,
    filter_term,
    filter_terms,
)

VELAS = TermFilterConfig(
    anchors=("vela", "velas", "pavio", "parafina", "cera"),
    blocked=("tendencia alcista", "bajista", "grafico", "ignicao", "motor"),
)


def test_version() -> None:
    assert TERM_FILTER_VERSION == "term-filter-v1"


def test_aceita_frase_do_nicho_em_pt() -> None:
    assert filter_term("como fazer vela aromática", VELAS).accepted


def test_aceita_ancora_com_acento_normalizado() -> None:
    assert filter_term("velas artesanais passo a passo", VELAS).accepted


def test_rejeita_ingles_com_marcador() -> None:
    v = filter_term("candle making for beginners", VELAS)
    assert v.accepted is False
    assert v.reason == "lang"


def test_rejeita_espanhol_com_marcador() -> None:
    v = filter_term("vela aromatica con cera de soja", VELAS)
    assert v.reason == "lang"


def test_rejeita_frase_sem_palavra_pt() -> None:
    # nenhum marcador de língua, nenhum diacrítico, nenhuma palavra-função PT
    v = filter_term("wick trimmer scissors", VELAS)
    assert v.reason == "lang"


def test_rejeita_sem_ancora() -> None:
    v = filter_term("presente de aniversário original", VELAS)
    assert v.accepted is False
    assert v.reason == "no_anchor"


def test_rejeita_bloqueado_com_precedencia() -> None:
    v = filter_term("vela grafico tendencia alcista", VELAS)
    assert v.reason == "blocked"


def test_ancora_casa_palavra_inteira_nao_substring() -> None:
    # "envelope" NÃO contém "vela"; "revelar" NÃO contém "vela"
    assert filter_term("envelope revelar", VELAS).reason == "no_anchor"


def test_sem_ancoras_configuradas_nao_reprova_ancora() -> None:
    cfg = TermFilterConfig(anchors=(), blocked=())
    assert filter_term("qualquer coisa em portugues", cfg).accepted


def test_filter_terms_conta_motivos() -> None:
    aceitos, motivos = filter_terms(
        ["como fazer vela", "candle making for beginners", "presente original"],
        VELAS,
    )
    assert aceitos == ["como fazer vela"]
    assert motivos == {"lang": 1, "no_anchor": 1}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/domain/test_term_filter.py -q`
Expected: FAIL — `ModuleNotFoundError: content_factory.domain.term_filter`.

- [ ] **Step 3: Implementação mínima**

```python
# src/content_factory/domain/term_filter.py
"""Filtro de termos `term-filter-v1` (R1) — puro, sem I/O.

Ordem de precedência DOCUMENTADA do motivo (como o `INTENT_ORDER` do q1):
`blocked` → `lang` → `no_anchor`. Listas fechadas e versionadas; sem
heurística opaca.

O `filter-v1`/`intent-v3` da branch web NÃO é substituído por este: aquele
vive só no protocolo q1; este governa pool, hashtags e descoberta.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from content_factory.domain.text import contains_phrase, word_tokens

TERM_FILTER_VERSION = "term-filter-v1"
REASON_ORDER: tuple[str, ...] = ("blocked", "lang", "no_anchor")

# marcadores de OUTRO idioma (lista fechada). Casam como palavra/frase inteira.
NON_PT_MARKER_WORDS = frozenset(
    {"for", "the", "and", "with", "en", "con", "los", "las", "del", "of"}
)
NON_PT_MARKER_PHRASES: tuple[str, ...] = ("para la", "de la")

# evidência POSITIVA de português: diacríticos ou palavras-função PT.
_PT_DIACRITICS = "áàâãéêíóôõúüç"
PT_FUNCTION_WORDS = frozenset(
    {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "para",
        "como",
        "com",
        "que",
        "e",
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "na",
        "no",
        "nas",
        "nos",
        "em",
        "por",
        "se",
        "ao",
        "sobre",
        "sem",
        "mais",
        "sao",
        "essa",
        "esse",
    }
)


@dataclass(frozen=True)
class TermFilterConfig:
    anchors: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()


@dataclass(frozen=True)
class TermVerdict:
    accepted: bool
    reason: str | None  # None | "blocked" | "lang" | "no_anchor"


def _is_other_language(term: str, words: list[str]) -> bool:
    phrase = " ".join(words)
    if any(w in NON_PT_MARKER_WORDS for w in words):
        return True
    if any(contains_phrase(words, p) for p in NON_PT_MARKER_PHRASES):
        return True
    if any(ch in term.casefold() for ch in _PT_DIACRITICS):
        return False
    return not any(w in PT_FUNCTION_WORDS for w in words)


def filter_term(term: str, config: TermFilterConfig) -> TermVerdict:
    words = word_tokens(term)
    checks: dict[str, bool] = {
        "blocked": any(contains_phrase(words, b) for b in config.blocked),
        "lang": _is_other_language(term, words),
        "no_anchor": bool(config.anchors)
        and not any(contains_phrase(words, a) for a in config.anchors),
    }
    reason = next((r for r in REASON_ORDER if checks[r]), None)
    return TermVerdict(accepted=reason is None, reason=reason)


def filter_terms(
    terms: Sequence[str], config: TermFilterConfig
) -> tuple[list[str], dict[str, int]]:
    accepted: list[str] = []
    reasons: dict[str, int] = {}
    for term in terms:
        verdict = filter_term(term, config)
        if verdict.accepted:
            accepted.append(term)
        else:
            assert verdict.reason is not None
            reasons[verdict.reason] = reasons.get(verdict.reason, 0) + 1
    return accepted, reasons
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/domain/test_term_filter.py -q`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain/term_filter.py tests/domain/test_term_filter.py
git commit -m "feat(domain): term-filter-v1 — idioma, âncora e bloqueio (R1)"
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
- **persistência**: nº de dias distintos (últimos 7) / 7.

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
blocked_terms: [tendencia alcista, bajista, grafico, ignicao, motor]
```
`segments/sabonetes-artesanais.yaml`:
```yaml
anchor_terms: [sabonete, sabonetes, saboaria, glicerinada, saponificacao]
blocked_terms: [natura, phebo, dove, lux, protex, granado]
```
`segments/resina-artesanal.yaml`:
```yaml
anchor_terms: [resina, epoxi, molde, pigmento, encapsulada]
blocked_terms: [odontologica, dente, clareia, dental, ortodontia]
```

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
- Modify: `src/content_factory/app/research_pipeline.py` (cpp do tipo; comportamento completo na Task 9)
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

- [ ] **Step 4: Rodar e ver passar** — o teste novo PASS. Em `research_pipeline.py`, ajuste mínimo para o novo tipo (o ciclo completo vem na Task 9): a fase web passa a receber `list[SuggestHit]`.

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
    """Pool de termos do segmento (R4). Único por (segment_id, term)."""

    __tablename__ = "segment_terms"
    __table_args__ = (
        UniqueConstraint("segment_id", "term", name="uq_segment_term"),
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
        sa.UniqueConstraint("segment_id", "term", name="uq_segment_term"),
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
  - `KeywordSignalRepository.days_seen(segment_id: int, source: str, days: int) -> dict[str, int]`
  - `SegmentTermRepository` (Protocol): `upsert_many(segment_id, terms: Sequence[SegmentTerm]) -> int`; `top_by_origin(segment_id, origin, limit) -> list[SegmentTerm]`; `counts_by_origin(segment_id) -> dict[str, int]`; `all_for_segment(segment_id, status: str | None = None) -> list[SegmentTerm]`

- [ ] **Step 1: Teste que falha** (repositório em memória já existe? não — teste o SQL)
Adicione um teste de integração que: cria segmento, faz `upsert_many` de 2 termos (1 ativo, 1 excluído com motivo), lê `counts_by_origin` e `all_for_segment`, e confere que `upsert_many` de novo atualiza `last_seen_at`/scores sem duplicar (`uq_segment_term`).

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
                index_elements=["segment_id", "term"],
                set_={
                    "origin": stmt.excluded.origin,
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

    async def top_by_origin(self, segment_id: int, origin: str, limit: int) -> list[SegmentTerm]:
        stmt = (
            select(SegmentTermRow)
            .where(
                SegmentTermRow.segment_id == segment_id,
                SegmentTermRow.origin == origin,
                SegmentTermRow.status == "active",
            )
            .order_by(
                SegmentTermRow.kw_rank.desc().nullslast(),
                SegmentTermRow.trend_score.desc().nullslast(),
                SegmentTermRow.term,
            )
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

Adicione o helper `_segment_term(row) -> SegmentTerm` e, em `KeywordSignalRepositoryImpl`:

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


async def days_seen(self, segment_id: int, source: str, days: int) -> dict[str, int]:
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
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
- Modify: `src/content_factory/infra/queue/composition.py`
- Test: `tests/app/test_research_pipeline.py` (editar)

**Interfaces:**
- Consumes: `filter_terms`, `rank_suggestions`, `SuggestHit`, `TermFilterConfig`, `SegmentTerm`, as portas `KeywordSignalRepository.record_days/days_seen` e `SegmentTermRepository`.
- Produces: `ResearchDeps.terms: SegmentTermRepository` (novo campo, com default no-op para chamadas existentes); o envelope do research ganha `"terms_active"`, `"terms_excluded"`, `"filter_discards"` (dict motivo→n).

- [ ] **Step 1: Teste que falha**

```python
# tests/app/test_research_pipeline.py  (adicionar, no estilo dos testes existentes)
async def test_research_filtra_ranqueia_e_grava_o_pool() -> None:
    deps = _research_deps_com_fakes(
        suggest_hits=[
            SuggestHit("como fazer vela aromática", "velas", 1),  # aceita
            SuggestHit("candle making for beginners", "velas", 2),  # lang
            SuggestHit("presente original", "velas", 3),  # no_anchor
        ],
        segment=Segment(
            id=1,
            slug="velas-artesanais",
            name="Velas",
            platforms=["youtube"],
            keywords=["vela artesanal"],
            anchor_terms=["vela"],
            blocked_terms=["grafico"],
        ),
    )
    out = await run_research_cycle("velas-artesanais", DepthConfig(), deps)
    assert out.counts["terms_active"] >= 1  # a seed + a frase aceita
    assert out.counts["filter_discards"] == {"lang": 1, "no_anchor": 1}
    # a sugestão aceita ficou com peso != 0,5 e origem "suggest"
    gravados = {t.term: t for t in deps.terms.saved}
    assert gravados["como fazer vela aromática"].origin == "suggest"
    assert gravados["como fazer vela aromática"].kw_rank != 0.5
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/app/test_research_pipeline.py -k pool -q` → FAIL.

- [ ] **Step 3: Implementar**

No `run_research_cycle`, substituir a construção de `trend_keywords`/`keywords_for_social` por (pseudocódigo preciso):

```python
# 1) ranking do Suggest (posição + recorrência + persistência)
days_seen = await deps.signals.days_seen(segment.id, "web_suggest", 7)
term_weights = rank_suggestions(web_raw, days_seen)
today = deps.clock().date()
await deps.signals.record_days(segment.id, list(term_weights), "web_suggest", today)

# 2) filtro R1
cfg = TermFilterConfig(anchors=tuple(segment.anchor_terms), blocked=tuple(segment.blocked_terms))
accepted, discards = filter_terms(list(term_weights), cfg)
terms = [
    SegmentTerm(
        segment_id=segment.id,
        term=t,
        origin="suggest",
        kw_rank=term_weights[t],
        rules_version=TERM_FILTER_VERSION,
    )
    for t in accepted
]
terms += [
    SegmentTerm(
        segment_id=segment.id,
        term=t,
        origin="suggest",
        status="excluded",
        exclude_reason=r,
        rules_version=TERM_FILTER_VERSION,
    )
    for t, r in _excluded(term_weights, cfg)
]
terms += [
    SegmentTerm(segment_id=segment.id, term=k, origin="seed", rules_version=TERM_FILTER_VERSION)
    for k in segment.keywords
]
await deps.terms.upsert_many(segment.id, terms)
counts["terms_active"] = len(accepted) + len(segment.keywords)
counts["filter_discards"] = discards
```

Ajustes obrigatórios decorrentes:
- `web_signals` de `all_signals` deixa de ser `KeywordSignal` para o Suggest: construa `KeywordSignal(weight=term_weights[t])` só para gravar `keyword_signals` (retrocompatível com `top_for_segment` do q1) — mantendo `trend_signals` como está.
- `keywords_for_social` passa a usar `segment.keywords` (seeds) **+ os termos aceitos do pool** (origem `suggest`), como hoje.
- `ResearchDeps` ganha `terms: SegmentTermRepository`; em `composition.py`, adicione `terms=SegmentTermRepositoryImpl(session)` ao `ResearchDeps(...)` e crie um `NoOpSegmentTermRepository` default em `ports.py` (como `NoOpWebHitRepository`) para testes/uso avulso.
- Helper puro local `_excluded(weights, cfg)` devolve `[(term, reason)]` para os rejeitados.

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/app/test_research_pipeline.py -q` → PASS (todos, inclusive os antigos).

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/research_pipeline.py src/content_factory/app/ports.py \
        src/content_factory/infra/queue/composition.py tests/app/test_research_pipeline.py
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

Checar no pool: ≤1 em 13 termos fora do nicho/idioma por segmento; nenhuma classe do q1 vazia por causa do filtro (se acontecer, **reportar**, não afrouxar o filtro).

- [ ] **Step 4: Aceite de R4**

```sql
SELECT origin, status, count(*) FROM segment_terms GROUP BY 1, 2 ORDER BY 1, 2;
-- esperado: seeds ativas + termos suggest ativos + excluídos COM exclude_reason não nulo
```

- [ ] **Step 5: Commit de encerramento (se sobrou doc)**

```bash
git commit -am "docs(plans): bloco 1 concluído — R1/R2/R4 verificados" || true
```

---

## Auto-revisão da spec (R1/R2/R4)

- **R1** ✓ `term-filter-v1` puro (idioma lista fechada, âncora palavra inteira, bloqueio por segmento, motivo registrado) — Tasks 1, 4, 8. *Integração com o `q1` fica com a branch web* (decisão: dois filtros convivem); o filtro é reutilizável e exportado por `domain/term_filter.py`.
- **R2** ✓ posição exposta (Task 5), ranking puro (Task 2), persistência entre dias via `keyword_signal_days` (Tasks 6-7), peso no `keyword_signals` (Task 8).
- **R4** ✓ `segment_terms` (Task 6), repo (Task 7), seeds nunca excluídas e excluídos sempre com motivo (Task 8).
- Fora deste bloco: R3 (Trends aplicado, Bloco 4) preenche `trend_score`; R5/R6 (Bloco 2/4) consomem o pool.
