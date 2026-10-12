# Fix do collector Instagram (Fase 0) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Ao iniciar a execução, salvar uma cópia deste plano em
> `docs/ai-plans/2026-10-12-fix-instagram-collector-fase0.md` (convenção do
> projeto, `CLAUDE.md`: "Plano: docs/ai-plans/ antes de qualquer código").

**Goal:** Corrigir os 3 deferrals de código conhecidos do collector Instagram
(`media_type`, simetria de `#`, paginação do fallback) e expor telemetria do
gate #10, com playtest real antes/depois, deixando o Instagram no melhor
estado possível *dentro do que é código* — a aprovação do App Review em si é
ação externa do usuário, fora deste plano (ver `## Fora de escopo`).

**Architecture:** Nenhuma mudança de Protocol/arquitetura — tudo dentro de
`InstagramCollector` (`infra/collectors/instagram.py`) + uma correção
pontual de simetria em `app/score.py` (`relevance_of`) + uma leitura
opcional e não-intrusiva de um contador em `app/collect.py`.

**Tech Stack:** Python 3.14, httpx (`httpx.MockTransport` nos testes),
pytest + pytest-asyncio, pyright strict.

## Global Constraints

- TDD sempre: todo passo de código começa pelo teste falhando (CLAUDE.md).
- `pyright strict` 0 erros — nunca rebaixar config.
- `domain/` puro (sem I/O) — `app/score.py` e `infra/.../instagram.py` são as
  únicas camadas tocadas aqui, nenhuma mudança entra em `domain/`.
- CLI devolve só envelope `{"ok": ...}` — qualquer contador novo entra dentro
  do envelope existente, nunca como print solto.
- Gates antes de "pronto": pytest, ruff, pyright, bandit, pip-audit,
  mkdocs --strict (sem mudança de schema nesta fase, `alembic check` não se
  aplica).
- Reaproveitar fixtures existentes em `tests/fixtures/ig_hashtag_top.json`,
  `tests/fixtures/ig_hashtag_search.json` e o padrão de
  `httpx.MockTransport` já usado em `tests/infra/collectors/test_instagram.py`.

---

### Task 1: Baseline — playtest real antes de qualquer mudança

**Files:**
- Nenhum arquivo de código; evidência vai em
  `docs/PLANO_TESTE_E2E.md` (apêndice) e `docs/HANDOFF.md`.

**Interfaces:** nenhuma (tarefa operacional).

- [ ] **Step 1: Rodar o collect real do Instagram**

```bash
docker exec cf-python uv run cf collect --segment candles --platform instagram --limit 5
```

Expected: exit `0` ou `2` (partial), envelope com `"ok"` e contagem
`found > 0` via fallback `_own_media` (gate #10 segue ativo — ver achado da
investigação no plano-mãe).

- [ ] **Step 2: Registrar a run no banco para comparação pós-fix**

```bash
docker exec cf-python uv run psql "$DATABASE_URL" -c \
  "SELECT id, collector, status, found, inserted, error_code FROM collect_runs \
   WHERE collector='instagram' ORDER BY id DESC LIMIT 1;"
```

Guardar o `id` da run como baseline (vai ser comparado no Task 6).

- [ ] **Step 3: Commit da evidência**

```bash
git add docs/PLANO_TESTE_E2E.md docs/HANDOFF.md
git commit -m "docs: baseline do playtest Instagram antes do fix Fase 0"
```

---

### Task 2: Filtro de `media_type` (só vídeo/carrossel)

**Files:**
- Modify: `src/content_factory/infra/collectors/instagram.py:73-114`
  (`discover` e `_own_media`)
- Test: `tests/infra/collectors/test_instagram.py`

**Interfaces:**
- Consumes: `CandidateVideo` (`domain/models.py:22`, sem mudança de campo).
- Produces: `discover`/`_own_media` passam a descartar itens com
  `media_type == "IMAGE"` antes de montar `CandidateVideo` — nenhuma
  assinatura pública muda.

