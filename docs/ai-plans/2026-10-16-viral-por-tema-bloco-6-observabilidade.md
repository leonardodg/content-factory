# Bloco 6 — Observabilidade e idioma (R13+R14)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar o teste auditável. O `cf ops-report` passa a mostrar:
- cota e rendimento por origem (buscas, buscas vazias, vídeos por busca);
- vídeos por origem de termo;
- cobertura da remedição;
- o pool com os descartes do filtro.

O idioma fica verificado ponta a ponta. Faz parte da **Fase B**, implementada durante o teste. O pré-registro foi para o fim da Fase A (Bloco 4, Task 8): ele precisa estar commitado **antes** do primeiro dado do teste.

**Architecture:** Tudo é leitura (`OpsReportReader`) agregada por uma função pura (`build_ops_report`). A CLI só emite o envelope e os dados ficam no banco. O R14 é quase todo verificação, mais um indicador de cobertura de idioma no relatório. A cota e o rendimento vêm de `search_queries` (Bloco 2), não de `video_discoveries`: busca que volta vazia não gera descoberta, mas gasta 100 unidades e é o dado de rendimento da origem (revisão QA).

**Tech Stack:** Python 3.14, SQLAlchemy 2 async, typer, Markdown/MkDocs, pytest, ruff, pyright strict.

## Global Constraints

- `domain/` puro; `app/` → portas; `infra/` implementa portas; `cli/worker → app → domain`.
- `pyright` strict 0 erros; `ruff` linha 100/aspas duplas; `ruff format` limpo; TDD.
- Envelope só com contagens; **dados no banco**; sem segredo nem caminho absoluto de home rastreado (`scripts/check_privacy.py`).
- `make gates` antes de "pronto": `pytest` + `ruff` + `pyright` + `alembic check` + `bandit` + `pip-audit` + `mkdocs build --strict` + `privacy`.
- Sem migração neste bloco (só leitura + doc).

## Referência

Especificação §4 R13 e R14; §6 (checkpoints). **Depende dos Blocos 1–4** (pool, atribuição, `search_queries`, remedição). Não depende do Bloco 5: pode ser feito em paralelo a ele.

## Decisões deste bloco

