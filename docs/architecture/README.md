# Arquitetura — visão geral

Visão do sistema **como está**. Decisões estruturais (com alternativas)
ficam nos [ADRs](../adr/0001-postgres-pgvector.md); requisitos técnicos por
feature, nos [TRDs](../specs/trd-collectors.md).

| Documento | O que cobre |
|---|---|
| Esta página | Camadas, regra de dependência, superfícies |
| [Estado e fases](estado-e-proximas-fases.md) | O que existe hoje, o que falta, próximas fases |

## Camadas

| Camada | Papel | Regra |
|---|---|---|
| `cli/`, `workers/` | entrypoints (Typer, arq tasks) — só I/O de borda | chamam `app/`; nunca acessam `infra/` diretamente |
| `app/` | casos de uso: `collect`, `score`, `status`, `segment` | depende só de `ports.py` (Protocolos) e de `domain/` |
| `domain/` | entidades, `viral_score()`/`trend_decay()`, erros de negócio | **puro**: sem SQL/HTTP/arquivo; `now` é parâmetro onde o tempo importa |
| `infra/` | collectors, db (SQLAlchemy 2 + Alembic), queue (arq), redis | **implementa** os Protocolos; injetado nos entrypoints (DI manual) |

- **Dependência unidirecional:** `cli/workers → app → domain`; `infra`
  aponta para `ports.py` (para `app`), nunca o contrário.
- **Troca de estratégia:** TikTok-Api quebrar → nova implementação de
  `Collector`, zero mudança em `app/`/`domain/`
  ([ADR-0003](../adr/0003-coletores-fontes.md)).
- **Layout completo** de `src/` com um comentário por módulo:
  [dev/index](../dev/index.md).

## Superfícies

| Surface | Port | O que é |
|---|---|---|
| CLI `cf` | terminal/agentes | única interface de escrita; envelope JSON de status ([ADR-0004](../adr/0004-cli-first.md)) |
| Banco PostgreSQL | rede interna | fonte de verdade; agentes leem direto ([ADR-0001](../adr/0001-postgres-pgvector.md)) |
