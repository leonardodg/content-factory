# Plano de teste E2E — playtest manual (Task 18, 2026-10-09)

Playtest manual da stack real via compose `content-factory` (postgres/redis/python).
A suíte automatizada (`tests/e2e/`) roda com `uv run pytest -q -m e2e` +
`TEST_DATABASE_URL` (collector fake, sem chaves); este documento é o caminho
**com coletores reais**.

## Pré-requisitos

```bash
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
```

- **Chaves**: `.devcontainer/secrets/secrets.env` (gitignored) já existe com as
  3 chaves **vazias** (`YOUTUBE_API_KEY`, `INSTAGRAM_ACCESS_TOKEN`/`IG_USER_ID`,
  `TIKTOK_MS_TOKEN`). Placeholders não são commitados; valores reais entram
  fora do git, fornecidos pelo usuário (ver `docs/dev/credentials.md`).
- **Onde rodar**: o Redis do compose **não publica porta no host** — o CLI roda
  **dentro do container `cf-python`** (`docker exec cf-python uv run cf …`),
  que já recebe `DATABASE_URL`/`REDIS_URL` do compose.
- **Schema**: `uv run alembic upgrade head` com `DATABASE_URL` apontando ao
  Postgres do compose.

## Transcrição do playtest (2026-10-09)

```console
$ docker exec cf-python uv run cf ping
{"ok": true, "command": "ping"}

$ docker exec cf-python uv run cf segment load segments/candles.yaml
{"ok": true, "command": "segment.load", "slug": "candles", "id": 40}

# 1) collect só web (sem chave) — caminho de produção do register_defaults:
$ docker exec cf-python uv run cf collect --segment candles --platform web --limit 3
{"ok": true, "command": "collect", "found": 3, "inserted": 3, "updated": 0, "failed": 0}
exit=0

# 2) collect multi-plataforma (chaves vazias → yt/tt/ig falham como CollectorError):
$ docker exec cf-python uv run cf collect --segment candles --platform youtube,tiktok,instagram,web --limit 3
{"ok": true, "command": "collect", "partial": true, "found": 3, "inserted": 3, "updated": 0, "failed": 3}
exit=2

$ docker exec cf-python uv run cf score --segment candles
{"ok": true, "command": "score", "scored": 7}
exit=0

$ docker exec cf-python uv run cf status --segment candles
{"ok": true, "command": "status", "runs": {"failed": 3, "ok": 2}}
exit=0
```

## Contagens no banco (psql, mesmo Postgres do compose)

```console
$ psql -c "SELECT platform, count(*) FROM videos GROUP BY platform ORDER BY platform;"
 platform | count
----------+-------
 web      |     6
 youtube  |    28        -- inclui seeds de testes de integração prévios
(2 rows)

$ psql -c "SELECT count(*) FROM video_scores;"
 35                       -- inclui scores de seeds prévios; scored=7 do playtest ⊆ isto

$ psql -c "SELECT collector, status, count(*) FROM collect_runs
           GROUP BY collector, status ORDER BY collector, status;"
 collector | status | count
-----------+--------+-------
 instagram | failed |     1
 tiktok    | failed |     1
 web       | ok     |     2
 youtube   | failed |     1
```

Critério de aceitação do playtest: **`scored >= 1`** (7) e **linhas novas em
`videos`/`video_scores`/`collect_runs`** — atendido.

## Bug real achado neste playtest (e corrigido)

O 1º `cf collect --platform web` falhou com `internal_error`:
`WebCollector` usava o href completo como `platform_video_id`
(`VARCHAR(64)`) — URLs reais do DuckDuckGo > 64 chars estouravam o upsert
(`StringDataRightTruncation`), abortando a transação; o `finally` que grava o
`run` então falhava com `InFailedSqlTransaction`, **mascarando a causa raiz**.

- Correção (decisão humana, opção 1): `platform_video_id = sha1(url)` em
  `infra/collectors/web.py` — commit `f393674` (TDD).