- Nada de comando novo: `cf ops-report` ganha chaves. `build_ops_report` mantém-se **puro** — as agregações novas chegam como parâmetros opcionais (default vazio), preservando as chamadas existentes.
- Cota e rendimento por origem saem de `search_queries` (buscas, buscas vazias, cota, resultados, únicos novos). A cota da remedição sai das runs `remediate:*` de `collect_runs`.
- Cobertura da remedição = `video_metrics` novos na janela sobre os alvos (descobertos nos últimos `REMEDIATION_WINDOW_DAYS`) + contagem de `videos.removed_at`.
- O pré-registro **não** está neste bloco (está no Bloco 4, Task 8).

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/app/ports.py` (editar) | Métodos novos em `OpsReportReader`. |
| `src/content_factory/infra/db/repositories.py` (editar) | SQL das agregações novas. |
| `src/content_factory/app/ops_report.py` (editar) | Seções novas do relatório (função pura). |
| `src/content_factory/cli/main.py` (editar) | Passa as agregações ao `build_ops_report` (sem comando novo). |
| `docs/HANDOFF.md` (editar) | Estado do épico e próximos passos. |
| `tests/app/test_ops_report.py`, `tests/infra/...` | TDD. |

---

### Task 1: Agregações novas no leitor do `ops-report`

**Files:**
- Modify: `src/content_factory/app/ports.py`
- Modify: `src/content_factory/infra/db/repositories.py`
- Test: `tests/infra/test_repositories.py` (editar — integração)

**Interfaces:**
- Produces (em `OpsReportReader`):
  - `async def search_yield(self, segment_id: int, since: datetime) -> dict[str, dict[str, int]]` — chave `f"{platform}:{origin}"` → `{"searches", "empty", "failed", "quota", "results", "kept"}` (de `search_queries`)
  - `async def remediation_quota(self, segment_id: int, since: datetime) -> int` — soma de `quota_used` das runs `remediate:%`
  - `async def discoveries_by_origin(self, segment_id: int, since: datetime) -> dict[str, int]` — `term_origin` → nº de vídeos distintos
  - `async def remediation_coverage(self, segment_id: int, since: datetime) -> dict[str, int]` — `{"targets": n, "remeasured": n, "removed": n}`
  - `async def pool_by_origin(self, segment_id: int) -> dict[str, dict[str, int]]` — origem → `{"active": n, "excluded": n}`
  - `async def filter_discards(self, segment_id: int) -> dict[str, int]` — `exclude_reason` → n
  - `async def language_coverage(self, segment_id: int) -> dict[str, dict[str, int]]` — plataforma → `{"videos": n, "with_language": n}`

- [ ] **Step 1: Teste que falha** (integração, com dados sintéticos mínimos)
Semear 3 `search_queries` (uma vazia, uma com erro), 2 `video_discoveries` de origens distintas, 1 `segment_term` ativo + 1 excluído (`exclude_reason="lang"`), 1 vídeo YouTube com `language` e outro sem, e 1 `video_metrics` novo. Conferir cada agregação.

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`AttributeError`).

- [ ] **Step 3: Implementar** (SQL de agregação; padrão dos leitores existentes)

```python
async def search_yield(self, segment_id: int, since: datetime) -> dict[str, dict[str, int]]:
    q = SearchQueryRow
    stmt = (
        select(
            q.platform,
            q.origin,
            func.count(q.id),
            func.count(q.id).filter(q.results == 0, q.error_code.is_(None)),
            func.count(q.id).filter(q.error_code.is_not(None)),
            func.coalesce(func.sum(q.quota_used), 0),
            func.coalesce(func.sum(q.results), 0),
            func.coalesce(func.sum(q.kept), 0),
        )
        .where(q.segment_id == segment_id, q.started_at >= since)
        .group_by(q.platform, q.origin)
    )
    keys = ("searches", "empty", "failed", "quota", "results", "kept")
    return {
        f"{plat}:{origin}": dict(zip(keys, (int(v) for v in vals), strict=True))
        for plat, origin, *vals in (await self._s.execute(stmt)).all()
    }


async def remediation_coverage(self, segment_id: int, since: datetime) -> dict[str, int]:
    targets = (
        select(func.count(func.distinct(VideoSegmentRow.video_id)))
        .join(VideoRow, VideoRow.id == VideoSegmentRow.video_id)
        .where(
            VideoSegmentRow.segment_id == segment_id,
            VideoSegmentRow.discovered_at >= since,
            VideoRow.removed_at.is_(None),
        )
    )
    remeasured = (
        select(func.count(func.distinct(VideoMetricsRow.video_id)))
        .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoMetricsRow.video_id)
        .where(VideoSegmentRow.segment_id == segment_id, VideoMetricsRow.captured_at >= since)
    )
    removed = (
        select(func.count(VideoRow.id))
        .join(VideoSegmentRow, VideoSegmentRow.video_id == VideoRow.id)
        .where(VideoSegmentRow.segment_id == segment_id, VideoRow.removed_at.is_not(None))
    )
    return {
        "targets": int((await self._s.execute(targets)).scalar_one()),
        "remeasured": int((await self._s.execute(remeasured)).scalar_one()),
        "removed": int((await self._s.execute(removed)).scalar_one()),
    }
```

`pool_by_origin` (group by `origin`, `status`), `filter_discards` (group by `exclude_reason` onde `status='excluded'`), `discoveries_by_origin` (group by `term_origin`, `count(distinct video_id)`) e `language_coverage` (group by `platform`, `count(*)` e `count(language)`) seguem o mesmo molde.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ports.py src/content_factory/infra/db/repositories.py tests/infra/test_repositories.py
git commit -m "feat(ops): agregações de cota, origem, remedição, pool e idioma (R13/R14)"
```

---

### Task 2: Seções novas no `build_ops_report` (puro)

**Files:**
- Modify: `src/content_factory/app/ops_report.py`
- Test: `tests/app/test_ops_report.py` (editar)

