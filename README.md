<div align="center">

# content-factory

### Find the short videos that are working in your niche, re-measure them twice a day, and rank them. Every day, unattended.

[![License: MIT](https://img.shields.io/badge/License-MIT-1de9d6.svg)](LICENSE)
[![CI](https://github.com/leonardodg/content-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/leonardodg/content-factory/actions/workflows/ci.yml)
[![Docs](https://github.com/leonardodg/content-factory/actions/workflows/docs.yml/badge.svg)](https://leonardodg.github.io/content-factory/)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-3776ab.svg)](https://www.python.org/)
[![pgvector](https://img.shields.io/badge/Postgres-pgvector-336791.svg)](https://github.com/pgvector/pgvector)
[![arq](https://img.shields.io/badge/queue-arq%20%2B%20Redis-d82c20.svg)](https://arq-docs.helpmanual.io/)
[![CI](https://github.com/leonardodg/content-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/leonardodg/content-factory/actions/workflows/ci.yml)
[![Types](https://img.shields.io/badge/pyright-strict-2f73bf.svg)](pyproject.toml)

**[Documentation](https://leonardodg.github.io/content-factory/)** ·
**[Cycle map](https://leonardodg.github.io/content-factory/diagram/mapa-do-ciclo/)** ·
**[Pipeline flows](https://leonardodg.github.io/content-factory/diagram/fluxos/)** ·
**[Operations](https://leonardodg.github.io/content-factory/manual/operacao/)** ·
**[Installation](#-quick-start)**

</div>

Point it at a niche. A **segment** is a YAML file with keywords, languages
and platforms: `velas-artesanais`, `sabonetes-artesanais`, `macrame`.
Every morning a worker expands those keywords with Google Suggest and
Trends. It then searches YouTube, Instagram, TikTok and the web, stores
every candidate video, **measures it again in the evening**, and scores it.
The output is a ranking per segment, `viral_score`, built from views,
growth between snapshots, engagement, relevance and recency. It is the raw
material for the next stage: understanding *why* a video works and deriving
original ideas from that mechanism, not copying the video.

The CLI (`cf`) and the worker call the **same use cases**. The CLI prints
only a JSON envelope (`{"ok": ...}`); the data lives in Postgres.

---

## 📋 Features

- 🗂 **Parametrised by segment.** A new niche is a YAML file and
  `cf research start`, not new code.
- ⏰ **A daily cycle, not a script.** Three cron jobs (research 07:00,
  collect 02:00/08:00/14:00/20:00, score 30 min after each collect, BRT — set
  by `COLLECT_CRON_HOURS`/`SCORE_CRON_HOURS` in the worker's compose) run for
  as long as the segment's campaign is active.
- 🔍 **Keyword expansion before search.** Google Suggest and Trends find
  what the niche is talking about *today*, and that feeds a second,
  keyword-driven search on the social networks.
- 📈 **Real growth, not guessed growth.** The same video is re-measured on
  every collect (append-only `video_metrics`), so `velocity` is the delta
  between two real snapshots. It is never derived from the video's age.
- 🧮 **Honest scoring.** A missing metric is excluded and the weights are
  renormalised; a zero counts as zero. `metrics_available` records which
  inputs a score actually used.
- 🧵 **Concurrent, isolated segments.** One unit of work and one
  transaction per segment. A failing segment rolls back alone and its
  siblings commit.
- 🛡 **Built to survive flaky sources.**
  - a source or platform that fails becomes a `failed` run, and the rest
    of the cycle continues;
  - per-platform breakers, global rate-limit windows in Redis, and a lock
    with an ownership token per segment.
- 🔒 **Secrets never in the clear.** Tokens come from `/run/secrets`, and
  logs and envelopes are redacted. A privacy gate runs in CI.
- ✅ **Tests, pyright strict, bandit, pip-audit — all gated in CI.** Integration tests run
  against disposable Postgres/Redis, and CI runs them with real services.

---

## 🆚 Why not just a scraper script

| | This project | A one-shot scraping script |
|---|---|---|
| **Cadence** | cron worker, every day, while the campaign is active | runs when someone remembers |
| **Growth signal** | re-measures known videos twice a day → `velocity` from real deltas | one snapshot, no growth |
| **Failure isolation** | one platform failing → `failed` run, siblings persist; one segment failing → only its transaction rolls back | one exception kills the run |
| **Rate limits** | shared Redis windows per platform (YouTube 100/day, TikTok 6/min, Instagram 20/min), breakers | however fast the loop runs, until the platform notices |
| **Idempotency** | `ON CONFLICT` upserts; re-running a collect costs nothing | duplicates or manual state |
| **Auditability** | every platform/source attempt is a `collect_runs` row with status, error code and timings | whatever was printed |

---

## Technologies and Tools

### Core
| Technology | Role |
|---|---|
| **Python** ≥ 3.14 + **uv** | Application and dependency management |
| **Postgres 16 + pgvector** | Source of truth: segments, videos, metric snapshots, scores, signals, runs |
| **SQLAlchemy 2 (async) + Alembic** | ORM, one session per unit of work, versioned migrations |
| **Redis + arq** | Cron worker, global rate-limit windows, per-platform breaker, segment locks |
| **Typer** | `cf` CLI with a JSON-only envelope |
| **pydantic / pydantic-settings** | Domain models and typed configuration |

### Collectors
| Source | Role |
|---|---|
| **YouTube Data API v3** | `search` (last 7 days, by views) → `videos` → `channels` |
| **Instagram Graph API** | hashtag `top_media`; falls back to own media filtered by keyword until App Review |
| **TikTokApi + Playwright** | trending, hashtag and keyword search (needs `TIKTOK_MS_TOKEN`) |
| **ddgs** (DuckDuckGo) | web pages for the niche |
| **Google Suggest** | keyword expansion (research) |
| **pytrends** | interest over time (research) |

### Quality
| Technology | Role |
|---|---|
| **pytest** | Unit + integration against disposable Postgres/Redis (count: see the CI run) |
| **ruff** | Lint + format |
| **pyright** | Strict type checking, 0 errors |
| **bandit** + **pip-audit** | Security linting and dependency CVE scanning |
| **MkDocs + Material + Mermaid** | Documentation site, published to GitHub Pages |

---

## 🏗 Architecture

```
segments/*.yaml ──cf segment load──►  segments  ◄──cf research start──  research_campaigns
                                                                              │
              arq cron (BRT)                                                  ▼
   07:00 research_job ─┐                                        active_segments()
   02/08/14/20 collect_job ─┼──►  bounded_gather per segment (WORKER_MAX_SEGMENTS=3)
                            │         └─ 1 unit of work = 1 session = 1 commit per segment
   02:30/08:30/14:30/20:30 score_job ─┘
        │
        ├─ research: Suggest → Trends → keyword_signals / trend_observations
        │            → discover_by_keywords (YouTube · Instagram · TikTok)
        ├─ collect:  discover (YouTube · Instagram · TikTok · web)
        │            → videos + video_segments + video_metrics (new snapshot)
        └─ score:    latest snapshot + previous → video_scores (append-only)
```

Layers: `cli/` and the worker are entrypoints, `app/` holds the use cases,
`app/ports.py` the protocols, and `domain/` is pure, with no I/O. `infra/`
implements the ports (collectors, db, queue, redis). Dependencies point one
way only.

Diagrams (mind map, worker clock, every job and subtask, collectors, limits,
retries — Mermaid): **[Cycle map](https://leonardodg.github.io/content-factory/diagram/mapa-do-ciclo/)**
· source in [docs/diagram/mapa-do-ciclo.md](docs/diagram/mapa-do-ciclo.md).

---

## 🚀 Quick Start

### Prerequisites

| Resource | Notes |
|---|---|
| Docker + Compose | Postgres (pgvector), Redis, the `python` (CLI) and `worker` services |
| [uv](https://docs.astral.sh/uv/) | Python ≥ 3.14 |
| API keys (optional) | `YOUTUBE_API_KEY`, `INSTAGRAM_ACCESS_TOKEN` + `IG_USER_ID`, `TIKTOK_MS_TOKEN`. **The `web` collector needs none**, so you can run the whole cycle without any key. See [API keys](https://leonardodg.github.io/content-factory/manual/chaves-api/). |

### Installation

```bash
git clone https://github.com/leonardodg/content-factory.git
cd content-factory

# 0. Configure (secrets go in the secrets file, never in a tracked file)
cp .devcontainer/env/app.env.example .devcontainer/env/app.env
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env

# 1. Start Postgres + Redis + the CLI container, apply the schema
docker compose -f .devcontainer/docker-compose.yml up -d postgres redis python
docker compose -f .devcontainer/docker-compose.yml exec python uv run alembic upgrade head

# 2. Healthcheck
docker exec cf-python uv run cf ping
# → {"ok": true, "command": "ping"}

# 3. Load a segment and run one cycle by hand (web needs no key)
docker exec cf-python uv run cf segment load segments/candles.yaml
docker exec cf-python uv run cf collect --segment candles --platform web --limit 3
docker exec cf-python uv run cf score --segment candles

# 4. Let the worker run it every day, for N weeks
docker exec cf-python uv run cf research start --segment candles --weeks 1
docker compose -f .devcontainer/docker-compose.yml --profile worker up -d worker
```

Full walkthrough with expected outputs:
**[Getting started](https://leonardodg.github.io/content-factory/manual/guide-start/)**.

---

## 🧰 CLI (`cf`)

Every command prints exactly one JSON envelope (`{"ok": true|false, ...}`)
and exits non-zero on failure.

| Command | What it does |
|---|---|
| `cf ping` | CLI healthcheck (no database) |
| `cf segment load <yaml>` | Create or update a segment from its YAML |
| `cf research start\|stop\|status --segment S` | Start/stop the campaign that makes the worker process `S` |
| `cf collect --segment S [--platform …] [--limit N]` | Collect candidates now: idempotent upsert + audit in `collect_runs` |
| `cf score --segment S` | Score the latest snapshot of every video into `video_scores` |
| `cf status --segment S` | Summary of the last 7 days of collects |
| `cf collect-timings --segment S` | Per-platform `discover_ms`/`persist_ms` baseline |
| `cf score-sample` / `cf score-correlation` | Export the ranking for manual review; Spearman against the manual labels |
| `cf ingest --url U [--segment S]` | Fetch one URL (SSRF-guarded), extract title/description, store it if `--segment` is given |

---

## ⚙️ Configuration

`.devcontainer/env/app.env` (non-secret) and
`.devcontainer/secrets/secrets.env` (secret, mounted at `/run/secrets`).

```bash
DATABASE_URL=            # set by the compose for the containers
REDIS_URL=
YOUTUBE_API_KEY=         # secret
INSTAGRAM_ACCESS_TOKEN=  # secret
IG_USER_ID=
TIKTOK_MS_TOKEN=         # secret; empty = every TikTok run is `auth_expired`
TIKTOK_PROXY=            # optional

COLLECT_MAX_CONCURRENCY=2           # discovers at once per segment
WORKER_MAX_SEGMENTS=3               # segments at once per job (read every tick)
PLATFORM_MAX_CONCURRENCY=tiktok=1   # per-platform cap for the whole process; always keeps tiktok=1
COLLECT_CRON_HOURS=08,20            # collect times, BRT
```

Every variable, with defaults and what needs a restart:
**[Operations](https://leonardodg.github.io/content-factory/manual/operacao/)**.

---

## 🛠 Project structure

```
content-factory/
├── .github/workflows/
│   ├── ci.yml                    # lint, types, migrations, tests (real Postgres + Redis), security, privacy
│   └── docs.yml                  # builds the docs strictly and publishes them to GitHub Pages
├── .devcontainer/                # compose: postgres (pgvector), redis, python (CLI), worker
├── alembic/                      # migrations
├── segments/                     # one YAML per niche
├── src/content_factory/
│   ├── domain/                   # pure: models, scoring, fan-out guard, errors
│   ├── app/                      # use cases: collect, research_pipeline, score, ingest; ports.py
│   ├── cli/                      # Typer `cf`
│   └── infra/
│       ├── collectors/           # youtube, instagram, tiktok, web, web_suggest, trends_google, breaker
│       ├── db/                   # SQLAlchemy models, repositories, unit of work
│       ├── queue/                # arq worker + composition root
│       ├── ratelimit.py · lock.py · redact.py · logs.py
├── scripts/check_privacy.py      # privacy gate (CI + docs deploy)
├── tests/
└── docs/                         # MkDocs site; HANDOFF.md is the single source of project state
```

---

## 📦 Useful commands

```bash
make gates          # tests (unit) + lint + types + security + docs + privacy

uv run pytest -q                                   # unit; integration is skipped without TEST_*
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run alembic check
uv run bandit -q -c pyproject.toml -r src/ && uv run pip-audit
uv run --only-group docs mkdocs serve              # docs at http://127.0.0.1:8000
uv run python scripts/check_privacy.py
```

Integration tests need **disposable** Postgres/Redis (`TEST_DATABASE_URL`,
`TEST_REDIS_URL`). Never point them at a database you care about. See
[Dev environment](https://leonardodg.github.io/content-factory/dev/dev-environment/).

---

## ⚠️ Honest limitations

- **Instagram third-party hashtags need Meta App Review (gate #10).** Until
  then the collector falls back to the account's own media, filtered by
  keyword, and counts `gate_hits`. Views are never available for
  third-party media.
- **TikTok needs a session cookie.** Without `TIKTOK_MS_TOKEN` every TikTok
  run is `failed` (`auth_expired`). With it, sessions run one at a time
  (Playwright).
- **Google Suggest and Trends are unofficial.** Trends currently answers
  HTTP 400 every day. The research cycle carries on without it, recording
  a `failed` run, but the cause is not fixed.
- **The score is not calibrated yet.** The weights and the
  1000 views/hour velocity cap are MVP choices. Calibrating them is what
  the 7-day experiment is collecting data for.
- **One worker process.** Breakers for Suggest/Trends live in memory, which
  is correct only with a single worker.
- **The idea, script and production stages do not exist yet.** This repo
  finds and ranks; it does not generate.

---

## 🤝 Contributing

<img src="https://avatars.githubusercontent.com/u/1678290?s=400&u=2f875356b82f055057b6e9679c0b66001b9b29f9&v=4" width="120" title="LeoDG">

Issues and pull requests are welcome. Every change follows the project
rules in [CLAUDE.md](CLAUDE.md):
- a plan in `docs/ai-plans/` before the code;
- TDD;
- pyright strict with 0 errors;
- all the gates green (`make gates` plus `alembic check` and integration
  tests).

## 📄 License

MIT — see [LICENSE](LICENSE).

## 📮 Contact

LeoDG — [@le0dg](https://www.linkedin.com/in/le0dg)

- **Repository:** https://github.com/leonardodg/content-factory
- **Related projects:** [insta_kb](https://github.com/leonardodg/insta_kb) ·
  [minimax-video-factory](https://github.com/leonardodg/minimax-video-factory)