- [ ] **Step 1: Escrever o teste falhando — hashtag path ignora post de imagem**

```python
@pytest.mark.asyncio
async def test_discover_skips_image_media_type() -> None:
    """Reels analyzer: post IMAGE (sem vídeo) não deve virar CandidateVideo."""

    def _route(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/ig_hashtag_search"):
            return httpx.Response(200, json={"data": [{"id": "1784"}]})
        if path.endswith("/top_media"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "900",
                            "caption": "vela artesanal foto",
                            "media_type": "IMAGE",
                            "permalink": "https://www.instagram.com/p/IMG/",
                            "username": "velaslab",
                        },
                        {
                            "id": "901",
                            "caption": "vela artesanal video",
                            "media_type": "VIDEO",
                            "permalink": "https://www.instagram.com/p/VID/",
                            "username": "velaslab",
                        },
                    ]
                },
            )
        return httpx.Response(404, json={})

    c = InstagramCollector(access_token="t", ig_user_id="42", transport=httpx.MockTransport(_route))
    out = await c.discover(SEG, limit=5)
    assert [v.platform_video_id for v in out] == ["901"]
```

- [ ] **Step 2: Rodar o teste e confirmar falha**

Run: `pytest tests/infra/collectors/test_instagram.py::test_discover_skips_image_media_type -v`
Expected: FAIL — `out` contém também `"900"` (IMAGE não filtrado hoje).

- [ ] **Step 3: Implementar o filtro**

Em `src/content_factory/infra/collectors/instagram.py`, adicionar helper e
usar nos dois pontos (`discover` e `_own_media`):

```python
_SKIP_MEDIA_TYPES = {"IMAGE"}


def _is_video_like(item: dict[str, Any]) -> bool:
    return str(item.get("media_type", "")).upper() not in _SKIP_MEDIA_TYPES
```

Em `discover` (substituir a linha `return [self._map(cast(...)) for item in items[:limit]]`):

```python
        items: Any = top.get("data") or []
        videos = [cast(dict[str, Any], item) for item in items if _is_video_like(item)]
        return [self._map(item) for item in videos[:limit]]
```

Em `_own_media`, dentro do loop `for raw_item in items:`, logo após
`item = cast(cast(dict[str, Any], raw_item))`:

```python
            if not _is_video_like(item):
                continue
            caption = str(item.get("caption") or "").casefold()
```

(remove a linha antiga `caption = ...` duplicada, mantém só esta.)

- [ ] **Step 4: Rodar o teste e confirmar sucesso**

Run: `pytest tests/infra/collectors/test_instagram.py -v`
Expected: PASS em todos os testes do arquivo (os existentes continuam
verdes porque a fixture `ig_hashtag_top.json` já usa `media_type: "VIDEO"`).

- [ ] **Step 5: `pyright strict` no arquivo**

Run: `pyright src/content_factory/infra/collectors/instagram.py`
Expected: 0 erros.

- [ ] **Step 6: Commit**

```bash
git add src/content_factory/infra/collectors/instagram.py tests/infra/collectors/test_instagram.py
git commit -m "fix(instagram): filtra media_type IMAGE (Reels analyzer so quer video/carrossel)"
```

---

### Task 3: Simetria de `#` no matching de keyword

**Files:**
- Modify: `src/content_factory/infra/collectors/instagram.py:104` (dentro
  de `_own_media`)
- Modify: `src/content_factory/app/score.py:31-35` (`relevance_of`)
- Test: `tests/infra/collectors/test_instagram.py`,
  `tests/app/test_score.py` (criar se não existir arquivo de teste de
  `relevance_of` — conferir antes com
  `grep -rl "relevance_of" tests/` já que pode já existir em
  `tests/app/test_score.py` ou `tests/domain/test_scoring.py`)