**Interfaces:**
- Consumes: os dicts da Task 1.
- Produces: `build_ops_report(..., search_yield=None, remediation_quota=0, discoveries_by_origin=None, remediation=None, pool=None, filter_discards=None, language=None)` — novas chaves:
  - `"search_quota"`: `{"by_origin": {chave: {...contagens..., "videos_per_search": float | None}}, "searches_total": n, "quota_total": n (buscas + remedição), "remediation_quota": n}`
  - `"discovery_origins"`: `{origem: n}`
  - `"remediation"`: `{"targets": n, "remeasured": n, "removed": n, "coverage": float | None}`
  - `"term_pool"`: `{origem: {"active": n, "excluded": n}}`
  - `"filter_discards"`: `{motivo: n}`
  - `"language"`: `{plataforma: {"videos": n, "with_language": n, "rate": float | None}}`

- [ ] **Step 1: Testes que falham**

```python
# tests/app/test_ops_report.py  (adicionar)
def test_ops_report_traz_as_secoes_do_teste_viral() -> None:
    rep = build_ops_report(
        runs=[],
        coverage={},
        scored=0,
        since=_SINCE,
        until=_UNTIL,
        search_yield={
            "youtube:seed": {
                "searches": 4,
                "empty": 1,
                "failed": 0,
                "quota": 408,
                "results": 30,
                "kept": 28,
            },
            "youtube:suggest": {
                "searches": 8,
                "empty": 0,
                "failed": 1,
                "quota": 816,
                "results": 80,
                "kept": 60,
            },
        },
        remediation_quota=12,
        discoveries_by_origin={"seed": 10, "suggest": 25},
        remediation={"targets": 100, "remeasured": 95, "removed": 3},
        pool={"seed": {"active": 3, "excluded": 0}, "suggest": {"active": 20, "excluded": 5}},
        filter_discards={"lang": 4, "no_anchor": 1},
        language={"youtube": {"videos": 50, "with_language": 40}},
    )
    assert rep["search_quota"]["searches_total"] == 12
    assert rep["search_quota"]["quota_total"] == 408 + 816 + 12
    assert rep["search_quota"]["by_origin"]["youtube:seed"]["videos_per_search"] == 7.0
    assert rep["discovery_origins"]["suggest"] == 25
    assert rep["remediation"]["coverage"] == 0.95
    assert rep["term_pool"]["suggest"]["excluded"] == 5
    assert rep["filter_discards"] == {"lang": 4, "no_anchor": 1}
    assert rep["language"]["youtube"]["rate"] == 0.8


def test_ops_report_sem_dados_novos_continua_valido() -> None:
    rep = build_ops_report(runs=[], coverage={}, scored=0, since=_SINCE, until=_UNTIL)
    assert rep["remediation"]["coverage"] is None
    assert rep["term_pool"] == {} and rep["filter_discards"] == {}
```

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar**

```python
    return {
        # ... chaves atuais (window, collectors, web_empty, web_discards, blocked_runs, ticks, coverage, scored)
        "search_quota": {
            "by_origin": {
                key: {**c, "videos_per_search": _ratio(c["kept"], c["searches"])}
                for key, c in (search_yield or {}).items()
            },
            "searches_total": sum(c["searches"] for c in (search_yield or {}).values()),
            "quota_total": sum(c["quota"] for c in (search_yield or {}).values()) + remediation_quota,
            "remediation_quota": remediation_quota,
        },
        "discovery_origins": dict(discoveries_by_origin or {}),
        "remediation": {
            "targets": (remediation or {}).get("targets", 0),
            "remeasured": (remediation or {}).get("remeasured", 0),
            "removed": (remediation or {}).get("removed", 0),
            "coverage": _ratio(
                (remediation or {}).get("remeasured", 0), (remediation or {}).get("targets", 0)
            ),
        },
        "term_pool": {k: dict(v) for k, v in (pool or {}).items()},
        "filter_discards": dict(filter_discards or {}),
        "language": {
            plat: {**counts, "rate": _ratio(counts["with_language"], counts["videos"])}
            for plat, counts in (language or {}).items()
        },
    }
```

- [ ] **Step 4: Rodar e ver passar** — `uv run pytest tests/app/test_ops_report.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/app/ops_report.py tests/app/test_ops_report.py
git commit -m "feat(ops): seções de cota, origem, remedição, pool, descartes e idioma (R13/R14)"
```

