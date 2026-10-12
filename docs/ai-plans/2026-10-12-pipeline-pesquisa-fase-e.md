# Fase E — YouTube/TikTok ganham `discover_by_keywords`

**Goal:** `YoutubeCollector` e `TikTokCollector` implementam
`SocialDiscoverer` (Fase A), reaproveitando as APIs de busca que já usam
(`search.list` do YouTube Data API, API privada do TikTok-Api) mas
parametrizadas por keyword/hashtag em vez de só `segment.keywords` fixas.

**Pré-requisito:** Fase A mergeada.

### Task E1: `YoutubeCollector.discover_by_keywords`

**Files:**
- Modify: `src/content_factory/infra/collectors/youtube.py`
- Test: `tests/infra/collectors/test_youtube.py`

Ler `youtube.py` atual primeiro — `discover(segment, limit)` já deve
montar uma query de busca a partir de `segment.keywords`
(`youtube.googleapis.com/youtube/v3/search?q=...`). `discover_by_keywords`
reaproveita a MESMA chamada HTTP, só trocando a fonte da query:

```python
    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        query = " ".join([*keywords, *hashtags][:5])  # API não aceita query enorme
        return await self._search(query, limit=25)  # extrair _search de discover() se ainda não existir
```

(decisão exata de limite de termos/ordenação de relevância fica pro
implementador, documentar a escolha no plano real da fase quando escrita;
o ponto de design fixo é: reusar o MESMO client HTTP/parsing de resposta
de `discover()`, não duplicar.)

- [x] Steps de TDD completos seguindo o padrão exato dos testes existentes
  de `test_youtube.py` (fixtures, `httpx.MockTransport` se usado, ou o
  mock de client real se for `google-api-python-client`).
- [x] Commit: `feat(youtube): discover_by_keywords reusa busca por query dinâmica`

### Task E2: `TikTokCollector.discover_by_keywords`

**Files:**
- Modify: `src/content_factory/infra/collectors/tiktok.py`
- Test: `tests/infra/collectors/test_tiktok.py`

Mesma lógica — ler `tiktok.py` atual (TikTok-Api, risco anti-bot já
documentado em `ADR-0003`/`docs/manual/anti-ban.md`) e reaproveitar o
client/sessão Playwright já configurado, só trocando o termo de busca.
**Atenção especial**: TikTok é a plataforma de maior risco de
banimento — qualquer busca nova por keyword precisa passar pelo MESMO
`RateLimiter`/jitter já em uso, nunca gerar rajada de requisições novas
sem throttle (reforçar isso no plano real, não é opcional).

- [x] Steps de TDD completos seguindo o padrão exato de `test_tiktok.py`.
- [x] Commit: `feat(tiktok): discover_by_keywords reusa sessao com termo dinamico`

### Task E3: Teste de ponta a ponta do guard-rail com todos os stages

**Files:**
- Create: `tests/app/test_research_pipeline_e2e.py` (ou anexar em
  `tests/app/test_research_pipeline.py` da Fase A)

Objetivo: provar que com `socials` tendo IG+YT+TikTok reais (fakes de
rede, não fakes de Protocol — usar `httpx.MockTransport`/mocks de cada
collector real) rodando juntos num ciclo só, o `FanoutGuard` realmente
limita o total de chamadas (`max_nodes_per_cycle` baixo no teste, ex. 5,
e assert que nenhum collector é chamado mais vezes que o fan-out
permitiria).

- [x] Steps de TDD completos.
- [x] Commit: `test(research): guard-rail de ponta a ponta com IG/YT/TikTok reais`

### Task E4: Gates + docs

- [x] Gates completos, HANDOFF atualizado, plano copiado, commit final.

> **Nota**: plano de intenção detalhada (não SDD travado como a Fase A) — leia os arquivos reais citados antes de executar e ajuste os detalhes finos; a arquitetura (Protocols, nomes, pontos de integração) é o que está fixado aqui.
