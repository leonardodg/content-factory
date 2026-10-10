# Plano: fix do collector Instagram (endpoint errado + gate #10)

Data: 2026-10-10 · Branch: `fix/collect-upsert-savepoint` · TDD obrigatório

## Contexto

Playtest E2E via worker mostrou `collect_runs.instagram.status = failed` (runs 112/113,
`auth_expired`). Diagnóstico em runtime (probes com os tokens reais em secrets.env):

1. **Endpoint inexistente**: `InstagramCollector.discover` chama
   `GET /{user_id}/hashtags?user_search=&limit=` → Graph 400
   `(#100) Tried accessing nonexisting field (hashtags)`. Como qualquer 400 vira
   `AuthExpired`, o run falha com mensagem de token — sintoma enganoso.
2. **Endpoint correto** (docs Meta v21.0):
   - `GET /ig_hashtag_search?user_id=<UID>&q=<kw>` → `{"data":[{"id":...}]}`
   - `GET /{hashtag_id}/top_media?user_id=<UID>&fields=...&limit=...`
3. **Gate de produto**: `ig_hashtag_search` responde 400
   `(#10) To use 'Instagram Public Content Access', your use of this endpoint must be reviewed`
   — exige App Review do Meta (ação do usuário, dias/semanas). Sem review, hashtag de
   terceiros fica indisponível.
4. **Caminho que funciona hoje** (probe verificado, 200): `GET /{user_id}/media` com o
   token EAA (`INSTAGRAM_ACCESS_TOKEN_TEST`) — a própria conta vinculada posta conteúdo
   de velas artesanais (várias legendas batem com as keywords de `candles`).

## Comportamento alvo (contrato TDD)

1. `discover` usa `ig_hashtag_search` (params `user_id`, `q`) e depois
   `{tag}/top_media` (params `user_id`, `fields`, `limit`).
2. Erro **code 10** (gate "Instagram Public Content Access") em `ig_hashtag_search`
   → fallback: `GET /{user_id}/media?fields=&limit=50`, filtrado por keyword
   (casefold, qualquer keyword do segmento na legenda); sem keywords → sem filtro.
   Limitado a `limit`.
3. `code 10` em `top_media` → mesmo fallback.
4. Qualquer outro 400/401 → `AuthExpired` (comportamento preservado);
   5xx/HTML → `UpstreamChanged`; rede → `NetworkError` (redact).
5. Busca legítima sem resultado → `[]` (sem fallback — fallback só no gate).

## Testes (RED → GREEN)

`tests/infra/collectors/test_instagram.py`:

- `test_discover_uses_ig_hashtag_search_and_top_media` — registra as requisições;
  espera caminho/params corretos e candidatos do fixture. (RED: hoje → 404 do mock →
  `UpstreamChanged`.)
- `test_gate_falls_back_to_own_media` — `ig_hashtag_search` devolve 400 code 10;
  `/{uid}/media` devolve mídia própria → candidatos voltam. (RED: hoje → `AuthExpired`.)
- `test_own_media_fallback_filters_by_keyword` — mídia própria com legendas
  match/não-match → só a match volta. (RED: idem.)
- Ajuste de rota no teste existente `test_discover_maps_fixture`
  (`"hashtags" in url` → `"ig_hashtag_search" in url`).

## Entrega operacional

1. Trocar `INSTAGRAM_ACCESS_TOKEN` pelo token EAA funcional em
   `.devcontainer/secrets/secrets.env` (o IGAA atual não é parseável pelo Graph).
2. Worker não tem bind-mount → `docker compose ... up -d --build --force-recreate worker`
   (código + secrets mudam).
3. Playtest: `collect_job` → `collect_runs` instagram `ok` com `found > 0` →
   `videos`/`video_scores` no Postgres → `score_job`.
4. Documentar evidências em `docs/PLANO_TESTE_E2E.md` + `docs/HANDOFF.md`.
5. Gates completos → commits → code review do delta → push → PR.

## Fora de escopo

- Submeter App Review do Meta (gate #10) — decisão/ação do usuário; depois do review o
  caminho de hashtags volta a ser usado automaticamente (o fallback só age no gate).
- Suporte ao token `IGAA` (produto "Instagram API with Instagram Login", base/escopas
  diferentes) — mantido apenas como `INSTAGRAM_ACCESS_TOKEN_TEST` de referência.
- Filtro de qualidade de conteúdo além da keyword (relevância de peça/segmento é
  assunto do score).

## Decisões registradas

- Fallback **automático** no gate, sem flag de env: quando o App Review aprovar, o
  caminho de hashtags volta a ser usado automaticamente (o gate deixa de existir).
- Filtro por keyword no fallback porque a própria conta publica conteúdo misto
  (velas, sabonetes, macramê) — sem filtro, o fallback injetaria peça irrelevante.
- Busca vazia (keyword inexistente) **não** cai no fallback: evita false-positive de
  relevância quando o caminho de hashtags está disponível.

## Deferrals do code review (2026-10-10)

Aplicados no delta: matcher do gate restrito a `code 10` **+** frase
"public content access" (um `#10` genérico voltaria a ser `AuthExpired` —
não pode mascarar); gate na própria mídia própria vira `AuthExpired` ruidoso
(em vez de `[]` silencioso); testes do gate em `top_media` e da busca vazia
reforçado (agora capaz de falhar); `logger.warning` quando o fallback dispara;
docstring do `_get` explicando o `None`.

Deferidos (não bloqueiam):

1. Filtro de `media_type` (só vídeo/carrossel) no `_map` — pré-existente
   (também afeta `top_media`); relevante para o analyzer de Reels, tratar em
   issue do backlog.
2. Assimetria de `#`: `q` remove `#` mas o filtro de keyword e o scoring não —
   keyword cadastrada como `#vela` nunca casa legenda (pré-existente no
   scoring, `app/score.py`).
3. Paginação do fallback (uma página de 50; sem match nos recentes → `[]`
   mesmo havendo match em posts antigos).