---

### Task 3: CLI `ops-report` alimenta as seções

**Files:**
- Modify: `src/content_factory/cli/main.py`
- Test: `tests/cli/test_ops_report.py` (editar)

**Interfaces:**
- Consumes: leitores da Task 1.
- Produces: `cf ops-report --segment <slug> [--since <iso>]` devolve o relatório com as chaves novas; nada além do envelope no stdout.

- [ ] **Step 1: Teste que falha** — com repositório fake, checar que o resultado tem `search_quota`, `term_pool`, `remediation` e que a saída passa por `_emit`.

- [ ] **Step 2: Rodar e ver falhar** — FAIL.

- [ ] **Step 3: Implementar** — no handler existente, buscar as agregações (usando `since`/`until` já resolvidos e `REMEDIATION_WINDOW_DAYS` para a janela da remedição) e passá-las a `build_ops_report(...)`. Sem try/except novo: erro de leitura propaga como hoje.

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/cli/main.py tests/cli/test_ops_report.py
git commit -m "feat(cli): ops-report com cota por origem, pool, remedição e idioma (R13)"
```

---

### Task 4: R14 — idioma verificado ponta a ponta

**Files:**
- Test: `tests/infra/collectors/test_youtube.py`, `tests/infra/test_repositories.py` (editar)

**Interfaces:**
- Produces: garantias testadas (sem código novo, salvo lacuna encontrada).

- [ ] **Step 1: Testes que falham (ou confirmam)**

```python
def test_language_do_youtube_e_persistido_e_filtrado() -> None:
    # _map grava language; _language_matches descarta prefixo estrangeiro
    from content_factory.infra.collectors.youtube import _language_matches

    assert _language_matches("pt-BR", "pt") is True
    assert _language_matches("en", "pt") is False
    assert _language_matches(None, "pt") is True  # sem evidência, mantém
    assert _language_matches("pt-PT", "pt-BR") is False  # público BR (17/10)
    assert _language_matches("pt", "pt-BR") is True  # sem região: sem evidência
```

```python
async def test_upsert_grava_language_do_candidato() -> None:
    cand = CandidateVideo(
        platform="youtube", platform_video_id="v1", url="u", creator_handle="c", language="pt-BR"
    )
    await repo.upsert_candidates(seg_id, "youtube", [cand])
    # SELECT language FROM videos WHERE platform_video_id='v1' → 'pt-BR'
```

- [ ] **Step 2: Rodar e ver falhar** — se já passarem, o Step 1 vira teste de **regressão** (commitar mesmo assim); se falharem, há lacuna a corrigir.

- [ ] **Step 3: Corrigir a lacuna, se houver** — o único ponto esperado: `CandidateVideo.language` de Instagram/TikTok quando a plataforma informar (Instagram Graph devolve `language`? se não houver, manter `None` — "onde a plataforma informa"). R1 no título/descrição do `viral-v2` já é o Bloco 5 (Task 6).

- [ ] **Step 4: Rodar e ver passar** — PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/infra/collectors/test_youtube.py tests/infra/test_repositories.py
git commit -m "test(lang): videos.language persistido e filtro pt do YouTube (R14)"
```

---

### Task 5: Encerrar o épico — gates e checklist de aceite

- [ ] **Step 1: Gates completos** — `make gates` verde (pytest, ruff, pyright, `alembic check`, bandit, pip-audit, `mkdocs build --strict`, privacy).

- [ ] **Step 2: Aceite R13 (QA)**

```sql
-- cota e rendimento por plataforma/origem (inclui buscas vazias e com erro)
SELECT platform, origin, count(*), count(*) FILTER (WHERE results = 0), sum(quota_used), sum(kept)
FROM search_queries WHERE started_at > now() - interval '1 day' GROUP BY 1, 2 ORDER BY 1, 2;
-- cota total do dia (buscas + remedição) contra o teto de 8.000
SELECT sum(quota_used) FROM collect_runs WHERE started_at > now() - interval '1 day';
-- vídeos por origem de termo
SELECT term_origin, count(DISTINCT video_id) FROM video_discoveries
WHERE discovered_at > now() - interval '1 day' GROUP BY 1;
-- pool e descartes
SELECT origin, status, count(*) FROM segment_terms GROUP BY 1, 2 ORDER BY 1, 2;
SELECT exclude_reason, count(*) FROM segment_terms WHERE status = 'excluded' GROUP BY 1;
-- cobertura de idioma
SELECT platform, count(*), count(language) FROM videos GROUP BY 1;
```

