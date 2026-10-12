# Fase C — Instagram orientado a keyword do pipeline

**Goal:** `InstagramCollector` passa a implementar também `SocialDiscoverer`
(Fase A), reaproveitando `_own_media` (já robustecido na Fase 0 — filtro
`media_type`, simetria de `#`, paginação) para filtrar por keywords/hashtags
vindas do pipeline de pesquisa, não só `segment.keywords` estáticas.

**Pré-requisito:** Fase A mergeada (`SocialDiscoverer` Protocol existe).

### Task C1: `InstagramCollector.discover_by_keywords`

**Files:**
- Modify: `src/content_factory/infra/collectors/instagram.py`
- Test: `tests/infra/collectors/test_instagram.py`

**Interfaces:**
- Consumes: `SocialDiscoverer` Protocol (Fase A, `app/ports.py`).
- Produces: `InstagramCollector` passa a satisfazer estruturalmente os
  dois Protocols (`Collector` E `SocialDiscoverer`) — sem herança, Python
  estrutural já resolve, só precisa dos dois métodos presentes na classe.

- [ ] **Step 1: Escrever o teste falhando**

```python
@pytest.mark.asyncio
async def test_discover_by_keywords_uses_union_of_keywords_and_hashtags() -> None:
    """discover_by_keywords filtra _own_media pela união keywords+hashtags, não só segment.keywords."""
    c = InstagramCollector(
        access_token="t", ig_user_id="42", transport=httpx.MockTransport(_gate_route)
    )
    # _OWN_MEDIA tem "18001" (Vela Artesanal) e "18002" (Sabonete artesanal)
    out = await c.discover_by_keywords(keywords=["sabonete"], hashtags=["#vela"], depth=1)
    ids = {v.platform_video_id for v in out}
    assert ids == {"18001", "18002"}  # ambos batem: "vela" (hashtag sem #) + "sabonete" (keyword)
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `uv run pytest tests/infra/collectors/test_instagram.py::test_discover_by_keywords_uses_union_of_keywords_and_hashtags -v`
Expected: FAIL — `AttributeError: 'InstagramCollector' object has no attribute 'discover_by_keywords'`.

- [ ] **Step 3: Implementar**

Extrair a lógica de paginação+filtro de `_own_media` (Fase 0, Task 4) para
um helper privado parametrizado por lista de termos, e reescrever
`_own_media`/criar `discover_by_keywords` em cima dele:

```python
    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        """Stage 3-5 do pipeline (Fase A): filtra mídia própria pela união
        keywords+hashtags vinda do research_pipeline, não só segment.keywords."""
        terms = [k.casefold().removeprefix("#") for k in [*keywords, *hashtags]]
        return await self._own_media_by_terms(terms, limit=25)
```

Refatorar `_own_media(self, segment, limit)` (Fase 0) para delegar no
mesmo helper:

```python
async def _own_media(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
    keywords = [k.casefold().removeprefix("#") for k in segment.keywords]
    return await self._own_media_by_terms(keywords, limit)


async def _own_media_by_terms(self, terms: list[str], limit: int) -> list[CandidateVideo]:
    logger.warning("instagram: gate #10 (App Review pendente) — usando mídia própria")
    self.gate_hits += 1
    out: list[CandidateVideo] = []
    after: str | None = None
    for page in range(_MAX_OWN_MEDIA_PAGES):
        params = {"fields": FIELDS, "limit": "50"}
        if after:
            params["after"] = after
        rows = await self._get(f"{self._user}/media", params)
        if rows is None:
            if not out and page == 0:
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
            if not terms or any(t in caption for t in terms):
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

(ler o estado real do arquivo após a Fase 0 antes de editar — o corpo
exato de `_own_media` pode ter pequenas diferenças da Task 4 original;
preservar `gate_hits`/filtro/paginação já existentes, só generalizar o
parâmetro de filtro de "keywords do segmento" pra "lista de termos".)

- [ ] **Step 4: Rodar e confirmar sucesso**

Run: `uv run pytest tests/infra/collectors/test_instagram.py -v`
Expected: PASS em todos (os testes antigos de `_own_media`/`discover`
continuam valendo via `discover` → `_own_media` → `_own_media_by_terms`).

- [ ] **Step 5: `pyright strict`**

Run: `uv run pyright src/content_factory/infra/collectors/instagram.py`
Expected: 0 erros.

- [ ] **Step 6: Commit**

```bash
git add src/content_factory/infra/collectors/instagram.py tests/infra/collectors/test_instagram.py
git commit -m "feat(instagram): discover_by_keywords reusa _own_media com termos do pipeline"
```

### Task C2: Gates + docs

- [ ] Gates completos (pytest, ruff, pyright, bandit, pip-audit, mkdocs
  --strict, check_privacy.py).
- [ ] Atualizar `docs/HANDOFF.md` (Fase C concluída).
- [ ] Copiar plano para `docs/ai-plans/<data>-pipeline-pesquisa-fase-c.md`.
- [ ] Commit final.

> **Nota**: plano de intenção detalhada (não SDD travado como a Fase A) — leia os arquivos reais citados antes de executar e ajuste os detalhes finos; a arquitetura (Protocols, nomes, pontos de integração) é o que está fixado aqui.
