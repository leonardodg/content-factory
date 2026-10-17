# Bloco 6 — Observabilidade, idioma e pré-registro (R13+R14+§6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar o teste auditável e congelar o pré-registro antes de abrir as campanhas. O `cf ops-report` passa a mostrar cota por origem/coleta, vídeos por origem de termo, cobertura da remedição e o pool com os descartes do filtro; o idioma fica verificado ponta a ponta; e o pré-registro é commitado em `docs/manual/`.

**Architecture:** Tudo é leitura (`OpsReportReader`) agregada por uma função pura (`build_ops_report`) — a CLI só emite o envelope, os dados ficam no banco. R14 é majoritariamente verificação (com um indicador de cobertura de idioma no relatório). O pré-registro é um documento versionado que trava versões, pesos e a divisão de buscas por origem.

**Tech Stack:** Python 3.14, SQLAlchemy 2 async, typer, Markdown/MkDocs, pytest, ruff, pyright strict.

## Global Constraints

- `domain/` puro; `app/` → portas; `infra/` implementa portas; `cli/worker → app → domain`.
- `pyright` strict 0 erros; `ruff` linha 100/aspas duplas; `ruff format` limpo; TDD.
- Envelope só com contagens; **dados no banco**; sem segredo nem caminho absoluto de home rastreado (`scripts/check_privacy.py`).
- `make gates` antes de "pronto": `pytest` + `ruff` + `pyright` + `alembic check` + `bandit` + `pip-audit` + `mkdocs build --strict` + `privacy`.
- Sem migração neste bloco (só leitura + doc).

## Referência

Especificação §4 R13 e R14; §6 (pré-registro, execução, checkpoints). **Depende dos Blocos 1–5** (pool, atribuição, remedição, `viral-v2`, relatório).

## Decisões deste bloco

- Nada de comando novo: `cf ops-report` ganha chaves. `build_ops_report` mantém-se **puro** — as agregações novas chegam como parâmetros opcionais (default vazio), preservando as chamadas existentes.
- Cota por origem sai de `video_discoveries` (agrupando `query_id` distintos por `platform`/`term_origin`) + `collect_runs.quota_used` por plataforma.
- Cobertura da remedição = `video_metrics` novos na janela sobre os alvos (descobertos nos últimos `REMEDIATION_WINDOW_DAYS`) + contagem de `videos.removed_at`.
- **Pré-registro** congela: pergunta, versões (`term-filter-v1`, `kw-rank-v1`, `trends-anchor-v1`, `viral-v2`, `term-rank-v1`, `hashtag-mine-v1`), pesos, janela 30 d, **divisão fixa de buscas por origem** (R5), datas de corte e a regra de decisão por origem.
- Publicação no MkDocs: se `mkdocs build --strict` acusar arquivo fora do `nav`, acrescentar a página ao `nav` do `mkdocs.yml` (mesma âncora do manual) — não baixar o nível de validação.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/content_factory/app/ports.py` (editar) | Métodos novos em `OpsReportReader`. |
| `src/content_factory/infra/db/repositories.py` (editar) | SQL das agregações novas. |
| `src/content_factory/app/ops_report.py` (editar) | Seções novas do relatório (função pura). |
| `src/content_factory/cli/main.py` (editar) | Passa as agregações ao `build_ops_report` (sem comando novo). |
| `docs/manual/pre-registro-viral-por-tema.md` (novo) | Pré-registro commitado (§6). |
| `mkdocs.yml` (editar) | Entrada no `nav` (se necessário). |
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
  - `async def quota_by_origin(self, segment_id: int, since: datetime) -> dict[str, int]` — chave `f"{platform}:{origin}"` → nº de buscas distintas (`query_id`)
  - `async def discoveries_by_origin(self, segment_id: int, since: datetime) -> dict[str, int]` — `term_origin` → nº de vídeos distintos
  - `async def remediation_coverage(self, segment_id: int, since: datetime) -> dict[str, int]` — `{"targets": n, "remeasured": n, "removed": n}`
  - `async def pool_by_origin(self, segment_id: int) -> dict[str, dict[str, int]]` — origem → `{"active": n, "excluded": n}`
  - `async def filter_discards(self, segment_id: int) -> dict[str, int]` — `exclude_reason` → n
  - `async def language_coverage(self, segment_id: int) -> dict[str, dict[str, int]]` — plataforma → `{"videos": n, "with_language": n}`

- [ ] **Step 1: Teste que falha** (integração, com dados sintéticos mínimos)
Semear 1 coleta com 2 `video_discoveries` de origens distintas, 1 `segment_term` ativo + 1 excluído (`exclude_reason="lang"`), 1 vídeo YouTube com `language` e outro sem, e 1 `video_metrics` novo. Conferir cada agregação.

- [ ] **Step 2: Rodar e ver falhar** — FAIL (`AttributeError`).

- [ ] **Step 3: Implementar** (SQL de agregação; padrão dos leitores existentes)

```python
async def quota_by_origin(self, segment_id: int, since: datetime) -> dict[str, int]:
    stmt = (
        select(
            VideoDiscoveryRow.platform,
            VideoDiscoveryRow.term_origin,
            func.count(func.distinct(VideoDiscoveryRow.query_id)),
        )
        .where(VideoDiscoveryRow.segment_id == segment_id, VideoDiscoveryRow.discovered_at >= since)
        .group_by(VideoDiscoveryRow.platform, VideoDiscoveryRow.term_origin)
    )
    return {f"{plat}:{origin}": int(n) for plat, origin, n in (await self._s.execute(stmt)).all()}


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
- Produces: `build_ops_report(..., quota_by_origin=None, discoveries_by_origin=None, remediation=None, pool=None, filter_discards=None, language=None)` — novas chaves:
  - `"search_quota"`: `{"by_origin": {...}, "total": n, "per_collect": {...}}`
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
        quota_by_origin={"youtube:seed": 4, "youtube:suggest_trends": 8},
        discoveries_by_origin={"seed": 10, "suggest_trends": 25},
        remediation={"targets": 100, "remeasured": 95, "removed": 3},
        pool={"seed": {"active": 3, "excluded": 0}, "suggest": {"active": 20, "excluded": 5}},
        filter_discards={"lang": 4, "no_anchor": 1},
        language={"youtube": {"videos": 50, "with_language": 40}},
    )
    assert rep["search_quota"]["total"] == 12
    assert rep["discovery_origins"]["suggest_trends"] == 25
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
            "by_origin": dict(quota_by_origin or {}),
            "total": sum((quota_by_origin or {}).values()),
            "per_collect": (remediation or {}).get("per_collect", {}),
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