E `cf ops-report --segment <slug>` devolvendo `search_quota`, `discovery_origins`, `remediation`, `term_pool`, `filter_discards`, `language`.

- [ ] **Step 3: Aceite R14** — `videos.language` preenchido onde a plataforma informa (YouTube); filtro pt do YouTube (`_language_matches`) mantido; R1 aplicado aos títulos no `viral-v2`.

- [ ] **Step 4: Deploy (Fase B)** — se este bloco não foi junto com o Bloco 5: imagem nova (sem migração) numa janela sem cron e com a fila vazia, e depois a fumaça do `cf ops-report` nos 3 segmentos.

- [ ] **Step 5: Commit de encerramento do épico**

```bash
git commit -am "docs(plans): bloco 6 e épico viral-por-tema concluídos — R13/R14 verificados" || true
```

---

## Auto-revisão da spec (R13/R14)

- **R13** ✓ `ops-report` mostra cota por plataforma/origem/coleta (Tasks 1-3), vídeos por origem de termo, cobertura da remedição, tamanho do pool por origem e descartes do filtro por motivo (Tasks 1-3).
- **R14** ✓ `videos.language` verificado (Tasks 1/4), filtro pt do YouTube mantido (Task 4), R1 no título/descrição do `viral-v2` (Bloco 5, Task 6).
- **§6** ✓ checkpoints D+1..D+4 usam este relatório; o pré-registro está no Bloco 4 (Task 8).

---

## Como usar estes planos (épico completo — revisão QA)

**Pré-requisito:** a F1 web (`db3ad2e`, `4f56a0a`, `9e855a6`) mergeada na `main` e esta branch rebaseada (o Bloco 1 usa `probe_terms`).

| Fase | Blocos | Deploy | Efeito no teste |
|---|---|---|---|
| **A — coleta** | 1 → 2 → 3 → 4 (o 3 é independente do 2 e pode ir em paralelo) | 1 deploy ao fim do Bloco 4, depois do **pré-registro** (Bloco 4, Task 8). Migrações `0011`–`0014`. | **Abre o relógio dos 4 dias.** Pool, vagas por origem, atribuição, log de buscas, remedição e coleta a cada 6 h. |
| **B — análise** | 5 e 6 (em paralelo) | 1 deploy durante o teste. Migração `0015`. | `viral-v2`, `cf term-report` com `--as-of`, top vídeos, `ops-report` novo. Ativa a vaga de hashtag (exploratória, com a data anotada em adendo ao pré-registro). Tudo é recalculável no corte a partir dos dados da Fase A. |

- Cada bloco: TDD, `make gates`, revisão e teste real do QA antes de seguir.
- Deploy sempre numa janela sem cron e com a fila vazia, com backup do banco antes.
- O corte final (D+4) é `cf term-report --as-of <início + 96 h>` nos 3 segmentos.


## Resultado (2026-10-17)

- Tasks 1–3 feitas com TDD (RED visto em todas). Task 4 (R14) já coberta por
  testes existentes: gravação de `videos.language` sem apagar o conhecido,
  mapeamento do YouTube, filtro pt-BR (`accepted_language`) e remedição por
  idioma — não duplicados.
- Desvio: `remediation_coverage` conta como remedidos só os **alvos** medidos
  na janela (o plano contava qualquer vídeo medido; a cobertura podia passar
  de 100%).
- Teste real (banco descartável, sabonetes, Suggest/Trends/YouTube reais):
  4 buscas (408 unidades); `videos_per_search` seed 2,0 / suggest 11,0 /
  trends 7,0; 31 alvos com cobertura 1,0; pool 141 ativos / 77 excluídos
  (60 `lang`, 17 `blocked`); idioma declarado em 31/31.
