# Data model — content-factory

!!! warning "Documento gerado — não edite à mão"
    Fonte: `Base.metadata` (modelos ORM) com gate `alembic check`.
    Regenere: `uv run python scripts/gen_datamodel.py` · leitura: 2026-10-08.

Convenções: **FK → alvo** = constraint declarada no banco; `nullable` = aceita vazio (métricas inexpostas por plataforma); `VECTOR(1536)` = coluna para a fase IA (hoje sempre NULL).

## Tabelas, campo a campo

### collect_runs

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `collector` | `VARCHAR(32)` | obrigatório · indexado |
| `segment_id` | `INTEGER` | **FK → segments.id** · nullable |
| `status` | `VARCHAR(16)` | obrigatório |
| `started_at` | `DATETIME` | obrigatório |
| `finished_at` | `DATETIME` | nullable |
| `found` | `INTEGER` | obrigatório |
| `inserted` | `INTEGER` | obrigatório |
| `updated` | `INTEGER` | obrigatório |
| `failed` | `INTEGER` | obrigatório |
| `quota_used` | `INTEGER` | obrigatório |
| `retries` | `INTEGER` | obrigatório |
| `error_code` | `VARCHAR(32)` | nullable |

### creative_patterns

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `name` | `VARCHAR(200)` | UNIQUE · obrigatório |
| `description` | `TEXT` | obrigatório |
| `hook` | `TEXT` | obrigatório |
| `psychology` | `JSONB` | obrigatório |
| `visual_structure` | `JSONB` | obrigatório |
| `best_for` | `JSONB` | obrigatório |
| `origin_video_id` | `INTEGER` | **FK → videos.id** · nullable |
| `embedding` | `VECTOR(1536)` | nullable |

### creators

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `platform` | `VARCHAR(32)` | obrigatório |
| `platform_creator_id` | `VARCHAR(64)` | obrigatório |
| `username` | `VARCHAR(200)` | obrigatório |
| `followers_count` | `INTEGER` | nullable |
| `raw` | `JSONB` | obrigatório |

### ideas

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `segment_id` | `INTEGER` | **FK → segments.id** · obrigatório |
| `pattern_id` | `INTEGER` | **FK → creative_patterns.id** · nullable |
| `title` | `TEXT` | obrigatório |
| `summary` | `TEXT` | obrigatório |
| `status` | `VARCHAR(24)` | obrigatório |
| `embedding` | `VECTOR(1536)` | nullable |
| `created_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a27b1c50; now>, for_update=False)` |

### performance_metrics

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `published_video_id` | `INTEGER` | **FK → published_videos.id** · obrigatório · indexado |
| `captured_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a27a6990; now>, for_update=False)` |
| `views` | `INTEGER` | nullable |
| `likes` | `INTEGER` | nullable |
| `comments` | `INTEGER` | nullable |
| `shares` | `INTEGER` | nullable |
| `saves` | `INTEGER` | nullable |
| `watch_time_s` | `INTEGER` | nullable |
| `retention_pct` | `DOUBLE` | nullable |
| `completion_rate` | `DOUBLE` | nullable |
| `followers_gained` | `INTEGER` | nullable |

### production_jobs

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `script_id` | `INTEGER` | **FK → scripts.id** · obrigatório |
| `status` | `VARCHAR(24)` | obrigatório |
| `scheduled_for` | `VARCHAR(16)` | nullable |
| `assets` | `JSONB` | obrigatório |
| `created_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a27a5400; now>, for_update=False)` |

### published_videos

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `script_id` | `INTEGER` | **FK → scripts.id** · obrigatório |
| `platform` | `VARCHAR(32)` | obrigatório |
| `url` | `TEXT` | obrigatório |
| `published_at` | `DATETIME` | obrigatório |