### Task 5: Pré-registro em `docs/manual/` (§6)

**Files:**
- Create: `docs/manual/pre-registro-viral-por-tema.md`
- Modify: `mkdocs.yml` (só se `--strict` reclamar), `docs/HANDOFF.md`

**Interfaces:**
- Produces: documento versionado que trava o desenho antes de qualquer dado do teste novo.

- [ ] **Step 1: Escrever o pré-registro** com, no mínimo:

```markdown
# Pré-registro — descoberta de conteúdo viral por tema (teste de 4 dias)

Data do pré-registro: <preencher no commit>. Especificação: `docs/ai-plans/2026-10-16-especificacao-viral-por-tema.md`.

## Pergunta
Os termos vindos da web (Suggest, Trends e hashtags) trazem conteúdo com mais
engajamento do que as keywords fixas do segmento? Quais temas lideram em cada
segmento?

## Versões congeladas
`term-filter-v1`, `kw-rank-v1` (posição 0,5 / recorrência 0,3 / persistência 0,2;
cap de recorrência 3; janela 7 d), `trends-anchor-v1` (0,7 interesse + 0,3
inclinação), `viral-v2` (velocidade 0,35 / engajamento 0,25 / relativo ao canal
0,25 / alcance 0,15; janela 30 d; n mínimo por plataforma e segmento = 5),
`term-rank-v1` (top10 0,4 / mediana velocidade 0,3 / mediana engajamento 0,3;
bootstrap seed 20261016, 1.000 reamostragens), `hashtag-mine-v1` (top 25%).

## Divisão fixa de buscas por origem (R5)
YouTube: 3 buscas/coleta/segmento — 1 `seed`, 1 `suggest`/`trends`, 1
`hashtag:youtube` (vaga vai para `suggest`/`trends` enquanto o pool de hashtags
estiver vazio). Sem realocação adaptativa durante o teste.

## Regra de decisão por origem
A origem X é **melhor** que `seed` em um segmento se a mediana do `viral-v2`
dos vídeos trazidos por X for maior, com o **limite inferior do IC 95% da
diferença acima de zero**; IC cruzando zero ⇒ **direcional**; menos de **30
vídeos atribuídos** ⇒ **insuficiente**.

## Critério de "top temas"
`engagement_score` do termo com `n >= 5`; termo com `n < 5` sai como
"insuficiente".

## Datas de corte e checkpoints
D+0 fumaça (catch-up, 1ª coleta com atribuição, cota); D+1 (filtro por amostra
manual de 20 termos/segmento, remedição, 1º `cf term-report`); D+2 (velocity não
nula, hashtags no pool); D+4 (encerramento, backup, relatório final).

## Envs do teste
`COLLECT_CRON_HOURS=02,08,14,20`, `SCORE_CRON_HOURS=02,08,14,20`,
`TRENDS_TERMS_PER_DAY=16`, `SEARCHES_PER_COLLECT_YOUTUBE=3`,
`REMEDIATION_WINDOW_DAYS=7`, `REMEDIATION_LIMIT=500`, `VIRAL_V2_WINDOW_DAYS=30`,
`VIRAL_V2_MIN_POPULATION=5`, `COLLECT_LIMIT=25`.
```