**Interfaces:**
- Consumes: `SegmentConfig.keywords: list[str]`, `VideoSnapshot` (sem
  mudança de schema).
- Produces: nenhuma assinatura muda — só a normalização interna da
  comparação.

- [ ] **Step 1: Escrever o teste falhando — keyword com `#` deve casar legenda sem `#`**

Em `tests/infra/collectors/test_instagram.py`:

```python
@pytest.mark.asyncio
async def test_own_media_fallback_keyword_with_hash_matches_caption_without_hash() -> None:
    """Keyword cadastrada como '#vela' deve casar legenda 'Vela artesanal' (sem #)."""
    seg = SegmentConfig(slug="candles", name="Velas", platforms=["instagram"], keywords=["#vela"])
    c = InstagramCollector(
        access_token="t", ig_user_id="42", transport=httpx.MockTransport(_gate_route)
    )
    out = await c.discover(seg, limit=5)
    assert [v.platform_video_id for v in out] == ["18001"]
```

(reaproveita `_gate_route`/`_OWN_MEDIA` já definidos no arquivo — a legenda
do item `"18001"` é `"Passo a passo de Vela Artesanal com cera de soja ✨"`,
sem `#`.)

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest tests/infra/collectors/test_instagram.py::test_own_media_fallback_keyword_with_hash_matches_caption_without_hash -v`
Expected: FAIL — `out` vem vazio (`"#vela"` não é substring de `"vela artesanal..."`.casefold()`).

- [ ] **Step 3: Corrigir `_own_media`**

Em `src/content_factory/infra/collectors/instagram.py`, trocar:

```python
        keywords = [k.casefold() for k in segment.keywords]
```

por:

```python
        keywords = [k.casefold().removeprefix("#") for k in segment.keywords]
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest tests/infra/collectors/test_instagram.py -v`
Expected: PASS.

- [ ] **Step 5: Mesmo problema em `app/score.py` — teste falhando**

Primeiro confirmar onde ficam os testes de `relevance_of`:

```bash
grep -rln "relevance_of" tests/
```

Adicionar no arquivo encontrado (ou criar `tests/app/test_score.py` se não
existir nenhum):

```python
def test_relevance_of_keyword_with_hash_matches_text_without_hash() -> None:
    snap = VideoSnapshot(
        video_id=1,
        title="",
        description="Vela artesanal de soja",
        metrics=VideoMetrics(),
        published_at=None,
    )
    assert relevance_of(snap, ["#vela"]) == 1.0
```

(ajustar os campos obrigatórios de `VideoSnapshot` conforme o modelo real em
`domain/models.py:51` antes de rodar — ler o modelo se os nomes de campo
acima não baterem.)

- [ ] **Step 6: Rodar e confirmar falha**

Run: `pytest <arquivo_encontrado>::test_relevance_of_keyword_with_hash_matches_text_without_hash -v`
Expected: FAIL (`hits == 0`).

- [ ] **Step 7: Corrigir `relevance_of`**

Em `src/content_factory/app/score.py:31-35`, trocar:

```python
def relevance_of(snap: VideoSnapshot, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    text = f"{snap.title} {snap.description}".lower()
    hits = sum(1 for k in keywords if k.lower() in text)
    return hits / len(keywords)
```

por:

```python
def relevance_of(snap: VideoSnapshot, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    text = f"{snap.title} {snap.description}".lower()
    hits = sum(1 for k in keywords if k.lower().removeprefix("#") in text)
    return hits / len(keywords)
```

- [ ] **Step 8: Rodar toda a suíte afetada**

Run: `pytest tests/infra/collectors/test_instagram.py <arquivo_de_score> -v`
Expected: PASS em tudo.

- [ ] **Step 9: `pyright strict`**

Run: `pyright src/content_factory/infra/collectors/instagram.py src/content_factory/app/score.py`
Expected: 0 erros.

