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

## Com chaves válidas (playtest completo — pendente do usuário)

```bash
# 1) corrigir secrets.env FORA do git: YOUTUBE_API_KEY (AIzaSy…) e
#    INSTAGRAM_ACCESS_TOKEN (long-lived ~180 chars) + IG_USER_ID
# 2) rebuild caso o código tenha mudado (worker não tem bind-mount!):
docker compose -f .devcontainer/docker-compose.yml --profile worker build worker
docker compose -f .devcontainer/docker-compose.yml --profile worker up -d worker
# 3) re-rodar collect (via worker, caminho de produção):
docker exec -i cf-python uv run python - <<'PY'
from arq.connections import ArqRedis
async def main():
    r = ArqRedis.from_url("redis://redis:6379/0")
    print(await r.enqueue_job("collect_job", "candles", ["web", "youtube", "instagram"], 3))
import asyncio; asyncio.run(main())
PY
# esperado: runs status ok nas 3 plataformas; depois score_job scored >= 7
```
