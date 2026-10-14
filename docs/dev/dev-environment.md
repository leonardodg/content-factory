# Ambiente de desenvolvimento

## Pré-requisitos

`uv`, Docker + Compose v2, Node/`npx` (só para regenerar diagramas).

## Stack (compose único)

`.devcontainer/docker-compose.yml` — 1 container por responsabilidade, rede
interna `content-factory-net`:

| Serviço | Papel |
|---|---|
| `python` (`cf-python`) | devcontainer (target `dev`), repo montado em `/app` |
| `worker` | target `worker` (Playwright/chromium); profile `worker` — só sobe com `--profile worker` |
| `postgres` | `pgvector/pgvector:pg16`, porta **só** `127.0.0.1:5432`, volume `pgdata` |
| `redis` | `redis:7-alpine`, **sem porta pública** (rede interna; ADR-0002) |

Componentes/versões e por quê: `.devcontainer/readme.md`.

## Env em camadas

| Arquivo | Versionado | Conteúdo |
|---|---|---|
| `.devcontainer/env/defaults.env` | sim | defaults não-sensíveis (rate-limit, cron) |
| `.devcontainer/env/app.env.example` → `app.env` | exemplo sim | `Settings` com valores vazios |
| `.devcontainer/secrets/secrets.env.example` → `secrets.env` | exemplo sim | só tokens (ver [credentials](credentials.md)) |
| `.env` (raiz) | **nunca** | override local pontual |

Precedência no container: `environment:` do compose > último `env_file` >
default do `Settings`. Segredos ficam fora do git (validado pelo teste de
privacidade no CI).

## Subir

```bash
cp .devcontainer/env/app.env.example .devcontainer/env/app.env
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
docker compose -f .devcontainer/docker-compose.yml exec python uv run cf ping
```

Fora do container (host), o Postgres responde em `127.0.0.1:5432`.

## Banco de testes — `TEST_DATABASE_URL`

Suíte de integração e scripts de docs apontam para um banco **de teste**
explícito (nunca use o banco de produção por acidente):

```bash
export TEST_DATABASE_URL='postgresql+psycopg://contentfactory:localdev@localhost:5432/contentfactory'
uv run alembic upgrade head    # aplica as migrations no banco alvo
uv run alembic check           # modelo ≡ migrations (gate da casa)
uv run pytest -q
```

Sem a variável, os testes de integração são **pulados** com motivo claro
(`tests/conftest.py`).

## Redis de testes — `TEST_REDIS_URL`

Testes de lock/concorrência usam um Redis **descartável**, nunca o Redis do
experimento (`content-factory-redis-1`, sem porta pública):

```bash
docker run -d --rm --name cf-test-redis -p 127.0.0.1:56379:6379 redis:7-alpine
export TEST_REDIS_URL='redis://127.0.0.1:56379/15'
uv run pytest -q -m "not e2e"
docker rm -f cf-test-redis
```

Sem a variável, os testes de Redis são **pulados** (`requires_redis` em
`tests/conftest.py`). O CI expõe `TEST_REDIS_URL=redis://localhost:6379/15`
(banco 15, sem colisão com `REDIS_URL`).

## Gates (DoD — todos verdes antes de "pronto")

| Gate | Comando |
|---|---|
| Testes | `uv run pytest -q` (com `TEST_DATABASE_URL`) |
| Lint + formato | `uv run ruff check . && uv run ruff format --check .` |
| Tipos | `uv run pyright` (strict, 0 erros) |
| Migrations | `uv run alembic check` |
| Segurança | `uv run bandit -q -c pyproject.toml -r src/ && uv run pip-audit` |
| Docs | `uv run --only-group docs mkdocs build --strict` |
| Privacidade | `uv run python scripts/check_privacy.py` |

`make gates` roda o conjunto (sem alembic/pytest de integração — estes
precisam do compose). O CI (`.github/workflows/ci.yml`) roda a mesma
suíte com services pgvector/redis; o `docs.yml` roda o `mkdocs strict`.

## Docs gerados (nunca edite à mão)

```bash
export TEST_DATABASE_URL='…'   # mesma URL de cima
uv run python scripts/gen_datamodel.py   # → docs/data-model/README.md
uv run python scripts/gen_diagram.py     # → docs/diagram/{er.mmd,er.html,er.svg,er.png,README.md}
```

Ambos exigem `alembic check` verde antes de gerar; `gen_diagram.py` exige
`npx` (mermaid-cli) e falha com mensagem clara se faltar.
