# Fundação + coletores + banco — content-factory

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Montar a fundação de qualidade (tooling, camadas, CLI, banco
completo, docs, CI) e o motor de descoberta (4 coletores + viral score) do
content-factory, segmento `candles`.

**Architecture:** Monolito modular `content_factory` em camadas
(`cli/workers → app → domain`, `infra` implementa `ports.py`), CLI-first com
envelope JSON, PostgreSQL 16+pgvector (Alembic), Redis+arq, compose único no
padrão da casa.

**Tech Stack:** Python 3.14 · uv · ruff · pyright strict · SQLAlchemy 2 +
Alembic · psycopg3 · Redis 7 + arq · Typer · pydantic v2 · httpx · ddgs ·
TikTokApi + Playwright · YouTube Data API v3 · Instagram Graph API ·
mkdocs · GitHub Actions

**Spec:** `docs/specs/2026-10-07-content-factory-design.md` (fonte do quê e
porquê; ADRs 0001–0004; PRD em `docs/produto/prd.md`)

## Global Constraints

- Python **3.14**; gerenciamento **uv** (nunca pip solto); `uv.lock` commitado
- **pyright strict = 0 erros** — corrigir código, **nunca** rebaixar a config
- `ruff check` + `ruff format` limpos; ler o **total** do linter (exit code)
- **Domínio puro**: `domain/` sem SQL/HTTP/arquivos/`print`/`datetime.now()`
  (tempo entra como parâmetro `now: datetime`); dependência unidirecional
  `cli/workers → app → domain`; `infra` só é importado nos entrypoints
- **TDD**: escrever teste → ver falhar → implementar → ver passar → commit
- **Mock só na fronteira** (HTTP dos coletores/fixtures); scoring, regras e
  upsert rodam de verdade; integração usa Postgres/Redis reais
- CLI: envelope **`{"ok": bool, "command": str, ...}`**; exit `0` ok ·
  `2` falha parcial · `1` falha total; resultados ficam no banco
- **Nenhum segredo em arquivo rastreado**; `.env` gitignored; segredos só em
  `.devcontainer/secrets/` (gitignored, `*.example` versionados)
- Imagens Docker **pinadas**; compose único em `.devcontainer/docker-compose.yml`
- Conventional Commits com corpo do "porquê"
- Toda task fecha com gates verdes antes do próximo commit

---

## Contexto

Sessão de 2026-10-07: design aprovado por seções (spec + 4 ADRs + PRD
commitados). Este plano é a execução da frente 1+2 do PRD (fundação,
coletores, banco). O código legado (`src/` do InstagramReelsAnalyzer) é
referência de domínio, não base — movido/removido na Task 1.

## Decisões (não redesenhar)

Postgres+pgvector (ADR-0001) · Redis+arq (ADR-0002) · coletores oficiais +
TikTok-Api atrás de Protocol (ADR-0003) · CLI-first (ADR-0004) · monolito
modular · compose único padrão da casa · segurança/anti-bloqueio conforme
spec §7.

## Tasks (tracker)

| # | Task | ✅ |
|---|------|----|
| 1 | Tooling base (uv, pyproject, ruff, pyright, pre-commit) + legado | ☐ |
| 2 | Esqueleto de camadas + ports (Protocolos) + config | ☐ |
| 3 | Domínio: modelos + scoring + trend decay (TDD) | ☐ |
| 4 | Banco: ORM completo + Alembic + repositórios (integração) | ☐ |
| 5 | Segmentos: YAML → validação → persistência (TDD) | ☐ |
| 6 | CLI: envelope + exit codes + `segment load`/`status` (TDD) | ☐ |
| 7 | Docker: compose único + Dockerfile multi-stage + devcontainer + secrets | ☐ |
| 8 | CI: quality + docs + teste de privacidade | ☐ |
| 9 | Docs: mkdocs + architecture + dev + data-model/diagram | ☐ |
| 10 | Fronteira de coleta: `CandidateVideo` + registry + FakeCollector (TDD) | ☐ |
| 11 | Use-case `collect` + `collect_runs` + rate-limit/lock Redis (TDD) | ☐ |
| 12 | Collector YouTube (TDD com fixtures) | ☐ |
| 13 | Collector TikTok (TDD com fixtures) | ☐ |
| 14 | Collector Instagram (TDD com fixtures) | ☐ |
| 15 | Collector Web + `cf ingest` + guarda SSRF (TDD) | ☐ |
| 16 | Use-case `score` + `cf score` (TDD) | ☐ |
| 17 | Worker arq + cron 2×/dia + `cf status` | ☐ |
| 18 | E2E `00`/`01` + gates finais + SOLID_AUDIT + REVISAO + PR | ☐ |

---

### Task 1: Tooling base (uv, pyproject, ruff, pyright, pre-commit) + legado

**Files:**
- Create: `pyproject.toml` (substitui o atual/poetry), `.python-version`, `.pre-commit-config.yaml`, `CLAUDE.md`, `.env.example`, `Makefile`, `README.md` (novo, mínimo), `src/content_factory/__init__.py` (vazio — hatch precisa dele p/ build)
- Move: `src/*` → `legacy/` (exceto `__pycache__`); `README.md` antigo → `legacy/README-antigo.md`
- Modify: `.gitignore` (já tem `.env`, `database.db*`)

> O `readme = "README.md"` e o `packages = ["src/content_factory"]` do
> pyproject só resolvem se o novo README e o `__init__.py` existirem — sem
> eles o `uv sync` falha no build (falso negativo na Task 1).

**Interfaces:**
- Produz: toolchain onde todas as tasks seguintes rodam (`uv run …`)

- [ ] **Step 1: Mover legado + criar mínimos que o build exige**

```bash
mkdir -p legacy/src && mv src/* legacy/src/ 2>/dev/null; mv src/__pycache__ legacy/src/ 2>/dev/null; rmdir src 2>/dev/null
mv README.md legacy/README-antigo.md
mkdir -p src/content_factory
touch src/content_factory/__init__.py
```

`README.md` novo (raiz — `pyproject` aponta pra ele):

```markdown
# content-factory

Motor de inteligência de conteúdo parametrizado por segmento.
Estado: docs/HANDOFF.md · Spec: docs/specs/ · CLI: `cf --help`
```

- [ ] **Step 2: `pyproject.toml`**

```toml
[project]
name = "content-factory"
version = "0.1.0"
description = "Motor de inteligência de conteúdo parametrizado por segmento"
readme = "README.md"
requires-python = ">=3.14"
license = { text = "MIT" }
dependencies = [
  "typer>=0.15",
  "pydantic>=2.10",
  "pydantic-settings>=2.7",
  "pyyaml>=6.0",
  "sqlalchemy>=2.0.36",
  "alembic>=1.14",
  "psycopg[binary]>=3.2",
  "redis>=5.2",
  "arq>=0.26",
  "httpx>=0.28",
  "ddgs>=9.0",
  "TikTokApi>=6.4",
  "playwright>=1.49",
  "python-dateutil>=2.9",
  "pgvector>=0.5.0",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.25",
  "pytest-cov>=6.0",
  "ruff>=0.9",
  "pyright>=1.1.390",
  "bandit>=1.8",
  "pip-audit>=2.7",
  "pre-commit>=4.0",
  "radon>=6.0",
]
docs = ["mkdocs>=1.6", "mkdocs-material>=9.5", "mkdocs-mermaid2-plugin>=1.2"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/content_factory"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]
exclude = ["legacy", ".venv", "alembic/versions"]
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]
[tool.ruff.format]
quote-style = "double"

[tool.pyright]
typeCheckingMode = "strict"
include = ["src", "tests"]
venvPath = "."
venv = ".venv"

[tool.bandit]
targets = ["src"]
# B101 (assert) aceito em testes; tests/ fora do scan do bandit
exclude_dirs = ["tests", "legacy", ".venv"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
  "integration: requer Postgres/Redis de pé (compose)",
  "e2e: testes E2E numerados (tests/e2e/)",
]
```

- [ ] **Step 3: `.python-version` + criar venv + instalar**

```bash
echo "3.14" > .python-version
uv sync --all-groups
```

- [ ] **Step 4: `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: end-of-file-fixer
      - id: check-yaml
        exclude: ^mkdocs\.yml$
      - id: check-added-large-files
      - id: check-merge-conflict
      - id: detect-private-key
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.9.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/compilerla/conventional-pre-commit
    rev: v3.8.0
    hooks:
      - id: conventional-pre-commit
```

- [ ] **Step 5: `CLAUDE.md` (só regras imutáveis; estado vive no HANDOFF)**

```markdown
# content-factory — regras imutáveis

- Estado: ver docs/HANDOFF.md (única fonte de estado)
- Plano: docs/ai-plans/ antes de qualquer código; TDD sempre
- pyright strict 0 erros — nunca rebaixar config
- domain/ é puro (sem I/O); infra implementa app/ports.py
- Nenhum segredo em arquivo rastreado; .env nunca commita
- Gates antes de "pronto": pytest, ruff, pyright, alembic check, bandit,
  pip-audit, mkdocs --strict, teste de privacidade (ver spec §10)
- CLI devolve só envelope {"ok": ...}; dados no banco
```

- [ ] **Step 6: `.env.example` (raiz, interpolação do compose)**

```env
# interpolação ${VAR} do compose — NUNCA coloque valores reais aqui
POSTGRES_USER=contentfactory
POSTGRES_DB=contentfactory
```

- [ ] **Step 7: `Makefile`**

```make
.PHONY: test test-int lint typecheck security docs gates
test:        ; uv run pytest -q -m "not integration and not e2e"
test-int:    ; uv run pytest -q -m integration
lint:        ; uv run ruff check . && uv run ruff format --check .
typecheck:   ; uv run pyright
security:    ; uv run bandit -q -c pyproject.toml && uv run pip-audit
docs:        ; uv run --only-group docs mkdocs build --strict
gates: test lint typecheck security docs
```

- [ ] **Step 8: Rodar gates e ver falhar onde ainda não existe**

```bash
uv run ruff check . ; uv run pyright ; uv run pytest -q
```
Expected: ruff/pyright passam (sem código ainda ou só legado ignorado);
pytest: **no tests ran** (exit 5) — ok, testes nascem na Task 3.

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "chore: toolchain uv+ruff+pyright strict+pre-commit

Fundacao de qualidade exigida pelo spec (DoD): mesmo toolchain da CI,
legado movido para legacy/ como referencia de dominio."
```

---

### Task 2: Esqueleto de camadas + ports (Protocolos) + config

**Files:**
- Create: `src/content_factory/__init__.py`, `src/content_factory/domain/__init__.py`,
  `src/content_factory/domain/errors.py`, `src/content_factory/app/__init__.py`,
  `src/content_factory/app/ports.py`, `src/content_factory/config.py`,
  `src/content_factory/infra/__init__.py`, `src/content_factory/cli/__init__.py`,
  `tests/__init__.py`, `tests/app/test_ports.py`
- Modify: nenhum

**Interfaces:**
- Produz (usado por TODAS as tasks seguintes):

```python
# app/ports.py
class Collector(Protocol):
    platform: ClassVar[str]

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]: ...

    # aceita SegmentConfig (e não Segment): coletores não precisam do id —
    # o id só entra no upsert, via VideoRepository.upsert_candidates


class VideoRepository(Protocol):
    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats: ...


class SegmentRepository(Protocol):
    async def get_by_slug(self, slug: str) -> Segment: ...
    async def upsert(self, cfg: SegmentConfig) -> Segment: ...


class CollectRunRepository(Protocol):
    async def record(self, run: CollectRun) -> int: ...


class RateLimiter(Protocol):
    async def acquire(self, platform: str) -> None: ...  # bloqueia até liberar


class Lock(Protocol):
    async def acquire_once(self, key: str) -> bool: ...  # False se já em execução
    async def release(self, key: str) -> None: ...  # SEM isso o lock fica eterno
```

(Segment/CandidateVideo/CollectRun/UpsertStats vêm de `domain/models.py`,
criado abaixo; `ScoreResult` só existe na Task 3 — `ScoreRepository` é
**adicionado ao ports na Task 16**, nunca aqui: pyright strict reprova
import não resolvido.)

- `domain/errors.py`: `DomainError`, `SegmentNotFound`, `CollectorError(code)`,
  `AuthExpired`, `RateLimited`, `UpstreamChanged`, `InvalidPayload`,
  `NetworkError` (códigos de spec §8: `auth_expired`, `rate_limited`,
  `blocked`, `upstream_changed`, `network`, `invalid_payload`)
- `config.py`: `Settings(BaseSettings)` com `database_url`, `redis_url`,
  `youtube_api_key`, `instagram_access_token`, `ig_user_id`,
  `tiktok_ms_token`, `tiktok_proxy`

- [ ] **Step 1: Teste falhando (imports)**

`tests/app/test_ports.py`:

```python
from typing import get_type_hints
from content_factory.app import ports
from content_factory.domain.errors import CollectorError


def test_collector_protocol_declares_discover() -> None:
    hints = get_type_hints(ports.Collector.discover)
    assert set(hints) >= {"segment", "limit", "return"}


def test_collector_error_carries_code() -> None:
    err = CollectorError("blocked", "tiktok")
    assert err.code == "blocked"
    assert err.platform == "tiktok"
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
uv run pytest tests/app/test_ports.py -v
```
Expected: FAIL — `ModuleNotFoundError: content_factory`

- [ ] **Step 3: Implementar esqueleto**

`domain/errors.py`:

```python
class DomainError(Exception):
    """Erro de negócio do domínio."""


class SegmentNotFound(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"segmento não encontrado: {slug}")
        self.slug = slug


class CollectorError(DomainError):
    """Erro classificado de coleta (gravado em collect_runs.error_code)."""

    def __init__(self, code: str, platform: str, message: str = "") -> None:
        super().__init__(message or f"{platform}:{code}")
        self.code = code
        self.platform = platform