- [ ] **Step 10: Commit**

```bash
git add src/content_factory/infra/collectors/instagram.py src/content_factory/app/score.py tests/
git commit -m "fix(instagram,score): remove assimetria de # no matching de keyword"
```

---

### Task 4: Paginação do fallback `_own_media`

**Files:**
- Modify: `src/content_factory/infra/collectors/instagram.py:91-114`
- Test: `tests/infra/collectors/test_instagram.py`

**Interfaces:**
- Consumes: resposta do Graph `GET /{user}/media` já inclui `paging.cursors.after`
  quando há mais páginas (formato padrão da Graph API — conferir no probe
  real durante o Task 1 se o payload bate; se o campo vier em
  `paging.next` em vez de `paging.cursors.after`, ajustar a extração abaixo
  para `paging.next` com `httpx.URL(...).params["after"]`).
- Produces: `_own_media` passa a buscar até `_MAX_OWN_MEDIA_PAGES` páginas
  (constante nova) em vez de só uma.

- [ ] **Step 1: Escrever o teste falhando — match só na segunda página**

```python
@pytest.mark.asyncio
async def test_own_media_fallback_paginates_until_match() -> None:
    """Sem match na página 1 (tem cursor 'after'), busca a página 2 e acha."""
    calls = {"n": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/ig_hashtag_search"):
            return httpx.Response(
                400,
                json={"error": {"message": "(#10) ... public content access ...", "code": 10}},
            )
        if path.endswith("/media"):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": "1",
                                "caption": "sabonete",
                                "media_type": "VIDEO",
                                "permalink": "https://x/1",
                                "username": "u",
                            }
                        ],
                        "paging": {"cursors": {"after": "CURSOR2"}},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "2",
                            "caption": "vela artesanal",
                            "media_type": "VIDEO",
                            "permalink": "https://x/2",
                            "username": "u",
                        }
                    ],
                    "paging": {},
                },
            )
        return httpx.Response(404, json={})

    c = InstagramCollector(access_token="t", ig_user_id="42", transport=httpx.MockTransport(_route))
    out = await c.discover(SEG, limit=5)
    assert [v.platform_video_id for v in out] == ["2"]
    assert calls["n"] == 2
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest tests/infra/collectors/test_instagram.py::test_own_media_fallback_paginates_until_match -v`
Expected: FAIL (`calls["n"] == 1`, `out` vazio — hoje só busca 1 página).

- [ ] **Step 3: Implementar paginação**

Em `src/content_factory/infra/collectors/instagram.py`, adicionar constante
no topo do módulo (perto de `FIELDS`):

```python
_MAX_OWN_MEDIA_PAGES = 3
```

Reescrever `_own_media`:

```python
    async def _own_media(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        """Fallback no gate #10: mídia pública da própria conta, filtrada por keyword.

        A conta publica conteúdo misto (velas, sabonetes, macramê); sem filtro a
        relevância do segmento seria violada. Busca vazia de hashtag não cai aqui.
        Pagina até _MAX_OWN_MEDIA_PAGES para achar `limit` matches.
        """
        logger.warning("instagram: gate #10 (App Review pendente) — usando mídia própria")
        keywords = [k.casefold().removeprefix("#") for k in segment.keywords]
        out: list[CandidateVideo] = []
        after: str | None = None
        for page in range(_MAX_OWN_MEDIA_PAGES):
            params = {"fields": FIELDS, "limit": "50"}
            if after:
                params["after"] = after
            rows = await self._get(f"{self._user}/media", params)
            if rows is None:
                if not out and page == 0:
                    # gate também na mídia própria: sem hashtag e sem fallback — falha ruidosa
                    raise AuthExpired(self.platform, "HTTP 400 gate #10 na mídia própria")
                break
            items: Any = rows.get("data") or []
            if not items:
                break
            for raw_item in items:
                item = cast(dict[str, Any], raw_item)
                if not _is_video_like(item):
                    continue
                caption = str(item.get("caption") or "").casefold()
                if not keywords or any(k in caption for k in keywords):
                    out.append(self._map(item))
                if len(out) >= limit:
                    return out
            paging = cast(dict[str, Any], rows.get("paging") or {})
            cursors = cast(dict[str, Any], paging.get("cursors") or {})
            after = cast(str | None, cursors.get("after"))
            if not after:
                break
        return out
```

