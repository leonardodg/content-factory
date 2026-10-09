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
- Bug secundário de mascaramento: **backlog** (ver
  `docs/REVISAO_2026-10-07.md` §6 e ledger).

## Com chaves reais (playtest completo — pendente do usuário)

```bash
# preencher .devcontainer/secrets/secrets.env FORA do git (nunca commitado)
docker exec cf-python uv run cf collect --segment candles \
  --platform youtube,tiktok,instagram,web --limit 10
# esperado: exit 0 (sem partial) + runs status ok nas 4 plataformas
```