class AuthExpired(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("auth_expired", platform, message)


class RateLimited(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("rate_limited", platform, message)


class UpstreamChanged(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("upstream_changed", platform, message)


class InvalidPayload(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("invalid_payload", platform, message)


class NetworkError(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("network", platform, message)
```

(Subclasses fixam o `code` — collectors escrevem
`raise RateLimited(self.platform, "quota 403")` em vez de repetir a
string; `CollectorError("blocked", platform)` continua direto, sem
subclasse.)

`domain/models.py` (mínimo desta task; cresce na Task 3):

```python
from datetime import datetime
from pydantic import BaseModel, Field


class SegmentConfig(BaseModel):
    slug: str
    name: str
    languages: list[str] = Field(default_factory=lambda: ["pt-BR"])
    platforms: list[str]
    content_types: list[str] = Field(default_factory=list)
    keywords: list[str]
    subnichos: dict[str, list[str]] = Field(default_factory=dict)


class Segment(SegmentConfig):
    id: int


class CandidateVideo(BaseModel):
    """DTO de fronteira: o que todo collector devolve (validado na entrada)."""

    platform: str
    platform_video_id: str
    url: str
    creator_handle: str
    creator_followers: int | None = None
    title: str = ""
    description: str = ""
    published_at: datetime | None = None
    duration_s: int | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    thumbnail_url: str | None = None
    raw: dict = Field(default_factory=dict)


class UpsertStats(BaseModel):
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


class CollectRun(BaseModel):
    collector: str
    segment_id: int | None = None
    status: str = "running"  # running|ok|partial|failed|blocked
    found: int = 0
    inserted: int = 0
    updated: int = 0
    failed: int = 0
    quota_used: int = 0
    retries: int = 0
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
```

`app/ports.py`: Protocolos do bloco Interfaces (métodos `...` com
`raise NotImplementedError` omitido — Protocol só declara).

`config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://contentfactory@localhost/contentfactory"
    redis_url: str = "redis://localhost:6379/0"
    youtube_api_key: str = ""
    instagram_access_token: str = ""
    ig_user_id: str = ""
    tiktok_ms_token: str = ""
    tiktok_proxy: str | None = None


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Rodar teste + gates**

```bash
uv run pytest tests/app/test_ports.py -q && uv run ruff check . && uv run ruff format . && uv run pyright
```
Expected: `1 passed` (mais os testes), ruff 0, pyright 0 erros

- [ ] **Step 5: Commit**

```bash
git add src tests && git commit -m "feat: contratos das camadas (ports) e erros de dominio

Protocolos tipados sao o ponto de extensao: novos collectors/repos entram
por aqui sem tocar em app/ nem domain/."
```

---

### Task 3: Domínio: modelos + scoring + trend decay (TDD)

**Files:**
- Create: `src/content_factory/domain/scoring.py`, `tests/domain/test_scoring.py`, `tests/domain/__init__.py`
- Modify: `src/content_factory/domain/models.py` (adicionar `VideoMetrics`, `ScoreResult`)

**Interfaces:**
- Produz (usado pela Task 16):
  - `viral_score(m: VideoMetrics, relevance: float, now: datetime, published_at: datetime | None) -> ScoreResult`
  - `ScoreResult(total: float, performance: float, velocity: float, engagement: float, relevance: float, recency: float, trend_decay: str)` (`trend_decay` ∈ `none|low|medium|high`)
  - `engagement_rate(m: VideoMetrics) -> float` (não-negativo; sem clamp
    superior — `viral_score` limita `min(eng * 10, 100.0)` antes de pesar)

- [ ] **Step 1: Testes falhando**

`tests/domain/test_scoring.py`:

```python
from datetime import UTC, datetime, timedelta
from content_factory.domain.models import VideoMetrics, ScoreResult
from content_factory.domain.scoring import engagement_rate, viral_score

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def metrics(**kw) -> VideoMetrics:
    base = dict(views=100_000, likes=5_000, comments=200, shares=100, saves=50)
    base.update(kw)
    return VideoMetrics(**base)


def test_engagement_rate_weighted() -> None:
    # likes*1 + comments*2 + shares*3 + saves*4 = 5000+400+300+200 = 5900 / 100000
    assert engagement_rate(metrics(views=100_000)) == 5.9


def test_engagement_rate_zero_views() -> None:
    assert engagement_rate(metrics(views=0)) == 0.0


def test_viral_score_returns_breakdown_and_total() -> None:
    r = viral_score(metrics(), relevance=0.9, now=NOW, published_at=NOW - timedelta(hours=6))
    assert isinstance(r, ScoreResult)
    for comp in (r.performance, r.velocity, r.engagement, r.relevance, r.recency):
        assert 0.0 <= comp <= 100.0
    assert 0.0 <= r.total <= 100.0
    assert r.trend_decay in {"none", "low", "medium", "high"}


def test_viral_score_prefers_recent_over_old() -> None:
    recent = viral_score(metrics(), 0.9, NOW, NOW - timedelta(hours=6))
    old = viral_score(metrics(), 0.9, NOW, NOW - timedelta(days=90))
    assert recent.total > old.total
    assert old.trend_decay == "high"


def test_viral_score_zero_metrics_is_zeroish() -> None:
    r = viral_score(
        metrics(views=0, likes=0, comments=0, shares=0, saves=0),
        relevance=0.0,
        now=NOW,
        published_at=None,
    )
    assert r.total == 0.0
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
uv run pytest tests/domain/test_scoring.py -v
```
Expected: FAIL — `ImportError: cannot import name 'VideoMetrics'`

- [ ] **Step 3: Implementar**

Adicionar em `domain/models.py`:

```python
class VideoMetrics(BaseModel):
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0


class ScoreResult(BaseModel):
    total: float
    performance: float
    velocity: float
    engagement: float
    relevance: float
    recency: float
    trend_decay: str
```

`domain/scoring.py`:

```python
from datetime import datetime

from content_factory.domain.models import ScoreResult, VideoMetrics

WEIGHTS = {
    "performance": 0.25,
    "velocity": 0.20,
    "engagement": 0.15,
    "relevance": 0.20,
    "recency": 0.20,
}


def engagement_rate(m: VideoMetrics) -> float:
    if m.views <= 0:
        return 0.0
    weighted = m.likes * 1 + m.comments * 2 + m.shares * 3 + m.saves * 4
    return round(weighted / m.views * 100, 2)


def trend_decay(published_at: datetime | None, now: datetime) -> str:
    if published_at is None:
        return "none"
    age_days = (now - published_at).days
    if age_days <= 7:
        return "low"
    if age_days <= 30:
        return "medium"
    return "high"


def viral_score(
    m: VideoMetrics, relevance: float, now: datetime, published_at: datetime | None
) -> ScoreResult:
    performance = min(m.views / 1_000_000 * 100, 100.0)
    eng = min(engagement_rate(m) * 10, 100.0)  # 10%+ = 100
    decay = trend_decay(published_at, now)
    age_days = (now - published_at).days if published_at else 999
    velocity = (
        100.0 if age_days <= 1 else 80.0 if age_days <= 3 else 50.0 if age_days <= 7 else 20.0
    )
    recency = {"none": 50.0, "low": 100.0, "medium": 50.0, "high": 15.0}[decay]
    rel = min(max(relevance, 0.0), 1.0) * 100

    total = round(
        performance * WEIGHTS["performance"]
        + velocity * WEIGHTS["velocity"]
        + eng * WEIGHTS["engagement"]
        + rel * WEIGHTS["relevance"]
        + recency * WEIGHTS["recency"],
        2,
    )
    if m.views == 0 and m.likes == 0 and relevance == 0:
        total = 0.0
    return ScoreResult(
        total=total,
        performance=round(performance, 2),
        velocity=velocity,
        engagement=round(eng, 2),
        relevance=round(rel, 2),
        recency=recency,
        trend_decay=decay,
    )
```

- [ ] **Step 4: Rodar teste + gates**

```bash
uv run pytest tests/domain -q && uv run ruff check . && uv run pyright
```
Expected: `5 passed`, 0 issues

- [ ] **Step 5: Commit**

```bash
git add src/content_factory/domain tests/domain && git commit -m "feat: scoring de dominio puro (viral score + trend decay)

Peso por componente fechado no spec 4.3; replication/production ficam para
a fase IA (colunas NULL no schema). now entra como parametro p/ teste
deterministico."
```

---

### Task 4: Banco: ORM completo + Alembic + repositórios (integração)

**Files:**
- Create: `src/content_factory/infra/db/base.py`, `src/content_factory/infra/db/orm.py`,
  `src/content_factory/infra/db/repositories.py`, `alembic.ini`,
  `alembic/env.py`, `tests/infra/__init__.py`, `tests/infra/test_orm.py`,
  `tests/infra/test_repositories.py`, `tests/conftest.py`
- Modify: `src/content_factory/app/ports.py` (repositórios conforme Task 2 —
  implementações aqui), `.gitignore` (`alembic` não; já cobre `.env`)

**Interfaces:**
- Produz: `get_session_factory(settings)`; classes `VideoRepositoryImpl`,
  `SegmentRepositoryImpl`, `CollectRunRepositoryImpl`; migration `0001` com
  TODAS as tabelas do spec §5; `alembic check` exit 0
- Consome: `Settings.database_url`, modelos do spec §5

- [ ] **Step 1: Infra de teste — Postgres/Redis de pé**

`tests/conftest.py`:

```python
import os
import pytest

requires_infra = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL ausente — suba o compose (ver docs/dev/dev-environment.md)",
)
```

`docker compose` de teste local: usar o mesmo compose da Task 7 com
`TEST_DATABASE_URL=postgresql+psycopg://contentfactory@localhost:5432/contentfactory`
(documentar em `docs/dev/` na Task 9). **Gate:** integration tests NÃO podem
rodar sem banco (skip é por ausência de env, erro explícito — nunca verde
falso).

- [ ] **Step 2: Teste falhando (ORM)**

`tests/infra/test_orm.py`:

```python
import os

import pytest
from sqlalchemy import create_engine, inspect
from tests.conftest import requires_infra


@requires_infra
@pytest.mark.integration
def test_all_tables_exist() -> None:
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    names = set(inspect(engine).get_table_names())
    expected = {
        "segments",
        "creators",
        "videos",
        "video_segments",
        "video_metrics",
        "video_scores",
        "transcripts",
        "creative_patterns",
        "ideas",
        "scripts",
        "production_jobs",
        "published_videos",
        "performance_metrics",
        "collect_runs",
    }
    assert expected <= names
```

- [ ] **Step 3: Rodar e ver falhar**

```bash
TEST_DATABASE_URL=postgresql+psycopg://… uv run pytest tests/infra/test_orm.py -v
```
Expected: FAIL (tabela inexistente / engine sem migrations)

- [ ] **Step 4: Implementar ORM (spec §5, campo a campo)**

`infra/db/orm.py` (SQLAlchemy 2 declarativo; JSONB, pgvector, FKs):

```python
from datetime import datetime
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, VECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SegmentRow(Base):
    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CreatorRow(Base):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("platform", "platform_creator_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    platform_creator_id: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(200))
    followers_count: Mapped[int | None]
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)


class VideoRow(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("platform", "platform_video_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    platform_video_id: Mapped[str] = mapped_column(String(64))
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id"))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int | None]
    language: Mapped[str | None] = mapped_column(String(16))
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    first_collected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_collected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    creator: Mapped[CreatorRow | None] = relationship()


class VideoSegmentRow(Base):
    __tablename__ = "video_segments"
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"), primary_key=True)
    discovery_source: Mapped[str] = mapped_column(String(64))
    relevance_score: Mapped[float | None] = mapped_column(Float)
    discovered_at: Mapped[datetime] = mapped_column(server_default=func.now())


class VideoMetricsRow(Base):
    __tablename__ = "video_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    views: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    shares: Mapped[int | None]
    saves: Mapped[int | None]
    followers_at_capture: Mapped[int | None]


class VideoScoreRow(Base):
    __tablename__ = "video_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), index=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    viral_score: Mapped[float] = mapped_column(Float, index=True)
    performance: Mapped[float] = mapped_column(Float)
    velocity: Mapped[float] = mapped_column(Float)
    engagement: Mapped[float] = mapped_column(Float)
    relevance: Mapped[float] = mapped_column(Float)
    recency: Mapped[float] = mapped_column(Float)
    trend_decay: Mapped[str] = mapped_column(String(8))
    replication_score: Mapped[float | None] = mapped_column(Float)  # fase IA
    production_score: Mapped[float | None] = mapped_column(Float)  # fase IA


class TranscriptRow(Base):
    __tablename__ = "transcripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), unique=True)
    source: Mapped[str] = mapped_column(String(32))
    language: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    segments: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CreativePatternRow(Base):
    __tablename__ = "creative_patterns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    psychology: Mapped[list] = mapped_column(JSONB, default=list)
    visual_structure: Mapped[list] = mapped_column(JSONB, default=list)
    best_for: Mapped[list] = mapped_column(JSONB, default=list)
    origin_video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"))
    embedding: Mapped[list | None] = mapped_column(VECTOR(1536))  # fase IA


class IdeaRow(Base):
    __tablename__ = "ideas"
    id: Mapped[int] = mapped_column(primary_key=True)
    segment_id: Mapped[int] = mapped_column(ForeignKey("segments.id"))
    pattern_id: Mapped[int | None] = mapped_column(ForeignKey("creative_patterns.id"))
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    embedding: Mapped[list | None] = mapped_column(VECTOR(1536))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ScriptRow(Base):
    __tablename__ = "scripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id"))
    title: Mapped[str] = mapped_column(Text)
    duration_s: Mapped[int | None]
    hook: Mapped[str] = mapped_column(Text, default="")
    scenes: Mapped[list] = mapped_column(JSONB, default=list)
    cta: Mapped[str] = mapped_column(Text, default="")
    production_difficulty: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ProductionJobRow(Base):
    __tablename__ = "production_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    scheduled_for: Mapped[str | None] = mapped_column(String(16))  # today|tomorrow|+7
    assets: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PublishedVideoRow(Base):
    __tablename__ = "published_videos"
    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"))
    platform: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PerformanceMetricRow(Base):
    __tablename__ = "performance_metrics"
    id: Mapped[int] = mapped_column(primary_key=True)
    published_video_id: Mapped[int] = mapped_column(ForeignKey("published_videos.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    views: Mapped[int | None]
    likes: Mapped[int | None]
    comments: Mapped[int | None]
    shares: Mapped[int | None]
    saves: Mapped[int | None]
    watch_time_s: Mapped[int | None]
    retention_pct: Mapped[float | None]
    completion_rate: Mapped[float | None]
    followers_gained: Mapped[int | None]


class CollectRunRow(Base):
    __tablename__ = "collect_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    collector: Mapped[str] = mapped_column(String(32), index=True)
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id"))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    found: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(32))
```

`infra/db/base.py` (assíncrono — os coletores/repos são async; psycopg3
suporta async com a mesma URL `postgresql+psycopg://`):

```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from content_factory.config import Settings


def get_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return async_sessionmaker(bind=engine, expire_on_commit=False)
```

- [ ] **Step 5: Alembic init + primeira migration**

```bash
uv run alembic init alembic
# em alembic/env.py: importar content_factory.infra.db.orm, usar target_metadata = Base.metadata
# em alembic/script.py.mako N/A (padrão)
uv run alembic revision --autogenerate -m "schema inicial content-factory"
# REVISAR o diff gerado antes de subir:
uv run alembic upgrade head
uv run alembic check   # exit 0
```

`alembic/env.py` (essencial):

```python
from alembic import context
from content_factory.config import get_settings
from content_factory.infra.db.orm import Base

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)
```

- [ ] **Step 6: Repositórios (implementam ports da Task 2)**

`infra/db/repositories.py`:

```python
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from content_factory.app.ports import CollectRunRepository, SegmentRepository, VideoRepository
from content_factory.domain.errors import SegmentNotFound
from content_factory.domain.models import (
    CandidateVideo,
    CollectRun,
    Segment,
    SegmentConfig,
    UpsertStats,
)
from content_factory.infra.db.orm import CollectRunRow, SegmentRow, VideoRow


class VideoRepositoryImpl(VideoRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_candidates(
        self, segment_id: int, source: str, candidates: Sequence[CandidateVideo]
    ) -> UpsertStats:
        stats = UpsertStats()
        for c in candidates:
            stmt = pg_insert(VideoRow).values(
                platform=c.platform,
                platform_video_id=c.platform_video_id,
                url=c.url,
                title=c.title,
                description=c.description,
                published_at=c.published_at,
                duration_s=c.duration_s,
                thumbnail_url=c.thumbnail_url,
                raw=c.raw,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["platform", "platform_video_id"],
                set_={
                    "title": stmt.excluded.title,
                    "last_collected_at": stmt.excluded.last_collected_at,
                },
            )
            res = await self._s.execute(stmt)
            if res.rowcount == 1:
                stats.inserted += 1
            else:
                stats.updated += 1
            # vídeo resolvido → grava vínculo video_segments
            # (ON CONFLICT (video_id, segment_id) DO UPDATE discovery_source)
        return stats


class SegmentRepositoryImpl(SegmentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_slug(self, slug: str) -> Segment:
        row = (
            await self._s.execute(select(SegmentRow).where(SegmentRow.slug == slug))
        ).scalar_one_or_none()
        if row is None:
            raise SegmentNotFound(slug)
        cfg = SegmentConfig.model_validate(row.config)
        return Segment(id=row.id, **cfg.model_dump())

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        stmt = pg_insert(SegmentRow).values(slug=cfg.slug, name=cfg.name, config=cfg.model_dump())
        stmt = stmt.on_conflict_do_update(
            index_elements=["slug"],
            set_={"name": stmt.excluded.name, "config": stmt.excluded.config},
        )
        row = (await self._s.execute(stmt.returning(SegmentRow))).scalar_one()
        return Segment(id=row.id, **cfg.model_dump())


class CollectRunRepositoryImpl(CollectRunRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, run: CollectRun) -> int:
        row = CollectRunRow(**run.model_dump())
        self._s.add(row)
        await self._s.flush()
        return row.id
```

- [ ] **Step 7: Teste do upsert idempotente (integração — sem mock)**

`tests/infra/test_repositories.py`:

```python
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from content_factory.domain.models import CandidateVideo, SegmentConfig
from content_factory.infra.db.orm import Base
from content_factory.infra.db.repositories import SegmentRepositoryImpl, VideoRepositoryImpl
from tests.conftest import requires_infra

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def candidate(i: int) -> CandidateVideo:
    return CandidateVideo(
        platform="youtube",
        platform_video_id=f"vid{i}",
        url=f"https://youtu.be/vid{i}",
        creator_handle="u",
    )


@requires_infra
async def test_upsert_is_idempotent() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await s.rollback()
        repo = VideoRepositoryImpl(s)
        first = await repo.upsert_candidates(1, "youtube", [candidate(1)])
        second = await repo.upsert_candidates(1, "youtube", [candidate(1)])
        assert first.inserted >= 1 and second.updated >= 1
    await engine.dispose()


@requires_infra
async def test_segment_not_found_raises() -> None:
    from content_factory.domain.errors import SegmentNotFound

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        with pytest.raises(SegmentNotFound):
            await SegmentRepositoryImpl(s).get_by_slug("nao-existe")
    await engine.dispose()
```

> Os testes de integração assumem schema já migrado (`alembic upgrade head`
> no compose de teste) — nunca criam tabelas por conta própria.

- [ ] **Step 8: Rodar gates**

```bash
TEST_DATABASE_URL=… uv run pytest tests/infra -q && uv run alembic check && uv run ruff check . && uv run pyright
```
Expected: integration PASS (tabelas existem), `alembic check` exit 0

- [ ] **Step 9: Commit**

```bash
git add alembic.ini alembic src/content_factory/infra tests && \
git commit -m "feat: schema completo (14 tabelas) com alembic + repositorios

Série temporal em video_metrics/video_scores da conta de velocity e
historico; colunas de replication/production nascem NULL p/ a fase IA."
```

---

### Task 5: Segmentos: YAML → validação → persistência (TDD)

**Files:**
- Create: `segments/candles.yaml`, `src/content_factory/app/segment.py`,
  `tests/app/test_segment.py`
- Modify: `src/content_factory/domain/models.py` (parser do YAML)

**Interfaces:**
- Consumes: `SegmentRepository` (Task 2), `SegmentConfig`/`Segment`
- Produz: `load_segment_file(path: Path) -> SegmentConfig`;
  `upsert_segment(repo, path) -> Segment`

- [ ] **Step 1: Teste falhando**

`tests/app/test_segment.py`:

```python
from pathlib import Path
import pytest
from content_factory.app.segment import load_segment_file, upsert_segment
from content_factory.domain.errors import DomainError


def test_load_candles_yaml() -> None:
    cfg = load_segment_file(Path("segments/candles.yaml"))
    assert cfg.slug == "candles"
    assert "youtube" in cfg.platforms
    assert any("vela" in k for k in cfg.keywords)


def test_invalid_yaml_raises_domain_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("segment:\n  name: x\n")  # sem slug/keywords/platforms
    with pytest.raises(DomainError):
        load_segment_file(bad)
```

- [ ] **Step 2: Rodar e ver falhar**

```bash
uv run pytest tests/app/test_segment.py -v
```
Expected: FAIL — módulo `app.segment` não existe

- [ ] **Step 3: Implementar**

`segments/candles.yaml` (primeiro segmento — velas artesanais):

```yaml
slug: candles
name: Velas artesanais
languages: [pt-BR, en]
platforms: [youtube, tiktok, instagram, web]
content_types: [tutorial, transformation, satisfying, tips, mistakes,
                behind_the_scenes, product, business, storytelling]
keywords:
  - candle making
  - soy candle
  - scented candle
  - candle DIY
  - candle business
  - velas artesanais
  - vela aromática
  - vela de soja
subnichos:
  fabricacao: [cera, pavio, temperatura, moldes]
  decoracao: [minimalista, luxo, temática]
  fragrancias: []
  erros: []
  asmr: []
  empreendedorismo: []
  embalagem: []
  presentes: []
audience: [artesanato, decoração, presentes, empreendedorismo, DIY]
```

`app/segment.py`:

```python
from pathlib import Path
import yaml
from content_factory.app.ports import SegmentRepository
from content_factory.domain.errors import DomainError
from content_factory.domain.models import Segment, SegmentConfig


def load_segment_file(path: Path) -> SegmentConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return SegmentConfig.model_validate(data)
    except Exception as exc:  # ValidationError | yaml error
        raise DomainError(f"segmento inválido em {path}: {exc}") from exc


async def upsert_segment(repo: SegmentRepository, path: Path) -> Segment:
    return await repo.upsert(load_segment_file(path))
```

- [ ] **Step 4: Rodar teste + gates** (esperado: `2 passed`, ruff/pyright 0)

- [ ] **Step 5: Commit**

```bash
git add segments src/content_factory/app tests/app && \
git commit -m "feat: segmento candles em YAML validado por pydantic

Novo segmento = 1 arquivo YAML, sem codigo (criterio de sucesso 3 do PRD)."
```

---

### Task 6: CLI: envelope + exit codes + `segment load`/`status` (TDD)

**Files:**
- Create: `src/content_factory/cli/output.py`, `src/content_factory/cli/main.py`,
  `tests/cli/__init__.py`, `tests/cli/test_output.py`, `tests/cli/test_cli.py`
- Modify: nenhum

**Interfaces:**
- Produz (contrato com agentes — ADR-0004):
  - `output.envelope_ok(command: str, **counts) -> dict` → `{"ok": True, "command": ..., ...}`
  - `output.envelope_err(command: str, code: str, message: str) -> dict`
  - `output.exit_code_for(ok: bool, partial: bool = False) -> int` → `0|2|1`
  - CLI nesta task: `cf segment load <path>` · `cf ping`
    (`collect` na Task 11, `ingest` na 15, `score` na 16, `status` na 17 —
    **não anunciar comando que ainda não existe**)

- [ ] **Step 1: Teste falhando**

`tests/cli/test_output.py`:

```python
from content_factory.cli.output import envelope_err, envelope_ok, exit_code_for


def test_ok_envelope() -> None:
    e = envelope_ok("collect", inserted=10)
    assert e["ok"] is True and e["command"] == "collect" and e["inserted"] == 10


def test_err_envelope() -> None:
    e = envelope_err("collect", code="blocked", message="tiktok bloqueou")
    assert e["ok"] is False
    assert e["error"] == {"code": "blocked", "message": "tiktok bloqueou"}


def test_exit_codes() -> None:
    assert exit_code_for(ok=True, partial=False) == 0
    assert exit_code_for(ok=True, partial=True) == 2
    assert exit_code_for(ok=False, partial=True) == 1
```

`tests/cli/test_cli.py`:

```python
from typer.testing import CliRunner
from content_factory.cli.main import app

runner = CliRunner()


def test_segment_load_missing_file_exit_1() -> None:
    result = runner.invoke(app, ["segment", "load", "nope.yaml"])
    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
```

- [ ] **Step 2: Rodar e ver falhar** (`uv run pytest tests/cli -v` → ImportError)

- [ ] **Step 3: Implementar**

`cli/output.py`:

```python
import json
from typing import Any


def envelope_ok(command: str, **counts: Any) -> dict[str, Any]:
    return {"ok": True, "command": command, **counts}


def envelope_err(command: str, code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "command": command, "error": {"code": code, "message": message}}


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def exit_code_for(ok: bool, partial: bool = False) -> int:
    if not ok:
        return 1
    return 2 if partial else 0
```

`cli/main.py` (comandos de Task 5; `collect`/`score`/`ingest` chegam nas
Tasks 11/16/15 — registar placeholders de sub-app vazios é proibido, então
só comandos que existem):

```python
import sys
from pathlib import Path
import typer
from content_factory.cli.output import dumps, envelope_err, envelope_ok, exit_code_for
from content_factory.domain.errors import DomainError

app = typer.Typer(no_args_is_help=True)
segment_app = typer.Typer()
app.add_typer(segment_app, name="segment")


@segment_app.command("load")
def segment_load(path: Path) -> None:
    """Valida YAML de segmento e grava no banco."""
    try:
        import asyncio

        from sqlalchemy.ext.asyncio import async_sessionmaker
        from content_factory.app.segment import load_segment_file, upsert_segment
        from content_factory.config import get_settings
        from content_factory.infra.db.base import get_session_factory
        from content_factory.infra.db.repositories import SegmentRepositoryImpl

        cfg = load_segment_file(path)  # validação primeiro — sem DB na frente
        factory: async_sessionmaker[AsyncSession] = get_session_factory(get_settings())

        async def _save() -> int:
            async with factory() as s:
                seg = await upsert_segment(SegmentRepositoryImpl(s), path)
                await s.commit()
                return seg.id

        seg_id = asyncio.run(_save())
        print(dumps(envelope_ok("segment.load", slug=cfg.slug, id=seg_id)))
        raise typer.Exit(0)
    except DomainError as exc:
        print(dumps(envelope_err("segment.load", "invalid_segment", str(exc))))
        raise typer.Exit(1) from exc


@app.command("ping")
def ping() -> None:
    """Healthcheck do CLI (sem banco)."""
    print(dumps(envelope_ok("ping")))
    raise typer.Exit(0)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

> Persistência real: o E2E `01` exige `segment load` → `id > 0` no banco
> (spec §12.1) — wire completo aqui, não na Task 17. O teste de arquivo
> inexistente falha na validação **antes** de tocar o DB (sem exceção de
> conexão). `AsyncSession` entra no import de `sqlalchemy.ext.asyncio`.

`[project.scripts]` no pyproject: `cf = "content_factory.cli.main:main"`.

- [ ] **Step 4: Rodar gates** (esperado: `3 passed`; `uv run cf ping` → envelope ok)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/content_factory/cli tests/cli && \
git commit -m "feat: CLI cf com envelope JSON e exit codes 0/2/1

Contrato com agentes de AI: so status em JSON; dados ficam no banco (ADR-0004)."
```

---

### Task 7: Docker: compose único + Dockerfile multi-stage + devcontainer + secrets

**Files:**
- Create: `.devcontainer/docker-compose.yml`, `.devcontainer/Dockerfile`,
  `.devcontainer/devcontainer.json`, `.devcontainer/env/defaults.env`,
  `.devcontainer/env/app.env.example`, `.devcontainer/secrets/README.md`
  (+ `secrets/secrets.env.example`), `.devcontainer/readme.md`, `.dockerignore` (ajustar)
- Modify: `.gitignore` (ignorar `.devcontainer/env/app.env`, `.devcontainer/secrets/*.env`)

**Interfaces:**
- Produz: stack `content-factory-net` com serviços `python` (devcontainer),
  `worker`, `postgres` (pgvector/pg16), `redis`; porta Postgres só via
  `127.0.0.1:5432` em dev; `TEST_DATABASE_URL` derivado

- [ ] **Step 1: `.devcontainer/docker-compose.yml`** (padrão da casa, 1 arquivo)

```yaml
name: content-factory
networks: { content-factory-net: {} }
services:
  python:
    container_name: cf-python
    build:
      context: ..
      dockerfile: .devcontainer/Dockerfile
      target: dev
    volumes:
      - ..:/app
      - ${HOME}/.gitconfig:/home/appuser/.gitconfig:ro
      - ${HOME}/.ssh:/home/appuser/.ssh:ro
    env_file:
      - ./env/app.env
    environment:
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-contentfactory}:${POSTGRES_PASSWORD:-localdev}@postgres:5432/${POSTGRES_DB:-contentfactory}
      REDIS_URL: redis://redis:6379/0
    depends_on: [postgres, redis]
    networks: [content-factory-net]
    command: sleep infinity

  worker:
    build:
      context: ..
      dockerfile: .devcontainer/Dockerfile
      target: worker
    env_file: [./env/app.env]
    environment:
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-contentfactory}:${POSTGRES_PASSWORD:-localdev}@postgres:5432/${POSTGRES_DB:-contentfactory}
      REDIS_URL: redis://redis:6379/0
    depends_on: [postgres, redis]
    networks: [content-factory-net]
    profiles: ["worker"]

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-contentfactory}
      POSTGRES_DB: ${POSTGRES_DB:-contentfactory}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-localdev}
    ports: ["127.0.0.1:5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    networks: [content-factory-net]

  redis:
    image: redis:7-alpine
    networks: [content-factory-net]

volumes: { pgdata: {} }
```

- [ ] **Step 2: `.devcontainer/Dockerfile` multi-stage**

```dockerfile
FROM python:3.14-slim AS base
ENV PYTHONUNBUFFERED=1 UV_INSTALL_DIR=/usr/local/bin
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS dev
RUN uv sync --frozen --all-groups --no-install-project
RUN useradd -m appuser && chown -R appuser /app
USER appuser

FROM dev AS worker
USER root
RUN uv pip install --system playwright && playwright install --with-deps chromium
USER appuser
```

- [ ] **Step 3: `devcontainer.json` (sem features — lição da casa)**

```jsonc
{
  "name": "content-factory",
  "dockerComposeFile": "./docker-compose.yml",
  "service": "python",
  "workspaceFolder": "/app",
  "remoteUser": "appuser",
  "postCreateCommand": "uv sync --all-groups && uv run pre-commit install",
  "customizations": { "vscode": { "extensions": ["ms-python.python", "charliermarsh.ruff"] } }
}
```

- [ ] **Step 4: env 2 camadas + secrets + readme da stack**

- `.devcontainer/env/defaults.env` (versionado, não sensível):
  `RATE_LIMIT_YOUTUBE_PER_DAY=10000` · `RATE_LIMIT_TIKTOK_PER_MIN=6` · `COLLECT_CRON_HOURS=08,20`
- `.devcontainer/env/app.env.example` (versionado) e `env/app.env` (gitignored):
  mesmas chaves de `Settings` com valores vazios
- `.devcontainer/secrets/secrets.env.example` (versionado):
  `YOUTUBE_API_KEY=` · `INSTAGRAM_ACCESS_TOKEN=` · `IG_USER_ID=` · `TIKTOK_MS_TOKEN=`
- `.devcontainer/secrets/*.env` gitignored; `env_file` do worker inclui
  `./secrets/secrets.env` (montado, fora do git)
- `.devcontainer/readme.md`: tabela `| Componente | Versão | POR QUÊ |`
  (python:3.14-slim, pgvector/pgvector:pg16→ADR-0001, redis:7-alpine→ADR-0002,
  playwright→ADR-0003)
- Bootstrap local (sem isso o `env_file` do compose quebra — roda **antes**
  do Step 5; gitignored):

```bash
cp .devcontainer/env/app.env.example .devcontainer/env/app.env
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env
```

- [ ] **Step 5: Gate — stack sobe e CLI responde**

```bash
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
docker compose -f .devcontainer/docker-compose.yml exec python uv run cf ping
```
Expected: `{"ok": true, "command": "ping", ...}`

- [ ] **Step 6: Commit**

```bash
git add .devcontainer .dockerignore .gitignore && \
git commit -m "feat: compose unico (padrao casa) com 1 container por responsabilidade

infra (pgvector, redis) em rede interna; worker multi-stage com chromium;
secrets gitignored com so exemplos versionados."
```

---

### Task 8: CI: quality + docs + teste de privacidade

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/docs.yml`,
  `scripts/check_privacy.py`, `tests/cli/test_privacy_script.py`
- Modify: `Makefile` (target `privacy`)

**Interfaces:**
- Produz: gates CI = DoD (spec §10); `scripts/check_privacy.py` exit 1 se
  segredo/path pessoal em arquivo rastreado

- [ ] **Step 1: Teste do guard de privacidade (é código — TDD)**

`tests/cli/test_privacy_script.py`:

```python
import subprocess, sys
from pathlib import Path


def test_privacy_script_passes_on_repo() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/check_privacy.py"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Rodar e ver falhar** (`uv run pytest tests/cli/test_privacy_script.py -v` → arquivo não existe)

- [ ] **Step 3: `scripts/check_privacy.py`**

```python
"""Falha se segredo/key/path pessoal aparecer em arquivo rastreado."""

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "chave privada"),
    (
        r"(?i)(?:api[_-]?key|secret|password|token)['\"]?\s*[=:]\s*['\"]?"
        r"([A-Za-z0-9_\-]{16,})",
        "credencial literal",
    ),
    (r"/home/[a-z]+/", "path pessoal"),
]
# valor placeholder (YOUR_..., ..._HERE) não é segredo
PLACEHOLDER = re.compile(r"(?i)^(?:your[_-]|.*[_-]here$)")
ALLOWLIST = {
    ".env.example",
    "check_privacy.py",
    "secrets.env.example",
    "app.env.example",
    "2026-10-07-content-factory-design.md",
}


def _hit(pattern: str, text: str) -> bool:
    for match in re.finditer(pattern, text):
        if match.lastindex and PLACEHOLDER.search(match.group(match.lastindex)):
            continue
        return True
    return False


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    bad = []
    for name in files:
        if Path(name).name in ALLOWLIST or name.endswith((".png", ".svg", ".lock")):
            continue
        try:
            text = Path(name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in PATTERNS:
            if _hit(pattern, text):
                bad.append(f"{name}: {label}")
    if bad:
        print("PRIVACIDADE FALHOU:\n" + "\n".join(bad))
        return 1
    print(f"privacidade ok ({len(files)} arquivos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> Roda **antes** de `git add` em toda task (a permissão de commit é do
> hook de privacidade): se o teste falhar localmente, o commit não sai.

- [ ] **Step 4: `.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  quality:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_USER: contentfactory, POSTGRES_DB: contentfactory,
               POSTGRES_PASSWORD: ci }
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U contentfactory"
          --health-interval 5s --health-timeout 5s --health-retries 10
      redis:
        image: redis:7-alpine
        options: >-
          --health-cmd "redis-cli ping" --health-interval 5s
          --health-timeout 5s --health-retries 10
    env:
      TEST_DATABASE_URL: postgresql+psycopg://contentfactory:ci@localhost/contentfactory
      REDIS_URL: redis://localhost:6379/0
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with: { enable-cache: true }
      - run: uv sync --all-groups
      - run: uv run ruff check . && uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest -q -m "not e2e"
      - run: uv run alembic upgrade head && uv run alembic check
      - run: uv run bandit -q -c pyproject.toml
      - run: uv run pip-audit
      - run: uv run python scripts/check_privacy.py
```

- [ ] **Step 5: `.github/workflows/docs.yml`**

```yaml
name: docs
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --only-group docs
      - run: uv run --only-group docs mkdocs build --strict
```

- [ ] **Step 6: Rodar gates locais** (pytest privacidade + ruff + pyright) → commit

```bash
uv run pytest tests/cli/test_privacy_script.py -q && uv run ruff check . && \
git add .github scripts Makefile tests && \
git commit -m "ci: quality+docs com services pgvector/redis e teste de privacidade

CI = DoD (spec 10): mesmos gates locais, sem segredo em arquivo rastreado."
```

---

### Task 9: Docs: mkdocs + architecture + dev + data-model/diagram

**Files:**
- Create: `mkdocs.yml`, `docs/index.md`, `docs/architecture/README.md`,
  `docs/architecture/estado-e-proximas-fases.md`, `docs/dev/index.md`,
  `docs/dev/dev-environment.md`, `docs/dev/extending.md`,
  `docs/dev/credentials.md`, `docs/data-model/README.md`,
  `docs/specs/trd-collectors.md`, `docs/specs/trd-scoring.md`,
  `docs/specs/trd-cli.md`,
  `scripts/gen_datamodel.py`, `scripts/gen_diagram.py`
- Modify: `docs/README.md` (já é o índice — linkar novas páginas)

**Interfaces:**
- Produz: `make docs` (`mkdocs build --strict`) verde; data-model/diagram
  **gerados por script** (nunca à mão) a partir do metadata do Alembic;
  TRDs das 3 features (spec §11 exige `specs/` = TRD por feature);
  `dev/credentials.md` cobre rotação de tokens + conta secundária TikTok
  (spec §7)

- [ ] **Step 1: `mkdocs.yml`**

```yaml
site_name: content-factory
theme: { name: material }
plugins: [search, mermaid2]
markdown_extensions:
  - admonition
  - tables
  - pymdownx.superfences:
      custom_fences: [{ name: mermaid, class: mermaid, format: !!python/name:pymdownx.superfences.fence_code_format }]
nav:
  - Início: index.md
  - Roteamento: roteamento.md
  - Produto:
      - Ideia inicial (verbatim): produto/ideia-inicial.md
      - PRD: produto/prd.md
  - Specs:
      - Design: specs/2026-10-07-content-factory-design.md
      - TRD coletores: specs/trd-collectors.md
      - TRD scoring: specs/trd-scoring.md
      - TRD CLI: specs/trd-cli.md
  - ADRs:
      - 0001 Postgres+pgvector: adr/0001-postgres-pgvector.md
      - 0002 Redis+arq: adr/0002-redis-arq-fila.md
      - 0003 Coletores: adr/0003-coletores-fontes.md
      - 0004 CLI-first: adr/0004-cli-first.md
  - Arquitetura:
      - Visão geral: architecture/README.md
      - Estado e fases: architecture/estado-e-proximas-fases.md
  - Data model: data-model/README.md
  - Diagramas: diagram/README.md
  - Dev:
      - Índice: dev/index.md
      - Ambiente: dev/dev-environment.md
      - Estender: dev/extending.md
      - Credenciais: dev/credentials.md
  - Planos: ai-plans/2026-10-07-fundacao-coletores-banco.md
  - HANDOFF: HANDOFF.md
```

> Nav sempre com **arquivos**, nunca diretórios — mkdocs `--strict`
> reprova entrada de diretório e pune página órfã com warning.

- [ ] **Step 2: `docs/index.md`** (visão + mermaid das camadas) e páginas de
  `architecture/` (estado atual + próximas fases com o diagrama do spec §4),
  `dev/dev-environment.md` (compose, TEST_DATABASE_URL, gates),
  `dev/extending.md` (**novo segmento = YAML** · **novo collector = classe +
  registro + ADR se for plataforma nova**),
  `dev/credentials.md` (rotação de tokens YouTube/IG com escopo mínimo +
  conta secundária pro TikTok + onde ficam os segredos — spec §7),
  e os 3 TRDs (`specs/trd-*.md`): por feature, no template de TRD da skill
  (`referencias/documentacao.md`), cobrindo exatamente o que as tasks
  12–16 entregam (endpoints/mocks/erros por collector; fórmula do score;
  contrato da CLI).

- [ ] **Step 3: scripts geradores**

`scripts/gen_datamodel.py`: lê `Base.metadata` via engine
(`TEST_DATABASE_URL`), escreve `docs/data-model/README.md` com
`### tabela` + colunas/tipos/notas (FK declarada marcada);
`scripts/gen_diagram.py`: mermaid ER a partir do mesmo metadata →
`docs/diagram/er.mmd` + `.html` + `.svg` + `.png` (spec §11) + `README.md`
com links (entrypoint do nav "Diagramas"). `.svg/.png` via
`npx -y @mermaid-js/mermaid-cli` quando `npx` existir; se não, o script
**falha com mensagem clara** (não degrada silenciosamente — gate é gate).
Gate: `alembic check` antes de gerar.

- [ ] **Step 4: Gate**

```bash
uv run python scripts/gen_datamodel.py && uv run python scripts/gen_diagram.py && \
uv run --only-group docs mkdocs build --strict
```
Expected: `Documentation built in …` sem warnings

- [ ] **Step 5: Commit**

```bash
git add mkdocs.yml docs scripts && \
git commit -m "docs: mkdocs strict + data-model/diagram gerados do schema real

Nada de doc gerado editado na mão; gate alembic check antes de gerar."
```

---

### Task 10: Fronteira de coleta: registry + FakeCollector (TDD)

**Files:**
- Create: `src/content_factory/infra/collectors/registry.py`,
  `tests/infra/collectors/__init__.py`, `tests/infra/collectors/test_registry.py`
- Modify: `src/content_factory/app/ports.py` (se precisar de
  `list_collectors(platforms) -> list[Collector]`)

**Interfaces:**
- Consumes: `Collector` (Task 2), `CandidateVideo`, `Segment`
- Produz: `registry.register(collector)`, `registry.get(platform) -> Collector`,
  `registry.resolve(platforms: list[str]) -> list[Collector]` — **ponto único
  de extensão** (Task 12–15 registram aqui)

- [ ] **Step 1: Teste falhando**

```python
from content_factory.infra.collectors import registry
from content_factory.domain.errors import DomainError


def test_register_and_resolve() -> None:
    class Fake:
        platform = "fake"

        async def discover(self, segment, limit):
            return []

    registry.register(Fake())
    assert registry.get("fake").platform == "fake"
    assert [c.platform for c in registry.resolve(["fake"])] == ["fake"]


def test_unknown_platform_raises() -> None:
    with pytest.raises(DomainError):
        registry.get("desconhecida")
```

- [ ] **Step 2: Falha** → **Step 3: implementar** (`_REGISTRY: dict[str, Collector]`,
  `register` sobrescreve com aviso, `resolve` itera platforms na ordem) →
  **Step 4: gates** (`2 passed`) → **Step 5: commit**

```bash
git commit -m "feat: registry de collectors (ponto de extensao por plataforma)"
```

---

### Task 11: Use-case `collect` + `collect_runs` + rate-limit/lock Redis (TDD)

**Files:**
- Create: `src/content_factory/app/collect.py`, `src/content_factory/infra/ratelimit.py`,
  `tests/app/test_collect.py`
- Modify: `src/content_factory/cli/main.py` (adicionar comando `collect`)

**Interfaces:**
- Consumes: `Collector`, `VideoRepository`, `SegmentRepository`,
  `CollectRunRepository`, `RateLimiter`, `Lock` (Task 2)
- Produz: `async def collect(segment_slug: str, platforms: list[str],
  limit: int, deps: CollectDeps) -> CollectOutcome` com
  `CollectOutcome(ok, partial, counts: dict[str, int | bool], runs: list[CollectRun])`
  — CLI mapeia direto p/ envelope + exit code (Task 6)

- [ ] **Step 1: Teste falhando (núcleo com fakes, sem Redis/banco)**

```python
import asyncio
from content_factory.app.collect import CollectDeps, collect
from content_factory.domain.errors import CollectorError
from tests.stubs import FakeCollector, FakeLimiter, FakeLock, FakeRepos  # stubs/test doubles puros


async def test_collect_partial_failure_marks_partial() -> None:
    good = FakeCollector("youtube", n=3)
    bad = FakeCollector("tiktok", fail=CollectorError("blocked", "tiktok"))
    deps = CollectDeps(
        collectors=[good, bad],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=FakeLock(),
    )
    out = await collect("candles", ["youtube", "tiktok"], limit=10, deps=deps)
    assert out.ok is False and out.partial is True
    assert out.counts["inserted"] == 3
    run_bad = [r for r in out.runs if r.collector == "tiktok"][0]
    assert run_bad.status in {"blocked", "failed"} and run_bad.error_code == "blocked"


async def test_lock_prevents_second_run() -> None:
    deps = CollectDeps(
        collectors=[FakeCollector("youtube")],
        videos=FakeRepos().videos,
        segments=FakeRepos().segments,
        runs=FakeRepos().runs,
        limiter=FakeLimiter(),
        lock=FakeLock(acquired=False),
    )
    out = await collect("candles", ["youtube"], limit=5, deps=deps)
    assert out.ok is False and out.counts == {"ja_em_execucao": True}
```

(`tests/stubs.py` com doubles: `FakeCollector(platform, n, fail)` devolve
`CandidateVideo`s; `FakeLock(acquired=True)` — e **todos** os doubles
implementam o **Protocolo inteiro** da Task 2 (métodos não usados levantam
`NotImplementedError`): pyright strict valida estrutura dos testes também.)

- [ ] **Step 2: Falha** → **Step 3: implementar `app/collect.py`**

```python
from datetime import UTC, datetime
from dataclasses import dataclass, field
from content_factory.app.ports import (
    Collector,
    CollectRunRepository,
    Lock,
    RateLimiter,
    SegmentRepository,
    VideoRepository,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import CollectRun


@dataclass
class CollectDeps:
    collectors: list[Collector]
    videos: VideoRepository
    segments: SegmentRepository
    runs: CollectRunRepository
    limiter: RateLimiter
    lock: Lock


@dataclass
class CollectOutcome:
    ok: bool
    partial: bool
    counts: dict[str, int | bool]  # bool só no caso {"ja_em_execucao": True}
    runs: list[CollectRun] = field(default_factory=list)


async def collect(
    segment_slug: str, platforms: list[str], limit: int, deps: CollectDeps
) -> CollectOutcome:
    if not await deps.lock.acquire_once(f"collect:{segment_slug}"):
        return CollectOutcome(ok=False, partial=False, counts={"ja_em_execucao": True})
    try:
        segment = await deps.segments.get_by_slug(segment_slug)
        counts: dict[str, int | bool] = {"found": 0, "inserted": 0, "updated": 0, "failed": 0}
        runs: list[CollectRun] = []
        errors = 0
        for collector in deps.collectors:
            if collector.platform not in platforms:
                continue
            started = datetime.now(UTC)
            run = CollectRun(
                collector=collector.platform, segment_id=segment.id, started_at=started
            )
            try:
                await deps.limiter.acquire(collector.platform)
                found = await collector.discover(segment, limit)
                stats = await deps.videos.upsert_candidates(segment.id, collector.platform, found)
                run.status = "ok"
                run.found = len(found)
                run.inserted, run.updated = stats.inserted, stats.updated
                counts["found"] = int(counts["found"]) + len(found)
                counts["inserted"] = int(counts["inserted"]) + stats.inserted
                counts["updated"] = int(counts["updated"]) + stats.updated
            except CollectorError as exc:
                run.status = "blocked" if exc.code == "blocked" else "failed"
                run.error_code = exc.code
                errors += 1
                counts["failed"] = int(counts["failed"]) + 1
            finally:
                run.finished_at = datetime.now(UTC)
                await deps.runs.record(run)
                runs.append(run)
        ok = errors == 0
        return CollectOutcome(
            ok=ok,
            partial=(not ok and (int(counts["inserted"]) + int(counts["updated"]) > 0)),
            counts=counts,
            runs=runs,
        )
    finally:
        # SEM release o lock eterno bloqueia a próxima execução — bug clássico
        await deps.lock.release(f"collect:{segment_slug}")
```

- [ ] **Step 4: `infra/ratelimit.py` + `infra/redact.py`** —
  **rate-limit/token bucket Redis** (`redis.asyncio`; janelas: `youtube`
  cota diária via `INCR`+`EXPIRE 86400`; `tiktok` `6/min`; `instagram`
  janela 60 s; backoff com jitter ±20%) e **circuit breaker por plataforma**
  (3 falhas consecutivas → estado `blocked` por 15 min no Redis →
  `CollectorError("blocked")` imediato sem bater na API — spec §7;
  contador zera no primeiro sucesso). Usados pelos collectors; testes
  unitários mockam `FakeLimiter`/`FakeLock`.

  **`infra/redact.py`** (spec §7 — redação de logs): função
  `redact(text: str) -> str` que mascara ocorrências de `sessionid`,
  `ms_token`, `access_token`, `cookie`/`Set-Cookie` (valor após o padrão →
  `***`); **todo log de collector passa por `redact`** — teste
  `test_redact_masks_tokens()`.

> `Lock` ganha `release(key)` no Protocol (Task 2) — o teste
> `test_lock_prevents_second_run` do passo acima cobre o `acquire_once`;
> adicionar `test_lock_is_released_after_run()` garantindo que a segunda
> chamada de `collect` **depois** de uma execução bem-sucedida adquire o
> lock de novo.

- [ ] **Step 5: CLI `collect`** — com `--segment/--platform/--limit`,
  envolve `asyncio.run(collect(...))`, traduz `CollectOutcome` → envelope
  (exit 0/2/1). Teste de CLI: `runner.invoke(app, ["collect", …])` com deps
  fake via monkeypatch → exit 2 em falha parcial.

- [ ] **Step 6: Gates + commit**

```bash
uv run pytest -q -m "not integration and not e2e" && uv run ruff check . && uv run pyright
git add src tests && git commit -m "feat: use-case collect com audit em collect_runs, rate-limit e lock

Falha de uma plataforma nao derruba as demais (exit 2); lock impede run
concorrente do mesmo segmento (anti-padrao de bot)."
```

---

### Task 12: Collector YouTube (TDD com fixtures)

**Files:**
- Create: `src/content_factory/infra/collectors/youtube.py`,
  `src/content_factory/infra/collectors/__init__.py` (`register_defaults`),
  `tests/infra/collectors/conftest.py`, `tests/infra/collectors/test_youtube.py`,
  `tests/fixtures/youtube_search.json`, `tests/fixtures/youtube_videos.json`,
  `tests/fixtures/youtube_channels.json`
- Modify: `registry` (via `register_defaults` — ponto único de wire)

**Interfaces:**
- Consome: `Collector.discover(segment, limit)`, `Settings.youtube_api_key`,
  cota contada no próprio collector (`quota_used`: search=100, videos=1, channels=1)
- Produz: `YoutubeCollector(platform="youtube")` — candidatos com
  views/likes/comments reais (shares/saves = `None`, inexistentes na API);
  `register_defaults(settings)` registra os 4 coletores — CLI/worker chamam
  **uma vez** no entrypoint

- [ ] **Step 1: Fixtures** (respostas reais reduzidas da Data API v3 — gravar
  1× com chave real e anonimizar ids) em `tests/fixtures/*.json`:

`youtube_search.json`:
```json
{"items": [{"id": {"videoId": "abc123"},
  "snippet": {"title": "Como fazer vela aromática em casa",
    "description": "tutorial passo a passo de velas de soja",
    "channelId": "UC1", "channelTitle": "Velas Lab",
    "publishedAt": "2026-10-01T10:00:00Z",
    "thumbnails": {"default": {"url": "https://i.ytimg.com/vi/abc123/default.jpg"}}}}]}
```

`youtube_videos.json`:
```json
{"items": [{"id": "abc123",
  "statistics": {"viewCount": "120000", "likeCount": "4500", "commentCount": "210"},
  "contentDetails": {"duration": "PT8M12S"}}]}
```

`youtube_channels.json`:
```json
{"items": [{"id": "UC1", "statistics": {"subscriberCount": "54000"}}]}
```

- [ ] **Step 2: Transport mock + teste falhando**

`tests/infra/collectors/conftest.py`:

```python
import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _route(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/search" in url:
        return httpx.Response(200, content=(FIXTURES / "youtube_search.json").read_bytes())
    if "/videos" in url:
        return httpx.Response(200, content=(FIXTURES / "youtube_videos.json").read_bytes())
    if "/channels" in url:
        return httpx.Response(200, content=(FIXTURES / "youtube_channels.json").read_bytes())
    return httpx.Response(404, json={})


@pytest.fixture
def httpx_mock_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_route)
```

`tests/infra/collectors/test_youtube.py`:

```python
import httpx
import pytest
from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.youtube import YoutubeCollector

SEG = SegmentConfig(
    slug="candles", name="Velas", platforms=["youtube"], keywords=["candle making", "vela de soja"]
)


@pytest.mark.asyncio
async def test_discover_maps_fixture(httpx_mock_transport: httpx.MockTransport) -> None:
    c = YoutubeCollector(api_key="k", transport=httpx_mock_transport)
    out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "youtube"
    assert out[0].views is not None and out[0].url.startswith("https://")
    assert out[0].duration_s == 492  # PT8M12S
    assert out[0].creator_followers == 54000
    assert c.quota_used == 102  # 100 search + 1 videos + 1 channels


@pytest.mark.asyncio
async def test_quota_403_becomes_rate_limited() -> None:
    from content_factory.domain.errors import RateLimited

    def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "quota"}})

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_boom))
    with pytest.raises(RateLimited):
        await c.discover(SEG, limit=5)
```

- [ ] **Step 3: Rodar e ver falhar** (`uv run pytest tests/infra/collectors/test_youtube.py -v`
  → ImportError `content_factory.infra.collectors.youtube`)

- [ ] **Step 4: Implementar**

`infra/collectors/youtube.py`:

```python
import re
from datetime import UTC, datetime, timedelta

import httpx

from content_factory.domain.errors import NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.models import CandidateVideo, SegmentConfig

API = "https://www.googleapis.com/youtube/v3"
DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _duration_seconds(iso: str) -> int | None:
    m = DURATION_RE.fullmatch(iso)
    if not m:
        return None
    h, mn, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mn * 60 + s


class YoutubeCollector:
    platform = "youtube"
    COST_SEARCH, COST_LIST = 100, 1

    def __init__(
        self, api_key: str, transport: httpx.AsyncBaseTransport | None = None, quota_used: int = 0
    ) -> None:
        self._key = api_key
        self._client = httpx.AsyncClient(transport=transport, timeout=20)
        self.quota_used = quota_used

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, object]:
        try:
            r = await self._client.get(f"{API}/{path}", params={**params, "key": self._key})
        except httpx.HTTPError as exc:
            raise NetworkError(self.platform, str(exc)) from exc
        if r.status_code == 403:
            raise RateLimited(self.platform, "quota ou origem bloqueada")
        if r.status_code >= 400:
            raise UpstreamChanged(self.platform, f"HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as exc:
            raise UpstreamChanged(self.platform, "resposta não-JSON") from exc
        if not isinstance(data, dict):
            raise UpstreamChanged(self.platform, "JSON com shape inesperado")
        return data

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = " OR ".join(segment.keywords[:5])
        after = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        search = await self._get(
            "search",
            {
                "part": "snippet",
                "q": q,
                "type": "video",
                "order": "viewCount",
                "publishedAfter": after,
                "maxResults": str(min(limit, 50)),
            },
        )
        self.quota_used += self.COST_SEARCH
        items = search.get("items")
        if not isinstance(items, list):
            raise UpstreamChanged(self.platform, "search sem items")
        ids = [
            i["id"]["videoId"]
            for i in items
            if isinstance(i, dict) and i.get("id", {}).get("videoId")
        ]
        if not ids:
            return []
        stats = await self._get(
            "videos",
            {
                "part": "statistics,snippet,contentDetails",
                "id": ",".join(ids[:50]),
            },
        )
        self.quota_used += self.COST_LIST
        stats_by_id = {v["id"]: v for v in stats.get("items", [])}
        channels = await self._get(
            "channels",
            {
                "part": "statistics",
                "id": ",".join({v["snippet"]["channelId"] for v in stats_by_id.values()}),
            },
        )
        self.quota_used += self.COST_LIST
        followers = {
            c["id"]: int(c["statistics"].get("subscriberCount", 0))
            for c in channels.get("items", [])
        }
        return [self._map(v, followers) for v in stats_by_id.values()]

    def _map(self, v: dict, followers: dict[str, int]) -> CandidateVideo:
        sn, st = v["snippet"], v.get("statistics", {})
        vid = v["id"]
        return CandidateVideo(
            platform=self.platform,
            platform_video_id=vid,
            url=f"https://www.youtube.com/watch?v={vid}",
            creator_handle=sn.get("channelTitle", ""),
            creator_followers=followers.get(sn.get("channelId", "")),
            title=sn.get("title", ""),
            description=sn.get("description", ""),
            published_at=datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
            duration_s=_duration_seconds(v.get("contentDetails", {}).get("duration", "")),
            views=int(st.get("viewCount", 0)),
            likes=int(st.get("likeCount", 0)),
            comments=int(st.get("commentCount", 0)),
            shares=None,
            saves=None,  # a API não expõe (ADR-0003)
            thumbnail_url=sn.get("thumbnails", {}).get("default", {}).get("url"),
            raw=v,
        )
```

`infra/collectors/__init__.py` (wire único — cresce nas tasks 13–15):

```python
from content_factory.config import Settings
from content_factory.infra.collectors.registry import register
from content_factory.infra.collectors.youtube import YoutubeCollector


def register_defaults(settings: Settings) -> None:
    register(YoutubeCollector(api_key=settings.youtube_api_key))
    # Task 13: register(TikTokCollector(...))
    # Task 14: register(InstagramCollector(...))
    # Task 15: register(WebCollector())
```

- [ ] **Step 5: Gates + commit**

```bash
uv run pytest tests/infra/collectors -q && uv run ruff check . && uv run pyright
git add src tests && git commit -m "feat: collector YouTube (Data API v3) com contagem de cota"
```

---

### Task 13: Collector TikTok (TDD com fixtures)

**Files:**
- Create: `src/content_factory/infra/collectors/tiktok.py`,
  `tests/infra/collectors/test_tiktok.py`, `tests/fixtures/tiktok_trending.json`
- Modify: `infra/collectors/__init__.py` (`register_defaults`)

**Interfaces:**
- Consome: `TikTokApi` (v6 — biblioteca por trás do Protocol, ADR-0003),
  `Settings.tiktok_ms_token`/`tiktok_proxy`
- Produz: `TikTokCollector(platform="tiktok", ms_token, proxy=None,
  client=None)` — `client` injetável (testes passam stub; produção cria a
  sessão Playwright real). Descoberta: **trending + hashtag
  `segment.keywords[0]`** (spec §6). `ms_token` vazio → `AuthExpired`;
  `EmptyResponseException` → backoff 2^attempt ±jitter ×5 →
  `CollectorError("blocked", "tiktok")`; **fingerprints: usar a UA/`
  viewport que a própria biblioteca gera** (não forjar — UA coerente é
  condição anti-bloqueio da spec §7)

- [ ] **Step 1: Fixture** `tests/fixtures/tiktok_trending.json` (lista no
  shape do `as_dict()`):

```json
[{"id": "7301234567890123456", "desc": "vela de soja derretendo satisfactoria #candle",
  "createTime": 1759700000, "duration": 14,
  "author": {"uniqueId": "velaslab", "nickname": "Velas Lab"},
  "stats": {"playCount": 250000, "diggCount": 18000,
            "commentCount": 420, "shareCount": 95},
  "video": {"cover": "https://cdn.example/cover.jpg"}}]
```

- [ ] **Step 2: Teste falhando**

`tests/infra/collectors/test_tiktok.py`:

```python
import json
from pathlib import Path

import pytest
from content_factory.domain.errors import AuthExpired, CollectorError
from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.tiktok import TikTokCollector

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "tiktok_trending.json").read_text()
)
SEG = SegmentConfig(slug="candles", name="Velas", platforms=["tiktok"], keywords=["candle making"])


class StubTrending:
    def __init__(self, payload: list[dict]) -> None:
        self._p = payload

    async def videos(self, count: int = 30, **kw: object) -> list[dict]:
        return self._p


class StubHashtag:
    def __init__(self, payload: list[dict]) -> None:
        self._p = payload

    async def videos(self, count: int = 30, **kw: object) -> list[dict]:
        return self._p


class StubClient:
    def __init__(self, payload: list[dict]) -> None:
        self.trending = StubTrending(payload)
        self.hashtag = lambda name: StubHashtag(payload)


@pytest.mark.asyncio
async def test_discover_maps_fixture() -> None:
    c = TikTokCollector(ms_token="tok", client=StubClient(FIXTURE))
    out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "tiktok"
    assert out[0].views == 250000 and out[0].likes == 18000
    assert out[0].url.endswith(FIXTURE[0]["id"])
    assert out[0].published_at is not None  # epoch → UTC


@pytest.mark.asyncio
async def test_missing_token_raises_auth_expired() -> None:
    c = TikTokCollector(ms_token="", client=StubClient(FIXTURE))
    with pytest.raises(AuthExpired):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_empty_response_5x_becomes_blocked() -> None:
    from TikTokApi.exceptions import EmptyResponseException

    class BoomTrending:
        async def videos(self, **kw: object) -> list[dict]:
            raise EmptyResponseException()

    class BoomClient:
        trending = BoomTrending()
        hashtag = lambda name: BoomTrending()

    c = TikTokCollector(ms_token="tok", client=BoomClient())
    with pytest.raises(CollectorError) as ei:
        await c.discover(SEG, limit=5)
    assert ei.value.code == "blocked"
```

- [ ] **Step 3: Rodar e ver falhar**
  (`uv run pytest tests/infra/collectors/test_tiktok.py -v` → ImportError)

- [ ] **Step 4: Implementar** `infra/collectors/tiktok.py`:

```python
import asyncio
import random
from datetime import UTC, datetime
from typing import Any

from content_factory.domain.errors import AuthExpired, CollectorError
from content_factory.domain.models import CandidateVideo, SegmentConfig

MAX_ATTEMPTS = 5


class TikTokCollector:
    platform = "tiktok"

    def __init__(self, ms_token: str, proxy: str | None = None, client: Any | None = None) -> None:
        self._token = ms_token
        self._proxy = proxy
        self._client = client

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        if not self._token:
            raise AuthExpired(
                self.platform, "TIKTOK_MS_TOKEN ausente/expirado — ver docs/dev/credentials.md"
            )
        client = self._client or await self._open_session()
        videos = await self._call_with_backoff(client.trending, count=min(limit * 2, 60))
        if segment.keywords:
            videos += await self._call_with_backoff(
                client.hashtag(segment.keywords[0]), count=min(limit, 30)
            )
        seen: set[str] = set()
        out = []
        for v in videos:
            if v.get("id") not in seen:
                seen.add(v.get("id", ""))
                out.append(self._map(v))
        return out[:limit]

    async def _call_with_backoff(self, endpoint: Any, count: int) -> list[dict]:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await endpoint.videos(count=count)
            except Exception as exc:  # noqa: BLE001 — fronteira classifica depois
                from TikTokApi.exceptions import EmptyResponseException

                if not isinstance(exc, EmptyResponseException):
                    raise
                if attempt == MAX_ATTEMPTS - 1:
                    raise CollectorError(
                        "blocked", self.platform, f"sem resposta após {MAX_ATTEMPTS} tentativas"
                    ) from exc
                await asyncio.sleep(2**attempt + random.uniform(0, 2))
        return []

    async def _open_session(self) -> Any:
        from TikTokApi import TikTokApi

        api = TikTokApi(proxy=self._proxy)
        # create_sessions gera UA/viewport reais (fingerprint coerente — spec §7)
        await api.create_sessions(ms_tokens=[self._token], num_sessions=1, headless=True)
        return api

    def _map(self, v: dict[str, Any]) -> CandidateVideo:
        author = v.get("author", {})
        stats = v.get("stats", {})
        handle = author.get("uniqueId", "")
        return CandidateVideo(
            platform=self.platform,
            platform_video_id=v.get("id", ""),
            url=f"https://www.tiktok.com/@{handle}/video/{v.get('id', '')}",
            creator_handle=handle,
            title="",
            description=v.get("desc", ""),
            published_at=datetime.fromtimestamp(int(v.get("createTime", 0)), tz=UTC),
            duration_s=int(v.get("duration") or 0) or None,
            views=int(stats.get("playCount", 0)),
            likes=int(stats.get("diggCount", 0)),
            comments=int(stats.get("commentCount", 0)),
            shares=int(stats.get("shareCount", 0)),
            saves=None,  # TikTok não expõe (ADR-0003)
            thumbnail_url=(v.get("video") or {}).get("cover"),
            raw=v,
        )
```

> `client.hashtag(...)` precisa do pipeline hashtag do TikTokApi — se a
> biblioteca expuser `api.hashtag(name, ...)` com sessão aberta, usar
> `api.hashtag(segment.keywords[0]).videos(...)`; a fixture/contrato é o
> canário (risco registrado no plano, §Em aberto).

- [ ] **Step 5: Gates + commit**

```bash
uv run pytest tests/infra/collectors -q && uv run ruff check . && uv run pyright
git add src tests && git commit -m "feat: collector TikTok (TikTok-Api) atras do Protocol com auth_expired/blocked"
```

---

### Task 14: Collector Instagram (TDD com fixtures)

**Files:**
- Create: `src/content_factory/infra/collectors/instagram.py`,
  `tests/infra/collectors/test_instagram.py`,
  `tests/fixtures/ig_hashtag_search.json`, `tests/fixtures/ig_hashtag_top.json`
- Modify: `infra/collectors/__init__.py` (`register_defaults`)

**Interfaces:**
- Consome: Graph API oficial (`GET /{ig-user-id}/hashtags?user_search=q` →
  `GET /{hashtag-id}/top_media`), `Settings.instagram_access_token/ig_user_id`
- Produz: `InstagramCollector(platform="instagram", access_token, ig_user_id,
  transport=None)` — `views=None` (Graph API não devolve views de
  terceiros, ADR-0003), `duration_s=None`; header `X-App-Usage` lido em
  toda resposta (`self.app_usage: int`); 400/401 → `AuthExpired`

- [ ] **Step 1: Fixtures**

`ig_hashtag_search.json`:
```json
{"data": [{"id": "17841567890123456", "name": "candles"}]}
```

`ig_hashtag_top.json`:
```json
{"data": [{"id": "17900000000000001", "caption": "vela de soja artesanal #candle",
  "like_count": 5400, "comments_count": 130,
  "timestamp": "2026-10-01T10:00:00+0000", "media_type": "VIDEO",
  "permalink": "https://www.instagram.com/p/ABC123/",
  "thumbnail_url": "https://cdn.example/t.jpg", "username": "velaslab"}]}
```

- [ ] **Step 2: Teste falhando** `tests/infra/collectors/test_instagram.py`:

```python
import json
from pathlib import Path

import httpx
import pytest
from content_factory.domain.errors import AuthExpired
from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors.instagram import InstagramCollector

FIXDIR = Path(__file__).parent.parent / "fixtures"
SEG = SegmentConfig(
    slug="candles", name="Velas", platforms=["instagram"], keywords=["vela artesanal"]
)


def _route(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    headers = {"X-App-Usage": '{"call_count":10,"total_cputime":5,"total_time":5}'}
    if "hashtags" in url:
        return httpx.Response(
            200, headers=headers, content=(FIXDIR / "ig_hashtag_search.json").read_bytes()
        )
    if "top_media" in url or "recent_media" in url:
        return httpx.Response(
            200, headers=headers, content=(FIXDIR / "ig_hashtag_top.json").read_bytes()
        )
    return httpx.Response(404, json={})


@pytest.mark.asyncio
async def test_discover_maps_fixture() -> None:
    c = InstagramCollector(access_token="t", ig_user_id="1", transport=httpx.MockTransport(_route))
    out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "instagram"
    assert out[0].views is None  # limitação documentada (ADR-0003)
    assert out[0].likes == 5400 and out[0].comments == 130
    assert out[0].url.startswith("https://www.instagram.com/p/")
    assert c.app_usage == 10


@pytest.mark.asyncio
async def test_401_becomes_auth_expired() -> None:
    def _unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid token"}})

    c = InstagramCollector(
        access_token="bad", ig_user_id="1", transport=httpx.MockTransport(_unauthorized)
    )
    with pytest.raises(AuthExpired):
        await c.discover(SEG, limit=5)
```

- [ ] **Step 3: Rodar e ver falhar** (ImportError)

- [ ] **Step 4: Implementar** `infra/collectors/instagram.py`:

```python
import httpx

from content_factory.domain.errors import AuthExpired, NetworkError, UpstreamChanged
from content_factory.domain.models import CandidateVideo, SegmentConfig

API = "https://graph.facebook.com/v21.0"
FIELDS = (
    "id,caption,like_count,comments_count,timestamp,media_type,permalink,thumbnail_url,username"
)


class InstagramCollector:
    platform = "instagram"

    def __init__(
        self, access_token: str, ig_user_id: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._token = access_token
        self._user = ig_user_id
        self._client = httpx.AsyncClient(transport=transport, timeout=20)
        self.app_usage = 0

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        try:
            r = await self._client.get(
                f"{API}/{path}", params={**params, "access_token": self._token}
            )
        except httpx.HTTPError as exc:
            raise NetworkError(self.platform, str(exc)) from exc
        usage = r.headers.get("X-App-Usage", "")
        if usage and "call_count" in usage:
            import json

            self.app_usage = int(json.loads(usage).get("call_count", 0))
        if r.status_code in (400, 401):
            raise AuthExpired(self.platform, f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise UpstreamChanged(self.platform, f"HTTP {r.status_code}")
        data = r.json()
        if not isinstance(data, dict):
            raise UpstreamChanged(self.platform, "JSON inesperado")
        return data

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = segment.keywords[0].replace("#", "") if segment.keywords else segment.slug
        search = await self._get(f"{self._user}/hashtags", {"user_search": q, "limit": "1"})
        rows = search.get("data") or []
        if not rows:
            return []
        tag_id = rows[0]["id"]
        top = await self._get(
            f"{tag_id}/top_media", {"fields": FIELDS, "limit": str(min(limit, 25))}
        )
        items = top.get("data") or []
        return [self._map(i) for i in items[:limit]]

    def _map(self, item: dict) -> CandidateVideo:
        from datetime import datetime

        return CandidateVideo(
            platform=self.platform,
            platform_video_id=item.get("id", ""),
            url=item.get("permalink", ""),
            creator_handle=item.get("username", ""),
            title="",
            description=item.get("caption") or "",
            published_at=datetime.fromisoformat(item["timestamp"])
            if item.get("timestamp")
            else None,
            views=None,  # Graph API não expõe (ADR-0003)
            likes=int(item.get("like_count", 0)),
            comments=int(item.get("comments_count", 0)),
            shares=None,
            saves=None,
            duration_s=None,
            thumbnail_url=item.get("thumbnail_url"),
            raw=item,
        )
```

E em `register_defaults`:
`register(InstagramCollector(settings.instagram_access_token, settings.ig_user_id))`.

- [ ] **Step 5: Gates + commit:**

```bash
uv run pytest tests/infra/collectors -q && uv run ruff check . && uv run pyright
git add src tests && git commit -m "feat: collector Instagram via Graph API oficial (views de terceiros = null)"
```

---

### Task 15: Collector Web + `cf ingest` + guarda SSRF (TDD)

**Files:**
- Create: `src/content_factory/infra/collectors/web.py`,
  `src/content_factory/infra/ssrf.py`, `src/content_factory/app/ingest.py`,
  `tests/infra/collectors/test_web.py`, `tests/infra/test_ssrf.py`,
  `tests/app/test_ingest.py`
- Modify: `cli/main.py` (comando `ingest`), `infra/collectors/__init__.py`

**Interfaces:**
- Produz: `WebCollector.discover` (ddgs text search por keywords →
  `CandidateVideo(platform="web")`);
  `ingest_url(url, transport=None) -> CandidateVideo`
  (fetch + extrai `<title>`/meta description; `transport` só p/ teste);
  `ssrf.assert_public_http(url)` — valida scheme, resolve DNS e bloqueia
  IP privado/loopback/link-local/`169.254.169.254`/multicast/reserved,
  timeout 10 s, max 2 MB
- CLI: `cf ingest --url <url>` → envelope; com `--segment` faz upsert

- [ ] **Step 1: Teste SSRF falhando** `tests/infra/test_ssrf.py`:

```python
import pytest
from content_factory.domain.errors import DomainError
from content_factory.infra.ssrf import assert_public_http


def test_rejects_loopback_ip() -> None:
    with pytest.raises(DomainError):
        assert_public_http("http://127.0.0.1:8080/x")


def test_rejects_link_local_metadata() -> None:
    with pytest.raises(DomainError):
        assert_public_http("http://169.254.169.254/latest/meta-data/")


def test_rejects_non_http_schemes() -> None:
    for url in ("file:///etc/passwd", "ftp://example.com/a", "gopher://x"):
        with pytest.raises(DomainError):
            assert_public_http(url)


def test_rejects_private_ip_behind_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, port: int, *a: object, **kw: object) -> list:
        return [((2, 1, 6, 1, ("10.0.0.5", port))]

    import socket
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DomainError):
        assert_public_http("https://interno.example.com/x")


def test_allows_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, port: int, *a: object, **kw: object) -> list:
        return [((2, 1, 6, 1, ("93.184.216.34", port))]

    import socket
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert_public_http("https://example.com/x")   # não levanta
```

- [ ] **Step 2: Rodar e ver falhar** (módulo não existe)

- [ ] **Step 3: Implementar SSRF** `infra/ssrf.py`:

```python
import ipaddress
import socket
from urllib.parse import urlparse

from content_factory.domain.errors import DomainError


def assert_public_http(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DomainError(f"scheme/host inválido: {url}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as exc:
        raise DomainError(f"DNS falhou para {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise DomainError(f"IP não-público bloqueado: {ip} ({url})")
```

- [ ] **Step 4: Teste Web falhando → implementar**

`tests/infra/collectors/test_web.py`:

```python
import pytest
from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors import web as web_mod
from content_factory.infra.collectors.web import WebCollector

SEG = SegmentConfig(slug="candles", name="Velas", platforms=["web"], keywords=["vela artesanal"])


class StubDDGS:
    def text(self, query: str, max_results: int = 10) -> list[dict]:
        return [
            {
                "title": "Tutorial de velas",
                "href": "https://ex.com/vela",
                "body": "aprenda a fazer velas de soja",
            }
        ]


@pytest.mark.asyncio
async def test_discover_maps_ddgs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: StubDDGS())
    out = await WebCollector().discover(SEG, limit=5)
    assert out and out[0].platform == "web"
    assert out[0].title == "Tutorial de velas"
    assert out[0].url == "https://ex.com/vela"
    assert out[0].views is None
```

`infra/collectors/web.py` — `discover` roda
`await asyncio.to_thread(lambda: factory().text(q, max_results=limit))`
e mapeia `title/href/body` → `CandidateVideo(platform="web", views=None)`:

```python
import asyncio

from ddgs import DDGS

from content_factory.domain.models import CandidateVideo, SegmentConfig


class WebCollector:
    platform = "web"

    def __init__(self) -> None:
        pass  # resolve DDGS no momento do discover (monkeypatchável)

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        q = " OR ".join(segment.keywords[:3])
        results = await asyncio.to_thread(lambda: DDGS().text(q, max_results=limit))
        return [
            CandidateVideo(
                platform=self.platform,
                platform_video_id=r.get("href", ""),
                url=r.get("href", ""),
                creator_handle="",
                title=r.get("title", ""),
                description=r.get("body", ""),
                views=None,
                likes=None,
                comments=None,
                shares=None,
                saves=None,
                raw=dict(r),
            )
            for r in results
        ]
```

> `DDGS` referenciado como atributo de módulo **no corpo de `discover`**
> (closure lê o global a cada chamada) — é isso que o
> `monkeypatch.setattr(web_mod, "DDGS", …)` do teste troca. `register_defaults`
> ganha `register(WebCollector())`.

- [ ] **Step 5: `app/ingest.py` + teste → CLI `ingest`**

`tests/app/test_ingest.py`:

```python
import httpx
import pytest
from content_factory.app.ingest import ingest_url
from content_factory.domain.errors import DomainError


@pytest.mark.asyncio
async def test_ingest_extracts_title(monkeypatch: pytest.MonkeyPatch) -> None:
    import content_factory.infra.ssrf as ssrf

    monkeypatch.setattr(ssrf, "assert_public_http", lambda url: None)

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<html><head><title>Minha vela</title>"
                "<meta name='description' content='feito a mao'></head></html>"
            ),
        )

    cand = await ingest_url("https://ex.com/vela", transport=httpx.MockTransport(_route))
    assert cand.title == "Minha vela"
    assert cand.platform == "web" and cand.url == "https://ex.com/vela"


@pytest.mark.asyncio
async def test_ingest_blocks_private(monkeypatch: pytest.MonkeyPatch) -> None:
    # sem monkeypatch: URL real 127.0.0.1 → DomainError ANTES de fetch
    from content_factory.app.ingest import ingest_url as go

    with pytest.raises(DomainError):
        await go("http://127.0.0.1:9/x")
```

`app/ingest.py`: valida via `ssrf.assert_public_http` → `httpx.AsyncClient.get`
(`timeout=10`, lê no máx. 2 MB via `aiter_bytes` com corte) → extrai
`<title>`/`meta[name=description]` com regex → `CandidateVideo(platform="web")`.

CLI:

```python
@app.command("ingest")
def ingest(url: str, segment: str | None = None) -> None:
    """Baixa uma URL, extrai título/descrição e grava (se --segment)."""
    try:
        cand = asyncio.run(ingest_url(url))
        # se segment: wire p/ VideoRepository.upsert_candidates (mesmo wire da Task 6)
        print(dumps(envelope_ok("ingest", url=cand.url, title=cand.title)))
        raise typer.Exit(0)
    except DomainError as exc:
        print(dumps(envelope_err("ingest", "invalid_url", str(exc))))
        raise typer.Exit(1) from exc
```

- [ ] **Step 6: Gates + commit:**

```bash
uv run pytest tests/infra tests/app -q && uv run ruff check . && uv run pyright
git add src tests && git commit -m "feat: collector Web (ddgs) + cf ingest com guarda SSRF"
```

---

### Task 16: Use-case `score` + `cf score` (TDD)

**Files:**
- Create: `src/content_factory/app/score.py`, `tests/app/test_score.py`
- Modify: `cli/main.py` (comando `score`), `app/ports.py` (+ `ScoreRepository`
  + `VideoRepository.latest_for_segment`), `domain/models.py` (+ `VideoSnapshot`),
  `infra/db/repositories.py` (+ `ScoreRepositoryImpl`, método novo)

**Interfaces:**
- Ports novos (spec do módulo — Task 2 já avisou que entram aqui):

```python
class ScoreRepository(Protocol):
    async def upsert(
        self, video_id: int, segment_id: int, result: ScoreResult, computed_at: datetime
    ) -> None: ...
```

  `VideoRepository` ganha:
  `async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]`
  — 1 linha por vídeo do segmento com a **métrica mais recente**
  (`video_metrics` ORDER BY captured_at DESC LIMIT 1 por vídeo).

```python
class VideoSnapshot(BaseModel):
    video_id: int
    title: str = ""
    description: str = ""
    published_at: datetime | None = None
    metrics: VideoMetrics
```

- Produz: `ScoreDeps(videos, scores, segment, keywords, clock)` ·
  `async def score(deps: ScoreDeps) -> ScoreOutcome(ok, partial, scored)` ·
  relevância = `keywords encontradas / len(keywords)` (substring
  case-insensitive em título+descrição)

- [ ] **Step 1: Teste falhando** `tests/app/test_score.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from content_factory.app.score import ScoreDeps, score
from content_factory.domain.models import (
    ScoreResult,
    Segment,
    SegmentConfig,
    VideoMetrics,
    VideoSnapshot,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class FakeVideos:
    def __init__(self, snaps: list[VideoSnapshot]) -> None:
        self._s = snaps

    async def latest_for_segment(self, segment_id: int) -> list[VideoSnapshot]:
        return self._s

    async def upsert_candidates(self, segment_id: int, source: str, candidates: object) -> object:
        # pyright strict: o fake precisa satisfazer o Protocolo inteiro
        raise NotImplementedError


class FakeScores:
    def __init__(self) -> None:
        self.saved: list[tuple[int, ScoreResult]] = []

    async def upsert(
        self, video_id: int, segment_id: int, result: ScoreResult, computed_at: datetime
    ) -> None:
        self.saved.append((video_id, result))


SEG = Segment(
    id=1,
    **SegmentConfig(
        slug="candles", name="Velas", platforms=["youtube"], keywords=["vela", "soja"]
    ).model_dump(),
)


def snap(video_id: int, views: int, title: str = "vela de soja", age_h: int = 6) -> VideoSnapshot:
    return VideoSnapshot(
        video_id=video_id,
        title=title,
        published_at=NOW - timedelta(hours=age_h),
        metrics=VideoMetrics(views=views, likes=views // 20),
    )


async def test_score_writes_one_result_per_video() -> None:
    repo = FakeVideos([snap(1, 100_000), snap(2, 50_000)])
    scores = FakeScores()
    deps = ScoreDeps(
        videos=repo, scores=scores, segment=SEG, keywords=["vela", "soja"], clock=lambda: NOW
    )
    out = await score(deps)
    assert out.scored == 2 and out.ok is True
    assert len(scores.saved) == 2
    assert all(0 <= r.total <= 100 for _, r in scores.saved)


async def test_zero_metrics_totals_zero() -> None:
    zero = VideoSnapshot(video_id=9, title="x", published_at=None, metrics=VideoMetrics())
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([zero]), scores=scores, segment=SEG, keywords=["vela"], clock=lambda: NOW
    )
    await score(deps)
    assert scores.saved[0][1].total == 0.0


async def test_relevance_counts_keyword_hits() -> None:
    # título "vela de soja" casa com 2/2 keywords → relevance 100
    hits = VideoSnapshot(
        video_id=1, title="vela de soja", published_at=NOW, metrics=VideoMetrics(views=10_000)
    )
    scores = FakeScores()
    deps = ScoreDeps(
        videos=FakeVideos([hits]),
        scores=scores,
        segment=SEG,
        keywords=["vela", "soja"],
        clock=lambda: NOW,
    )
    await score(deps)
    assert scores.saved[0][1].relevance == 100.0
```

- [ ] **Step 2: Rodar e ver falhar** (ImportError `content_factory.app.score`)

- [ ] **Step 3: Implementar** `app/score.py`:

```python
from collections.abc import Callable
from datetime import UTC, datetime

from content_factory.app.ports import ScoreRepository, VideoRepository
from content_factory.domain.models import ScoreResult, Segment, VideoSnapshot
from content_factory.domain.scoring import viral_score


class ScoreDeps:
    def __init__(
        self,
        videos: VideoRepository,
        scores: ScoreRepository,
        segment: Segment,
        keywords: list[str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.videos = videos
        self.scores = scores
        self.segment = segment
        self.keywords = keywords
        self.clock = clock


class ScoreOutcome:
    def __init__(self, ok: bool, partial: bool, scored: int) -> None:
        self.ok = ok
        self.partial = partial
        self.scored = scored


def relevance_of(snap: VideoSnapshot, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    text = f"{snap.title} {snap.description}".lower()
    hits = sum(1 for k in keywords if k.lower() in text)
    return hits / len(keywords)


async def score(deps: ScoreDeps) -> ScoreOutcome:
    now = deps.clock()
    snaps = await deps.videos.latest_for_segment(deps.segment.id)
    scored = 0
    errors = 0
    for snap in snaps:
        rel = relevance_of(snap, deps.keywords)
        result = viral_score(snap.metrics, rel, now, snap.published_at)
        try:
            await deps.scores.upsert(snap.video_id, deps.segment.id, result, now)
            scored += 1
        except Exception:  # repo em falha não derruba o lote
            errors += 1
    ok = errors == 0
    return ScoreOutcome(ok=ok, partial=(not ok and scored > 0), scored=scored)
```

- [ ] **Step 4: CLI `score`** (mesmo wire da Task 6: factory →
  `SegmentRepositoryImpl.get_by_slug` + `VideoRepositoryImpl` +
  `ScoreRepositoryImpl` → `asyncio.run` → envelope `{"ok":…, "scored": N}`).
  Teste de CLI: comando sem segmento inexistente → exit 1
  (`SegmentNotFound` → `envelope_err(code="segment_not_found")`).

- [ ] **Step 5: Gates + commit:**

```bash
uv run pytest tests/app -q && uv run ruff check . && uv run pyright
git add src tests && git commit -m "feat: use-case score persiste video_scores timeseries com relevancia do segmento"
```

---

### Task 17: Worker arq + cron 2×/dia + `cf status`

> NOTE (Task 7): `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` já vem na imagem `worker` (legível por `appuser`) — não reinstalar o chromium.

**Files:**
- Create: `src/content_factory/infra/queue/__init__.py`,
  `src/content_factory/infra/queue/worker.py`,
  `tests/infra/queue/__init__.py`, `tests/infra/queue/test_worker.py`
- Modify: `cli/main.py` (comando `status`), compose (serviço `worker` já
  definido na Task 7 — `command: uv run arq content_factory.infra.queue.worker.WorkerSettings`)

**Interfaces:**
- Produz: `WorkerSettings.functions = {"collect_job": …, "score_job": …}`,
  cron `collect_job` 08:00/20:00 (`COLLECT_CRON_HOURS` da env, default
  `08,20`), jobs rodam o **mesmo** `app.collect`/`app.score` (zero lógica
  duplicada); `cf status --segment <slug>` lê `collect_runs` últimos 7 dias
  → envelope `{"ok": true, "runs": {"ok": N, "blocked": M, …}}`

- [ ] **Step 1: Teste falhando** `tests/infra/queue/test_worker.py`:

```python
import pytest
from content_factory.infra.queue import worker as w


class CountingDeps:
    def __init__(self) -> None:
        self.collect_calls = 0


async def test_run_collect_job_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_collect(slug: str, platforms: list[str], limit: int, deps: object) -> object:
        calls.append(slug)
        raise SystemExit  # sai imediato — só conta a chamada

    monkeypatch.setattr(w, "_collect_impl", fake_collect)
    monkeypatch.setattr(
        w, "_load_settings", lambda: {"segment": "candles", "platforms": ["youtube"], "limit": 10}
    )
    with pytest.raises(SystemExit):
        await w.run_collect_job({"collect_deps": None})
    assert calls == ["candles"]


def test_cron_hours_parse() -> None:
    assert w.parse_cron_hours("08,20") == {8, 20}
    assert w.parse_cron_hours("") == {8, 20}  # default
```

- [ ] **Step 2: Rodar e ver falhar** (ImportError)

- [ ] **Step 3: Implementar** `infra/queue/worker.py`:

```python
import os
from collections.abc import Awaitable, Callable
from typing import Any

from arq import create_pool
from arq.cron import cron
from arq.connections import RedisSettings

from content_factory.app.collect import CollectDeps, collect
from content_factory.app.score import ScoreDeps, score


def parse_cron_hours(raw: str) -> set[int]:
    try:
        hours = {int(h) for h in raw.split(",") if h.strip()}
    except ValueError:
        hours = set()
    return hours or {8, 20}


# hooks de teste: monkeypatch troca por fakes sem tocar no arq/banco
_collect_impl: Callable[..., Awaitable[Any]] = collect
_score_impl: Callable[..., Awaitable[Any]] = score
_load_settings: Callable[[], dict[str, Any]] = dict


async def run_collect_job(ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_settings()
    deps: CollectDeps = ctx["collect_deps"]  # buildado no startup do worker
    out = await _collect_impl(cfg["segment"], cfg["platforms"], cfg["limit"], deps)
    return {"ok": out.ok, "partial": out.partial, **out.counts}


async def run_score_job(ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_settings()
    out = await _score_impl(ctx["score_deps"])
    return {"ok": out.ok, "scored": out.scored}


class WorkerSettings:
    functions = [run_collect_job, run_score_job]
    cron_jobs = [
        cron(
            run_collect_job,
            hour=parse_cron_hours(os.getenv("COLLECT_CRON_HOURS", "08,20")),
            minute=0,
            unique=True,
        ),
        cron(run_score_job, hour={8, 21}, minute=30, unique=True),
    ]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://redis:6379/0"))
```

> O build real de `collect_deps`/`score_deps` (fábrica que monta
> repositórios + `register_defaults`) vive em `create_app_deps(settings)`
> e é injetado no `ctx` no startup — testes nunca passam por ele
> (monkeypatch nos hooks).

- [ ] **Step 4: `cf status` + teste** — SQL de contagem por status nos
  últimos 7 dias (`collect_runs WHERE started_at > now() - 7d GROUP BY
  status`); teste com repo fake devolvendo 2 runs `ok` + 1 `blocked` →
  envelope `{"ok": true, "runs": {"ok": 2, "blocked": 1}}`; segmento
  inexistente → exit 1.

- [ ] **Step 5: Gates + commit:**

```bash
uv run pytest tests/infra/queue tests/cli -q && uv run ruff check . && uv run pyright
git add src tests && git commit -m "feat: worker arq com cron 2x/dia e comando status operacional"
```

---

### Task 18: E2E `00`/`01` + gates finais + SOLID_AUDIT + REVISAO + PR

**Files:**
- Create: `tests/e2e/__init__.py`, `tests/e2e/test_00_prereq.py`,
  `tests/e2e/test_01_smoke.py`, `PLANO_TESTE_E2E.md`,
  `SOLID_AUDIT.md`, `REVISAO_2026-10-07.md`
- Modify: `docs/HANDOFF.md`, `docs/README.md`, tracker deste plano

**Interfaces:**
- Consome: tudo das tasks 1–17
- Produz: evidência de aceitação (PRD §critérios) e PR

- [ ] **Step 1: `test_00_prereq.py`** (gate — nunca rebaixar limiar):

```python
import os
import shutil
import socket
import pytest

pytestmark = pytest.mark.e2e


def test_ram_minima() -> None:
    with open("/proc/meminfo") as f:
        available_kb = int([l for l in f if l.startswith("MemAvailable")][0].split()[1])
    assert available_kb > 1_500_000, f"RAM disponível {available_kb} kB < 1.5 GB"


def test_postgres_up() -> None:
    assert os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL ausente"
    host = os.getenv("TEST_DB_HOST", "127.0.0.1")
    with socket.create_connection((host, 5432), timeout=3):
        pass


def test_docker_presente() -> None:
    assert shutil.which("docker"), "docker não encontrado"
```

- [ ] **Step 2: `test_01_smoke.py`** (aceitação — fixtures, sem chaves;
  exige `TEST_DATABASE_URL` + schema migrado):

```python
import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from content_factory.app.collect import CollectDeps, collect
from content_factory.app.score import ScoreDeps, score
from content_factory.app.segment import load_segment_file, upsert_segment
from content_factory.domain.models import CandidateVideo, SegmentConfig
from content_factory.infra.db.orm import Base, CollectRunRow, VideoRow, VideoScoreRow
from content_factory.infra.db.repositories import (
    CollectRunRepositoryImpl,
    ScoreRepositoryImpl,
    SegmentRepositoryImpl,
    VideoRepositoryImpl,
)
from tests.conftest import requires_infra

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class FakeCollectorOne:
    platform = "youtube"

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        return [
            CandidateVideo(
                platform="youtube",
                platform_video_id="e2e1",
                url="https://youtu.be/e2e1",
                creator_handle="u",
                title="vela de soja test",
                views=10_000,
                likes=500,
            )
        ]


class AlwaysLock:
    async def acquire_once(self, key: str) -> bool:
        return True

    async def release(self, key: str) -> None:
        return None


class NoopLimiter:
    async def acquire(self, platform: str) -> None:
        return None


@requires_infra
async def test_smoke_segment_collect_score() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        # 1) segment load → id > 0
        seg = await upsert_segment(SegmentRepositoryImpl(s), Path("segments/candles.yaml"))
        assert seg.id > 0

        # 2) collect com FakeCollector → upsert real
        deps = CollectDeps(
            collectors=[FakeCollectorOne()],
            videos=VideoRepositoryImpl(s),
            segments=SegmentRepositoryImpl(s),
            runs=CollectRunRepositoryImpl(s),
            limiter=NoopLimiter(),
            lock=AlwaysLock(),
        )
        out = await collect("candles", ["youtube"], limit=5, deps=deps)
        assert out.ok and out.counts["inserted"] >= 1

        # 3) score → linha em video_scores
        sdeps = ScoreDeps(
            videos=VideoRepositoryImpl(s),
            scores=ScoreRepositoryImpl(s),
            segment=seg,
            keywords=seg.keywords,
        )
        sout = await score(sdeps)
        assert sout.scored >= 1

        # 4) persistência: videos + scores + 1 run ok
        n_videos = (await s.execute(select(func.count()).select_from(VideoRow))).scalar()
        n_scores = (await s.execute(select(func.count()).select_from(VideoScoreRow))).scalar()
        runs = (
            (await s.execute(select(CollectRunRow).where(CollectRunRow.collector == "youtube")))
            .scalars()
            .all()
        )
        assert n_videos and n_videos >= 1
        assert n_scores and n_scores >= 1
        assert [r.status for r in runs][-1] == "ok"
        await s.rollback()
    await engine.dispose()
```

> Suíte roda **depois** de `alembic upgrade head` (mesmo gate do `test_00`)
> e usa os repositórios reais — só o collector é fake (fixture; critério 1
> do spec §12: CI roda sem chaves).

- [ ] **Step 3: `PLANO_TESTE_E2E.md`** — playtest manual: subir stack,
  criar `.devcontainer/secrets/secrets.env` com as 3 chaves, rodar
  `cf collect --segment candles --platform youtube,tiktok,instagram,web`,
  anexar saída + contagens neste doc.

- [ ] **Step 4: Gates finais com evidência (anexar saída no `REVISAO`):**

```bash
make gates && uv run pytest -q -m e2e   # exigindo infra de pé
```

- [ ] **Step 5: `SOLID_AUDIT.md`** — métricas com comando+data
  (`uv run ruff check --statistics`, pior cc via `uv run python -m radon cc -s src`),
  achados `arquivo:linha`, backlog, **go/no-go explícito**.

- [ ] **Step 6: `REVISAO_2026-10-07.md`** — 8 ângulos da skill, até 6
  findings verificados lendo código real, tabela final
  `# | Componente | Findings | Status`, **bug real achado nesta revisão**.

- [ ] **Step 7: Atualizar HANDOFF + tracker (☐→✅) deste plano**

- [ ] **Step 8: Push + PR**

```bash
git checkout -b update/fundacao-2026-10-07
git add -A && git commit -m "test: e2e 00/01 + auditorias SOLID/revisao da entrega"
git push -u origin update/fundacao-2026-10-07   # gh repo create content-factory --private na 1ª vez
gh pr create --title "Fundação + coletores + banco (frentes 1-2 do PRD)" --body "Ver docs/ai-plans/…"
# CI verde -> merge -> apagar branch local+remota
```

---

## O que mudou

(plano novo — preencher na execução)

## Descobertas

(plano novo — preencher na execução)

## Verificação

Gates de aceitação (evidência anexada obrigatória — ver spec §10 e §12):

```bash
uv run pytest -q -m "not integration and not e2e"   # unit
uv run pytest -q -m integration                     # exige TEST_DATABASE_URL
uv run pytest -q -m e2e                             # exige stack (00_prereq)
uv run ruff check . && uv run ruff format --check .
uv run pyright                                      # strict, 0 erros
uv run alembic check                                # exit 0
uv run bandit -q -c pyproject.toml && uv run pip-audit
uv run --only-group docs mkdocs build --strict
uv run python scripts/check_privacy.py
```

## Em aberto

- Chaves reais (`YOUTUBE_API_KEY`, `INSTAGRAM_ACCESS_TOKEN`, `TIKTOK_MS_TOKEN`)
  entram em `.devcontainer/secrets/secrets.env` **fora do git** na execução
  do playtest (Task 18)
- Pesos finais do score podem mudar quando a fase de IA entregar
  `replication`/`production` (renormalizar — ver spec §4.3)
- Shape do `as_dict` do TikTok-Api pode mudar upstream → fixture de contrato
  é o canário (Task 13)