(remove a implementação antiga de `_own_media` inteira, inclusive a
checagem solta `if not rows: return []` que fica redundante com o loop.)

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest tests/infra/collectors/test_instagram.py -v`
Expected: PASS em todos — incluindo
`test_gate_on_own_media_raises_not_silent_empty` (continua levantando
`AuthExpired` porque `page == 0` e `out` vazio).

- [ ] **Step 5: `pyright strict`**

Run: `pyright src/content_factory/infra/collectors/instagram.py`
Expected: 0 erros.

- [ ] **Step 6: Commit**

```bash
git add src/content_factory/infra/collectors/instagram.py tests/infra/collectors/test_instagram.py
git commit -m "fix(instagram): pagina o fallback own-media ate _MAX_OWN_MEDIA_PAGES"
```

---

### Task 5: Telemetria do gate #10 (contador exposto no envelope)

**Files:**
- Modify: `src/content_factory/infra/collectors/instagram.py` (atributo
  novo na instância)
- Modify: `src/content_factory/app/collect.py:60-72` (leitura opcional,
  sem mudar o Protocol `Collector`)
- Test: `tests/infra/collectors/test_instagram.py`, `tests/app/test_collect.py`

**Interfaces:**
- Consumes: nenhuma mudança de Protocol — leitura via
  `getattr(collector, "gate_hits", 0)`, duck-typed, não quebra collectors
  que não têm o atributo (youtube/tiktok/web).
- Produces: `CollectOutcome.counts["instagram_gate_hits"]` (chave dinâmica
  por plataforma, só aparece quando `> 0`).

- [ ] **Step 1: Escrever o teste falhando no collector**

```python
@pytest.mark.asyncio
async def test_gate_increments_gate_hits_counter() -> None:
    c = InstagramCollector(
        access_token="t", ig_user_id="42", transport=httpx.MockTransport(_gate_route)
    )
    assert c.gate_hits == 0
    await c.discover(SEG, limit=5)
    assert c.gate_hits == 1
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `pytest tests/infra/collectors/test_instagram.py::test_gate_increments_gate_hits_counter -v`
Expected: FAIL — `AttributeError: 'InstagramCollector' object has no attribute 'gate_hits'`.

- [ ] **Step 3: Adicionar o contador**

Em `__init__` (linha 28, ao lado de `self.app_usage = 0`):

```python
        self.gate_hits = 0
```

No início de `_own_media` (logo após o docstring, antes do `logger.warning`):

```python
        self.gate_hits += 1
```

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `pytest tests/infra/collectors/test_instagram.py -v`
Expected: PASS.

- [ ] **Step 5: Escrever o teste falhando em `collect.py`**

Localizar o teste existente do use-case `collect` (`grep -rln "async def collect" tests/`
para achar `tests/app/test_collect.py` ou equivalente) e adicionar, usando o
padrão de fake collector já existente nesse arquivo:

```python
class _FakeGatedCollector:
    platform = "instagram"

    def __init__(self) -> None:
        self.gate_hits = 2

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        return []


@pytest.mark.asyncio
async def test_collect_exposes_gate_hits_counter() -> None:
    deps = ...  # reaproveitar o builder de CollectDeps já usado nos outros testes do arquivo,
    # trocando a lista de collectors por [_FakeGatedCollector()]
    out = await collect("candles", ["instagram"], limit=5, deps=deps)
    assert out.counts["instagram_gate_hits"] == 2
```

