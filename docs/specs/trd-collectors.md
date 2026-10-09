# TRD — Coletores (YouTube · TikTok · Instagram · Web)

> **Status:** requisito técnico da fase B (plano tasks 12–15; a fronteira
> com registry/FakeCollector e o use-case `collect` vêm das tasks 10–11).
> Este TRD descreve o que será implementado — estado real: [HANDOFF](../HANDOFF.md).

## Fronteira técnica

**Dentro:** as 4 classes em `infra/collectors/` atrás do Protocolo
`app/ports.py::Collector`, a guarda SSRF de URLs e o enriquecimento de URL
(`cf ingest`). Cada collector devolve `CandidateVideo` (DTO pydantic) e o
upsert é idempotente por `UNIQUE(platform, platform_video_id)`.

**Não muda:** `domain/` (puro), casos de uso de `app/`, schema (14 tabelas
já existem), contrato da CLI (TRD [CLI](trd-cli.md)). Retry/backoff/circuit
breaker **só** na fronteira (spec §8); o domínio nunca retrya.

## Requisitos e critérios de aceitação

### YouTube — Data API v3 (task 12)

- [ ] R1 `YoutubeCollector.discover` consome `search.list` (keywords do
      segmento, `publishedAfter` = 7 dias, `order=viewCount`) → `videos.list`
      → `channels.list` — *teste: `tests/infra/collectors/test_youtube.py::test_discover_maps_fixture`*
- [ ] R2 Cota contada no collector (`search`=100, `videos`=1, `channels`=1;
      `quota_used == 102` na fixture) — *mesmo teste*
- [ ] R3 HTTP 403 → `RateLimited`; rede → `NetworkError`; shape/JSON
      inesperado → `UpstreamChanged` — *teste `test_quota_403_becomes_rate_limited`*
- [ ] R4 `shares`/`saves` = `None` (API não expõe); `duration_s` converte
      ISO 8601 (`PT8M12S` → 492) — *mesmo teste*

### TikTok — TikTok-Api v6 (task 13)

- [ ] R5 Descoberta = **trending + hashtag** `segment.keywords[0]`
      (spec §6); cliente injetável (testes usam stub; produção cria sessão
      Playwright) — *teste `test_tiktok.py::test_discover_maps_fixture`*
- [ ] R6 `ms_token` vazio/expirado → `AuthExpired` apontando
      `docs/dev/credentials.md` — *teste `test_missing_token_raises_auth_expired`*
- [ ] R7 `EmptyResponseException` → backoff `2^attempt ± jitter` ×5 →
      `CollectorError("blocked", "tiktok")` — *teste
      `test_empty_response_5x_becomes_blocked`*
- [ ] R8 Fingerprint: usar UA/viewport que a própria biblioteca gera (não
      forjar — spec §7) — *revisão de código*

### Instagram — Graph API oficial (task 14)

- [ ] R9 `GET /{ig-user-id}/hashtags?user_search=` →
      `GET /{hashtag-id}/top_media` — *teste `test_instagram.py::test_discover_maps_fixture`*
- [ ] R10 `views=None` e `duration_s=None` (Graph API não expõe views de
      terceiros — ADR-0003); `likes`/`comments` mapeados — *mesmo teste*
- [ ] R11 Header `X-App-Usage` lido em toda resposta (`app_usage`) —
      *mesmo teste*
- [ ] R12 HTTP 400/401 → `AuthExpired` — *teste `test_401_becomes_auth_expired`*

### Web — DuckDuckGo + ingest (task 15)

- [ ] R13 `WebCollector.discover` busca `ddgs` por keywords (sem chave) e
      mapeia `title/href/body` → `CandidateVideo(platform="web",
      views=None)` — *teste `test_web.py::test_discover_maps_ddgs`*
- [ ] R14 `cf ingest --url <url>` valida **antes** do fetch:
      scheme http/https, DNS resolve, IP público (bloqueia
      loopback/privado/link-local/`169.254.169.254`/multicast/reserved);
      timeout 10 s, máximo 2 MB — *testes `tests/infra/test_ssrf.py` +
      `tests/app/test_ingest.py::test_ingest_blocks_private`*
- [ ] R15 Enriquecimento extrai `<title>`/`meta description` e devolve
      `CandidateVideo`; com `--segment` faz upsert — *teste
      `test_ingest_extracts_title`*
- [ ] R16 Falha parcial não derruba o lote: erro por plataforma vira
      `collect_runs.status`/`error_code` + exit `2` (spec §8) —
      *testes do use-case `collect` (task 11)*

## Interfaces e dependências

| Item | Detalhe |
|---|---|
| Protocolo | `Collector` (`platform`, `discover(segment, limit) -> list[CandidateVideo]`) |
| DTO | `CandidateVideo` — campos nullable onde a plataforma não expõe |
| Wire | `infra/collectors/__init__.py::register_defaults(settings)` — chamado 1× no entrypoint |
| Settings | `youtube_api_key`, `instagram_access_token`, `ig_user_id`, `tiktok_ms_token`, `tiktok_proxy` |
| Externos | `googleapis.com/youtube/v3`, TikTok-Api (Playwright), `graph.facebook.com/v21.0`, `ddgs` |
| Erros → banco | `auth_expired`, `rate_limited`, `blocked`, `upstream_changed`, `network`, `invalid_payload` em `collect_runs.error_code` |
| Mocks | fixtures JSON gravadas em `tests/fixtures/` + `httpx.MockTransport` / stub de cliente / monkeypatch do `DDGS` |

## Pré-condições antes de código

- [ADR-0003](../adr/0003-coletores-fontes.md) aceito (APIs oficiais +
  TikTok-Api atrás de Protocol).
- Tasks 10–11 prontas: registry + `FakeCollector`, use-case `collect` com
  rate-limit/lock e gravação de `collect_runs`.
- Credenciais: [dev/credentials](../dev/credentials.md) (conta secundária
  TikTok; CI roda **sem chaves**, com fixtures).

## Riscos e limites técnicos

| Risco | Mitigação |
|---|---|
| TikTok anti-bot (`EmptyResponseException`) | backoff + `blocked` após 5 tentativas; Protocolo permite trocar backend (Apify) sem tocar em `app/`/`domain/` |
| Cota YouTube (100/unidade de busca) | `quota_used` rastreado, gravado em `collect_runs` |
| Instagram sem views de terceiros | métricas nullable desde o DTO; ver TRD [scoring](trd-scoring.md) |
| `ms_token` expira | `auth_expired` claro + rotação documentada |
| SSRF via URL de agente | guarda obrigatória antes de qualquer fetch (R14) |