- [ ] **Step 2: Publicar** — `uv run --only-group docs mkdocs build --strict`; se acusar arquivo fora do `nav`, acrescentar `- manual/pre-registro-viral-por-tema.md` ao bloco `Manual` do `mkdocs.yml` e repetir.

- [ ] **Step 3: Atualizar `docs/HANDOFF.md`** — estado do épico (blocos 1–6), envs, checklist de deploy (fila vazia, migrações, `cf segment load`, abertura das campanhas de 4 dias).

- [ ] **Step 4: Commit**

```bash
git add docs/manual/pre-registro-viral-por-tema.md mkdocs.yml docs/HANDOFF.md
git commit -m "docs(preregistro): congela versões, pesos e divisão de buscas do teste de 4 dias (§6)"
```

---

### Task 6: Encerrar o épico — gates e checklist de aceite

- [ ] **Step 1: Gates completos** — `make gates` verde (pytest, ruff, pyright, `alembic check`, bandit, pip-audit, `mkdocs build --strict`, privacy).

- [ ] **Step 2: Aceite R13 (QA)**

```sql
-- cota por plataforma/origem/coleta
SELECT platform, term_origin, count(DISTINCT query_id) FROM video_discoveries
WHERE discovered_at > now() - interval '1 day' GROUP BY 1, 2 ORDER BY 1, 2;
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

- [ ] **Step 4: Checklist do deploy (aplicar §6, janela sem cron e fila vazia)**

1. Encerrar as campanhas 856–858 (o teste antigo foi congelado em 16/10 15:40 BRT; backup em `~/content-factory-backups/2026-10-16-teste7d-codigo-antigo/`).
2. Aplicar as migrações `0011`–`0015`.
3. `cf segment load` dos 3 segmentos (sobe `probe_terms`, `anchor_terms`, `blocked_terms`).
4. Definir as envs do pré-registro e `COLLECT_LIMIT=25`.
5. Abrir as campanhas de **4 dias** dos 3 segmentos.

- [ ] **Step 5: Commit de encerramento do épico**

```bash
git commit -am "docs(plans): bloco 6 e épico viral-por-tema concluídos — R13/R14 verificados" || true
```

---

## Auto-revisão da spec (R13/R14)

- **R13** ✓ `ops-report` mostra cota por plataforma/origem/coleta (Tasks 1-3), vídeos por origem de termo, cobertura da remedição, tamanho do pool por origem e descartes do filtro por motivo (Tasks 1-3).
- **R14** ✓ `videos.language` verificado (Tasks 1/4), filtro pt do YouTube mantido (Task 4), R1 no título/descrição do `viral-v2` (Bloco 5, Task 6).
- **§6** ✓ pré-registro commitado em `docs/manual/` com versões, pesos, divisão de buscas, regra de decisão e checkpoints (Task 5); deploy único no fim com fila vazia e migrações (Task 6).

---

## Como usar estes planos (épico completo)

- Ordem de execução: **Bloco 1 → 2 → 3 → 4 → 5 → 6** (§7 da especificação). Os blocos 3, 5 e 6 dependem dos anteriores; 3 e 4 são independentes entre si.
- Cada bloco: implementar por TDD, rodar `make gates`, revisar e fazer o teste real do QA antes de seguir.
- **Deploy único no fim** (Bloco 6), numa janela sem cron e com a fila vazia.
- Migrações na ordem `0011` → `0012` → `0013` → `0014` → `0015`.