(adaptar à fixture/helper real de `CollectDeps` já presente no arquivo —
ler os primeiros ~40 blocos do arquivo de teste antes de escrever este
passo, para reaproveitar o builder exato em vez de recriar fakes de
`VideoRepository`/`SegmentRepository`/etc.)

- [ ] **Step 6: Rodar e confirmar falha**

Run: `pytest <arquivo_de_teste_collect>::test_collect_exposes_gate_hits_counter -v`
Expected: FAIL — `KeyError: 'instagram_gate_hits'`.

- [ ] **Step 7: Implementar em `app/collect.py`**

Em `src/content_factory/app/collect.py`, dentro do `try` do loop principal
(linha ~60-72), logo após a linha
`counts["skipped"] = int(counts["skipped"]) + stats.skipped`:

```python
                gate_hits = getattr(collector, "gate_hits", 0)
                if gate_hits:
                    counts[f"{collector.platform}_gate_hits"] = gate_hits
```

- [ ] **Step 8: Rodar e confirmar sucesso**

Run: `pytest <arquivo_de_teste_collect>::test_collect_exposes_gate_hits_counter -v`
Expected: PASS.

- [ ] **Step 9: Rodar a suíte completa afetada + pyright**

Run: `pytest tests/infra/collectors/test_instagram.py tests/app/test_collect.py -v`
Run: `pyright src/content_factory/infra/collectors/instagram.py src/content_factory/app/collect.py`
Expected: tudo verde, 0 erros.

- [ ] **Step 10: Commit**

```bash
git add src/content_factory/infra/collectors/instagram.py src/content_factory/app/collect.py tests/
git commit -m "feat(instagram): expoe contador gate_hits no envelope do collect"
```

---

### Task 6: Playtest final, gates completos e handoff

**Files:**
- Modify: `docs/HANDOFF.md`, `docs/PLANO_TESTE_E2E.md`
- Create: `docs/ai-plans/2026-10-12-fix-instagram-collector-fase0.md`
  (cópia final deste plano, conforme CLAUDE.md)

**Interfaces:** nenhuma (fechamento da fase).

- [ ] **Step 1: Rebuild do worker/CLI container (código mudou)**

```bash
docker compose -f .devcontainer/docker-compose.yml up -d --build --force-recreate cf-python worker
```

- [ ] **Step 2: Playtest real pós-fix**

```bash
docker exec cf-python uv run cf collect --segment candles --platform instagram --limit 5
```

Expected: envelope `"ok"`, `found > 0`, e agora `instagram_gate_hits > 0`
visível no JSON (Task 5) enquanto o gate #10 seguir ativo.

- [ ] **Step 3: Comparar com a baseline do Task 1**

Confirmar no Postgres que a nova run tem `status`/`found` pelo menos iguais
à baseline, e que não surgiu nenhum `error_code` novo:

```bash
docker exec cf-python uv run psql "$DATABASE_URL" -c \
  "SELECT id, collector, status, found, error_code FROM collect_runs \
   WHERE collector='instagram' ORDER BY id DESC LIMIT 3;"
```

- [ ] **Step 4: Gates completos**

```bash
docker exec cf-python uv run pytest
docker exec cf-python uv run ruff check . && uv run ruff format --check .
docker exec cf-python uv run pyright
docker exec cf-python uv run bandit -r src/
docker exec cf-python uv run pip-audit
docker exec cf-python uv run mkdocs build --strict
```

Expected: todos os 7 comandos saem com exit `0`.

- [ ] **Step 5: Atualizar `docs/HANDOFF.md`**

Mover a linha do backlog "deferrals do review do collector Instagram
(media_type, simetria de `#`, paginação do fallback)" de
"⛔ O que falta" para "✅ CONCLUÍDO", citando os commits das Tasks 2-5 e o
achado da investigação de App Review (caminho exato de submissão, ver
plano-mãe desta sessão) como pendência externa do usuário.

