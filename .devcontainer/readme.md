# Stack content-factory (compose único)

Compose único em `.devcontainer/docker-compose.yml` — 1 container por
responsabilidade, padrão da casa. Rede interna `content-factory-net`;
Postgres exposto **só** em `127.0.0.1:5432` (dev).

## Componentes

| Componente | Versão | POR QUÊ |
|---|---|---|
| python | `python:3.14-slim` | runtime do projeto (`requires-python >=3.14`); slim reduz a superfície da imagem |
| postgres | `pgvector/pgvector:pg16` | PostgreSQL 16 + extensão pgvector p/ busca vetorial futura — ADR-0001 |
| redis | `redis:7-alpine` | fila arq, cache, rate-limit e locks; alpine = imagem mínima — ADR-0002 |
| playwright (worker) | chromium (`playwright install --with-deps`) | TikTok-Api v6 usa browser headless — ADR-0003 |
| uv | latest via `pip install uv` | toolchain de deps do projeto (lock `uv.lock`, builds rápidos) |

## Serviços

| Serviço | Container | Papel |
|---|---|---|
| `python` | `cf-python` | devcontainer (target `dev`), bind-mount do repo em `/app` |
| `worker` | — | target `worker` (chromium); profile `worker` — só sobe com `--profile worker` (código da fila vem na Task 17) |
| `postgres` | `content-factory-postgres-1` | pgvector/pg16, volume `pgdata`, porta `127.0.0.1:5432` |
| `redis` | `content-factory-redis-1` | sem porta pública (rede interna) |

## Env em camadas (2 + secrets)

| Arquivo | Versionado | Conteúdo |
|---|---|---|
| `env/defaults.env` | sim | defaults não-sensíveis (rate-limit, cron) |
| `env/app.env.example` → `env/app.env` | exemplo sim, real **não** | chaves do `Settings` com valores vazios |
| `secrets/secrets.env.example` → `secrets/secrets.env` | exemplo sim, real **não** | só tokens (ver `secrets/README.md`) |

Precedência no container: `environment:` (compose) > `env_file` (último
arquivo vence) > default do `Settings`.

## Bootstrap (antes do primeiro `up`)

```bash
cp .devcontainer/env/app.env.example .devcontainer/env/app.env
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env
```

O build do serviço `python` declara o diretório temporário de features do
Dev Containers como contexto adicional. O CLI gera `Dockerfile-with-features`
fora do contexto principal; o Compose usa o caminho do contexto adicional para
autorizar a leitura estritamente desse diretório no Buildx.

## Subir a stack

```bash
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
docker compose -f .devcontainer/docker-compose.yml exec python uv run cf ping
```

Esperado: `{"ok": true, "command": "ping", ...}`

## TEST_DATABASE_URL (derivado do compose)

Host → container (`127.0.0.1:5432`, credenciais default do compose):

```bash
export TEST_DATABASE_URL='postgresql+psycopg://contentfactory:localdev@localhost:5432/contentfactory'
export DATABASE_URL="$TEST_DATABASE_URL"   # Alembic lê DATABASE_URL (Settings)
uv run alembic upgrade head
uv run pytest -q
```

## Gates

```bash
make lint && make typecheck      # ruff + pyright strict
make test                        # pytest sem infra
TEST_DATABASE_URL=… make test-int # integração (compose de pé)
```
