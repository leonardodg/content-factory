# Dev — como se desenvolve aqui

!!! warning "Docs descrevem o sistema **como está hoje**"
    A fonte da verdade é o código e o [HANDOFF](../HANDOFF.md); se divergir,
    confira contra `src/` e corrija o doc.

| Doc | O que cobre |
|---|---|
| [dev-environment](dev-environment.md) | subir compose, `TEST_DATABASE_URL`, alembic, gates |
| [extending](extending.md) | novo segmento = YAML · novo collector = classe + registro |
| [credentials](credentials.md) | onde ficam segredos, rotação de tokens, conta secundária TikTok |

## Source layout at a glance

```text
src/content_factory/
├── config.py            # Settings (pydantic-settings): DATABASE_URL, REDIS_URL, tokens
├── domain/              # PURO — sem I/O, sem datetime.now()
│   ├── models.py        # Segment, CandidateVideo, VideoMetrics, ScoreResult, CollectRun…
│   ├── scoring.py       # viral_score(), trend_decay(), engagement_rate() — puros
│   └── errors.py        # DomainError + CollectorError tipados (auth_expired, rate_limited…)
├── app/
│   ├── ports.py         # Protocolos: Collector, repositórios, RateLimiter, Lock
│   ├── segment.py       # load YAML → validação pydantic → upsert do segmento
│   ├── collect.py       # orquestra coleta (previsto — plano task 11)
│   ├── score.py         # recalc scores a partir de metrics (previsto — task 16)
│   ├── ingest.py        # enriquece URL fornecida por agente (previsto — task 15)
│   └── status.py        # consolidação operacional (previsto — task 17)
├── infra/
│   ├── collectors/      # registry + youtube/tiktok/instagram/web (previsto — tasks 10–15)
│   ├── db/              # engine, ORM (14 tabelas), repositórios, env do Alembic
│   ├── queue/           # arq tasks + cron 2×/dia (previsto — task 17)
│   ├── ssrf.py          # guarda de URL pública p/ ingest (previsto — task 15)
│   └── ratelimit.py     # token bucket em Redis (previsto — task 11)
└── cli/
    ├── main.py          # Typer: comandos cf (segment load, ping; resto na fase B)
    └── output.py        # envelope JSON de status + exit codes 0/2/1
```

Testes espelham `src/` 1:1 em `tests/` (TDD por task; mock só na fronteira).

## Comandos que você vai rodar todo dia

```bash
uv run cf ping                          # healthcheck do CLI (sem banco)
uv run pytest -q                        # suíte (precisa TEST_DATABASE_URL p/ integração)
uv run alembic check                    # modelo ≡ migrations
make gates                              # tudo: test lint typecheck security docs privacy
```

Detalhes e URLs: [dev-environment](dev-environment.md) ·
Extensões: [extending](extending.md).