- Bug secundário de mascaramento: **corrigido** — savepoint por candidato no
  upsert + `run.failed`/`counts["skipped"]` (commit `21ad56b`, TDD; ver
  "Playtest via worker" abaixo e `docs/REVISAO_2026-10-07.md` §6 #1).

## Playtest via worker (2026-10-10 — caminho de produção)

Escopo aprovado: worker com `secrets.env` (não o CLI do host), enqueue via
Redis (`collect_job`/`score_job`), `COLLECT_PLATFORMS=web,youtube,instagram`,
`COLLECT_LIMIT=3` em `.devcontainer/env/app.env`.

### Bug 2: imagem do worker stale (diagnóstico, não de código)

- **Sintoma**: runs de `web` terminando `failed internal_error` mesmo depois
  do fix `f393674` estar no worktree.
- **Causa**: o serviço `worker` não tem bind-mount — o código vem da imagem,
  buildada **antes** do fix (dentro do container `platform_video_id` ainda era
  `r.get("href")`; no host já era `sha1`). Verificado com
  `docker exec … grep platform_video_id` (×1 no container vs ×1 no host, com
  implementações diferentes).
- **Correção operacional**: `docker compose --profile worker build worker` +
  `up -d worker`. Re-run: run de `web` → `ok`, 3 found/3 updated, id sha1.
- **Lição documentada**: mudança de código exige rebuild da imagem do worker.

### Bug 3 (o do plano): mascaramento reproduzido e corrigido

O fix `21ad56b` fecha a REVISÃO §6 #1, que este playtest motivou:

- `upsert_candidates` usa `begin_nested` por candidato (precedente T16/score):
  candidato com dado doente é revertido **só ele** e vira `stats.skipped`;
  `OperationalError`/erros não-de-dados seguem propagando (não são engolidos).
- `app/collect.py` reflete `skipped` em `CollectRun.failed` (audit: linha
  persiste com `status='ok'` **e** `failed=N`) e em `counts["skipped"]`
  (chave nova — `counts["failed"]` continua sendo só falha de plataforma, a
  mensagem da CLI em `cli/main.py:156` segue verdadeira).
- TDD: `test_upsert_candidates_skips_bad_candidate_keeps_txn_alive` (reproduz
  `StringDataRightTruncation` no meio do lote e prova transação viva) e
  `test_skipped_candidates_visible_in_run_failed_and_counts`.

### Resultado com as chaves atuais (inválidas — pendente do usuário)

| Plataforma | Run | Resultado | Diagnóstico |
|---|---|---|---|
| web | 66 | `ok` (3 found, 3 updated, sha1) | caminho verde |
| youtube | 64 | `failed` `upstream_changed` | key inválida (ver abaixo) |
| instagram | 65 | `failed` `auth_expired` | token não parseável (ver abaixo) |

- **YouTube** (`YOUTUBE_API_KEY`, 53 chars, prefixo `AQ.A`): Google recusa —
  geocoding "provided API key is invalid"; YouTube/CustomSearch `401 API keys
  are not supported by this API`. Formato esperado de key de navegador:
  `AIzaSy…` (39 chars). **Ação**: criar key nova no Google Cloud Console
  (habilitar YouTube Data API v3).
- **Instagram** (`INSTAGRAM_ACCESS_TOKEN`, 32 chars): Graph API `400 Cannot
  parse access token`. Token long-lived real tem ~180 chars. **Ação**: copiar
  o token completo (Graph API Explorer / System User, escopo `instagram_basic`).
- **TikTok**: pulado por design (sem `ms_token`, spec §8).

### Scoring + sane checks (verdes)

- `score_job` no worker → `{'ok': True, 'scored': 7}`; 7 linhas em
  `video_scores` (títulos reais, `viral_score` 0–25).
- `cf status --segment candles` → `{"ok": true, "runs": {"ok": 3, "failed": 5}}`.
- Sane checks: 0 vazamento de chave/token nos logs do worker; nenhum lock
  órfão (`collect:candles` liberado); `cf:rl:youtube` com TTL; 0 jobs failed
  no container após o fix.

## Playtest com chaves válidas (2026-10-10 — concluído)

Credenciais novas fornecidas pelo usuário; YouTube já verde (run 111,
`AIza…` 39 chars → ok, 3 inserted). O Instagram ainda falhava → Bug 4.

### Bug 4: collector Instagram chamava endpoint inexistente

- **Sintoma**: runs 112/113 `instagram failed auth_expired`.
- **Causa raiz**: `discover` chamava `GET /{user_id}/hashtags?user_search=…` —
  o Graph responde `(#100) Tried accessing nonexisting field (hashtags)`; como
  qualquer 400 vira `AuthExpired`, a falha mascarava um erro de endpoint como
  erro de token.
- **Endpoint correto** (docs Meta v21.0): `GET /ig_hashtag_search?user_id&q` →
  `GET /{tag_id}/top_media?user_id&fields&limit`.
- **Gate #10**: `ig_hashtag_search` exige o produto *Instagram Public Content
  Access* (App Review do Meta — ação do usuário). Sem review, o Graph responde
  400 code 10.
- **Correção** (commit `fc320bf`, TDD — 3 testes novos): endpoint novo;
  code 10 deixou de virar `AuthExpired` e agora aciona fallback automático
  para `GET /{user_id}/media` (mídia da própria conta vinculada), filtrada por
  keyword casefold do segmento (a conta vinculada — de terceiro — publica
  conteúdo misto: velas, sabonetes, macramê). Busca vazia de hashtag **não** aciona o
  fallback. Plano: `docs/ai-plans/2026-10-10-fix-instagram-collector.md`.
- **Token**: o `INSTAGRAM_ACCESS_TOKEN` antigo (`IGAA…`, 187 chars) era de
  outro produto (Instagram API with Instagram Login) e o Graph respondia
  `Cannot parse access token`. Promovido o token funcional
  (`EAA…`, 302 chars, escopo Instagram Graph) de `INSTAGRAM_ACCESS_TOKEN_TEST`
  para `INSTAGRAM_ACCESS_TOKEN` **fora do git** (secrets.env).
- **Worker**: código + `secrets.env` mudaram → `up -d --build --force-recreate
  worker` (o worker não tem bind-mount; `secrets.env` chega como Docker secret
  `app_secrets`, materializado na criação do container — restart não basta).
  Verificado dentro do container: `ig_hashtag_search` presente, token `EAA` 302.

### Resultado (runs 123–125 + score_job)

| Run | Plataforma | Status | found/inserted/updated | failed |
|---|---|---|---|---|
| 123 | youtube | `ok` | 3 / 1 / 2 | 0 |
| 124 | instagram | `ok` | 3 / 3 / 0 | 0 |
| 125 | web | `ok` | 3 / 0 / 3 | 0 |

```console
$ psql -c "SELECT id, platform_video_id, raw->>'username' AS username,
                  raw->>'like_count' AS likes FROM videos
           WHERE platform='instagram' ORDER BY id;"
 id  | platform_video_id |   username    | likes
-----+-------------------+---------------+-------
 277 | <id-do-post>      | <conta>        |    77
 278 | <id-do-post>      | <conta>        |    30
 279 | <id-do-post>      | <conta>        |    79

# score_job → {'ok': True, 'scored': 15}; scores dos IG:
 viral_score 16.5, relevance 12.5 (keywords), engagement 0
```

- `engagement`/`performance` = 0 é **comportamento documentado**: a fórmula
  exige `views` (`domain/scoring.py:15`) e o Graph API não expõe views de
  terceiros (ADR-0003). `likes`/`comments` ficam em `raw`.
- Critério de aceitação: **IG `status=ok` com `found > 0`** + linhas novas em
  `videos`/`video_scores` — atendido.

### Pendência (usuário): App Review do Meta

O gate #10 bloqueia hashtag de terceiros até o app passar por *App Review*
(Instagram Public Content Access). Enquanto isso o fallback own-media cobre a
coleta. Quando o review aprovar, `ig_hashtag_search` volta a responder e o
caminho de hashtags é usado automaticamente — nenhuma mudança de código.

### Bug 5 (2026-10-12): token do produto errado, mascarado como "igual ao gate #10"

- **Sintoma**: playtest real (`cf collect --platform instagram`) voltou
  `auth_expired` e depois `upstream_changed` — não mais `ok` com fallback
  como nos runs 123-140 documentados acima. `INSTAGRAM_ACCESS_TOKEN` e
  `IG_USER_ID` estavam preenchidos nos dois arquivos de env.
- **Diagnóstico** (probe direto na Graph API, fora do coletor, para isolar
  token de código): `GET /ig_hashtag_search` devolvia
  `403 "(#10) Requires instagram_basic permission to manage the object"` —
  **code 10, mas sem a frase "public content access"**, logo
  `_is_public_content_gate` corretamente NÃO tratava como o gate documentado
  (comportamento certo, `test_generic_code10_is_not_gate` existe exatamente
  pra isso). `GET /{IG_USER_ID}` também falhava: `"does not exist, cannot be
  loaded due to missing permissions"`.
- **Causa raiz**: o `INSTAGRAM_ACCESS_TOKEN` em uso tinha sido gerado pelo
  produto **"API do Instagram com login do Instagram"**
  (`instagram_business_basic`) em vez de **"API do Instagram com login do
  Facebook"** (`instagram_basic`) — são dois fluxos OAuth/escopo diferentes
  no mesmo app Meta, e só o segundo é compatível com
  `infra/collectors/instagram.py` (`graph.facebook.com/v21.0`,
  `ig_hashtag_search`). Confirmado ao vivo no app
  **app Meta anterior, de terceiro** (developers.facebook.com): a feature
  "Instagram Public Content Access" nunca tinha sido submetida para App
  Review (botão "Adicionar à análise do app", não "aguardando aprovação");
  "Ações necessárias" do app vazio — não era bloqueio da Meta.
- **Não é bug de código**: nenhuma das Tasks 2-5 da Fase 0 mudou esse
  comportamento, e os testes existentes (`test_generic_code10_is_not_gate`)
  já cobriam exatamente essa distinção corretamente.
- **Correção operacional**: gerado token novo pelo produto certo (passo a
  passo agora documentado em
  [`docs/manual/chaves-api.md` §2](manual/chaves-api.md#2-instagram-graph-api-instagram_access_token-ig_user_id),
  com o aviso de qual produto usar). Verificado antes de colar
  (`GET /{IG_USER_ID}?fields=id,username` → `200`), colado em
  `secrets.env` **e** `app.env`, containers recriados.
- **Resultado pós-fix**: `cf collect` real → `{"ok": true, "found": 5,
  "inserted": 2, "updated": 3, "instagram_gate_hits": 1}` (run 153); worker
  real (`docker compose --profile worker up`, job enfileirado manualmente
  via arq para não esperar o cron) → `collect_job` runs 154-156 `ok` nas 3
  plataformas, `score_job scored=23`.
- **Token estendido para 60 dias** logo em seguida (Access Token Debugger →
  "Estender token de acesso", expira 2026-12-11) e revalidado:
  `{"ok": true, "found": 3, "inserted": 0, "updated": 3, "instagram_gate_hits": 1}`.
  Passo a passo de extensão/renovação em
  `docs/manual/chaves-api.md` §2 passos 9-10. Pendência: repetir antes de
  2026-12-11 ou migrar para System User token (sem expiração) para
  produção.