- [ ] **Step 6: Copiar este plano para `docs/ai-plans/`**

```bash
cp /home/leodg/.claude/plans/vamos-atualizar-este-projeto-virtual-kernighan.md \
   docs/ai-plans/2026-10-12-fix-instagram-collector-fase0.md
```

- [ ] **Step 7: Commit final**

```bash
git add docs/HANDOFF.md docs/PLANO_TESTE_E2E.md docs/ai-plans/2026-10-12-fix-instagram-collector-fase0.md
git commit -m "docs: fecha Fase 0 (fix Instagram) com evidencia de playtest e gates"
```

---

## Fora de escopo (achado da investigação ao vivo no Meta for Developers)

Confirmado nesta sessão, navegando no app real **Automation-IvanaLereaBR**
(id `4160317280965017`, Business `IvanaLerea`): a feature **"Instagram
Public Content Access"** (necessária para `ig_hashtag_search`/`top_media` de
contas de terceiros) aparece com status **"Adicionar à análise do app"** —
ou seja, **nunca foi submetida** para App Review (não é "aguardando
aprovação", é "ainda não solicitada"). A página "Ações necessárias" do app
está vazia — não há bloqueio ativo da Meta, a conta está em dia.

Isso **não é implementável em código** — é ação do usuário:
developers.facebook.com → app **Automation-IvanaLereaBR** → Casos de uso →
API do Instagram → Permissões e recursos → feature **"Instagram Public
Content Access"** → botão **"Adicionar à análise do app"**. Provavelmente
vai pedir verificação de negócio da Business `IvanaLerea` e um screencast
demonstrando `ig_hashtag_search`/`top_media` em uso (as Tasks 1 e 6 deste
plano, rodadas com a câmera ligada, servem de screencast). Quando aprovado,
o caminho de hashtag volta a ser usado automaticamente — nenhuma mudança de
código (comportamento já garantido pelo fallback no gate).

---

## Roadmap pós-Fase 0 (não implementar agora — planos separados depois)

Resumo do pipeline de pesquisa multi-plataforma desenhado nesta sessão,
mantido como contexto para o próximo plano (não é SDD task-by-task ainda —
será detalhado quando a Fase 0 estiver fechada):

- **Fase A** — fundação do pipeline: novos Protocols `KeywordExpander`/
  `TrendsProvider`/`SocialDiscoverer`/`KeywordSignalRepository` em
  `app/ports.py`; `domain/models.py` ganha `KeywordSignal`/`TrendPoint`/
  `DepthConfig`; `domain/fanout_guard.py` (guard-rail anti-explosão:
  dedupe global, cap de nós, cap de fan-out por etapa); novo use-case
  `app/research_pipeline.py::run_research_cycle`; nova tabela
  `keyword_signals` (Alembic) + `KeywordSignalRepositoryImpl`. Tudo testado
  com fakes, zero chamada de rede real.
- **Fase B** — Google Trends real via **pytrends** (decisão do usuário —
  não-oficial/scraping, mesmo risco já aceito para `web.py`/ddgs), com
  circuit breaker por falhas consecutivas.
- **Fase C** — `InstagramCollector.discover_by_keywords` (novo Protocol
  `SocialDiscoverer`) reaproveitando `_own_media` (já robustecido pela
  Fase 0) com keywords/hashtags vindas do pipeline, não só
  `segment.keywords` estáticas.
- **Fase D** — orquestração no worker arq: novo job `research_job`,
  tabela `research_campaigns` (estado de campanha — "repetir diariamente
  por N semanas/meses" não existe em cron puro do arq, precisa de estado
  persistido consultado no início do job), CLI `cf research start/stop/status`.
- **Fase E** — YouTube/TikTok ganham `discover_by_keywords`, testes de
  ponta a ponta do guard-rail com todos os stages reais encadeados.