### scripts

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `idea_id` | `INTEGER` | **FK → ideas.id** · obrigatório |
| `title` | `TEXT` | obrigatório |
| `duration_s` | `INTEGER` | nullable |
| `hook` | `TEXT` | obrigatório |
| `scenes` | `JSONB` | obrigatório |
| `cta` | `TEXT` | obrigatório |
| `production_difficulty` | `INTEGER` | nullable |
| `status` | `VARCHAR(24)` | obrigatório |
| `created_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a27b2c50; now>, for_update=False)` |

### segments

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `slug` | `VARCHAR(64)` | UNIQUE · obrigatório |
| `name` | `VARCHAR(200)` | obrigatório |
| `config` | `JSONB` | obrigatório |
| `created_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a2b77cb0; now>, for_update=False)` |
| `updated_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a293ca50; now>, for_update=False)` |

### transcripts

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `video_id` | `INTEGER` | **FK → videos.id** · UNIQUE · obrigatório |
| `source` | `VARCHAR(32)` | obrigatório |
| `language` | `VARCHAR(16)` | nullable |
| `text` | `TEXT` | obrigatório |
| `segments` | `JSONB` | obrigatório |
| `created_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a2907240; now>, for_update=False)` |

### video_metrics

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `video_id` | `INTEGER` | **FK → videos.id** · obrigatório · indexado |
| `captured_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a2f68170; now>, for_update=False)` |
| `views` | `INTEGER` | nullable |
| `likes` | `INTEGER` | nullable |
| `comments` | `INTEGER` | nullable |
| `shares` | `INTEGER` | nullable |
| `saves` | `INTEGER` | nullable |
| `followers_at_capture` | `INTEGER` | nullable |

### video_scores

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `video_id` | `INTEGER` | **FK → videos.id** · obrigatório · indexado |
| `segment_id` | `INTEGER` | **FK → segments.id** · obrigatório |
| `computed_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a2906690; now>, for_update=False)` |
| `viral_score` | `FLOAT` | obrigatório · indexado |
| `performance` | `FLOAT` | obrigatório |
| `velocity` | `FLOAT` | obrigatório |
| `engagement` | `FLOAT` | obrigatório |
| `relevance` | `FLOAT` | obrigatório |
| `recency` | `FLOAT` | obrigatório |
| `trend_decay` | `VARCHAR(8)` | obrigatório |
| `replication_score` | `FLOAT` | nullable |
| `production_score` | `FLOAT` | nullable |

### video_segments

| Campo | Tipo | Nota |
|---|---|---|
| `video_id` | `INTEGER` | **FK → videos.id** · PK |
| `segment_id` | `INTEGER` | **FK → segments.id** · PK |
| `discovery_source` | `VARCHAR(64)` | obrigatório |
| `relevance_score` | `FLOAT` | nullable |
| `discovered_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a2895480; now>, for_update=False)` |

### videos

| Campo | Tipo | Nota |
|---|---|---|
| `id` | `INTEGER` | PK |
| `platform` | `VARCHAR(32)` | obrigatório · indexado |
| `platform_video_id` | `VARCHAR(64)` | obrigatório |
| `creator_id` | `INTEGER` | **FK → creators.id** · nullable |
| `transcript_id` | `INTEGER` | **FK → transcripts.id** (FK diferida — use_alter) · nullable |
| `url` | `TEXT` | obrigatório |
| `title` | `TEXT` | obrigatório |
| `description` | `TEXT` | obrigatório |
| `published_at` | `DATETIME` | nullable |
| `duration_s` | `INTEGER` | nullable |
| `language` | `VARCHAR(16)` | nullable |
| `thumbnail_url` | `TEXT` | nullable |
| `raw` | `JSONB` | obrigatório |
| `first_collected_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a293f750; now>, for_update=False)` |
| `last_collected_at` | `DATETIME` | obrigatório · default: `DefaultClause(<sqlalchemy.sql.functions.now at 0x7ed1a2894d60; now>, for_update=False)` |

## Vínculos

Vínculo só-por-código (sem FK declarada) — conferir intenção: `creators.platform_creator_id`, `videos.platform_video_id`
