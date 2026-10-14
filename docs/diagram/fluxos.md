# Fluxos da pipeline — content-factory

> Visão consolidada (mapa mental, relógio do worker, coletores, dados
> salvos, limites e reciclos): [Mapa do ciclo](mapa-do-ciclo.md).

Diagramas do **comportamento em runtime** após a concorrência do pipeline
(plano `../ai-plans/2026-10-13-content-factory-concorrencia-pipeline.md`).
O estado do banco está no [diagrama ER](README.md); o passo a passo do
`cf collect` está no [manual](../manual/fluxo-busca.md).

Convenção: blocos `|||` marcam fronteira de sessão Postgres (1 UoW por
segmento); setas `-.->` são leituras de config/env.

## 1. Visão geral — cron, jobs, segmentos, plataformas

```mermaid
flowchart TB
    subgraph CRON["arq · America/Sao_Paulo · max_jobs=3"]
        R["research_job<br/>07:00"]
        C["collect_job<br/>08:00 + 20:00"]
        S["score_job<br/>08:30 + 21:30"]
    end
    R --> SEGS["active_segments()<br/>campanhas ativas, não vencidas"]
    C --> SEGS
    S --> SEGS
    SEGS --> TG["TaskGroup + semáforo<br/>WORKER_MAX_SEGMENTS=3<br/>1 UoW por segmento"]
    TG --> SG1["segmento A<br/>sessão própria"]
    TG --> SG2["segmento B<br/>sessão própria"]
    TG --> SG3["segmento C<br/>sessão própria"]
    SG1 --> PLAT["TaskGroup + semáforo<br/>COLLECT_MAX_CONCURRENCY=2<br/>1 sessão compartilhada no segmento"]
    PLAT --> P1["youtube"]
    PLAT --> P2["instagram"]
    PLAT --> P3["web / tiktok"]
    P1 --> PER["persistência SEQUENCIAL<br/>upsert + collect_runs"]
    P2 --> PER
    P3 --> PER
```

Cada segmento commita ou reverte sozinho: a falha de um nunca reverte os
outros. Exceção não esperada é capturada **dentro** da task do segmento —
`segments` no envelope tem todos os slugs, sem abandono silencioso.

## 2. Startup e shutdown do worker

```mermaid
flowchart TD
    A["arq on_startup"] --> G{"COLLECT_SEGMENT<br/>definido?"}
    G -- sim --> E["RuntimeError<br/>imagem multi-segmento sob compose antigo"]
    G -- não --> R0["redis fila ← REDIS_URL"]
    R0 --> R1{"rate_limit_redis_url<br/>≠ REDIS_URL?"}
    R1 -- sim --> R2["redis compartilhado<br/>cota + breaker + locks"]
    R1 -- não --> SH["1 cliente só"]
    R2 --> ENG["engine único + session factory"]
    SH --> ENG
    ENG --> TPL["SharedDeps: coletores, providers,<br/>limiter, lock_client, platform_slots<br/>(sem sessão de startup)"]
    TPL --> CTX["ctx: app_deps + campaigns_deps"]
    CTX --> UP["worker pronto · 3 crons"]
    UP --> DOWN["arq on_shutdown"]
    DOWN --> CL["fecha clientes Redis<br/>+ engine.dispose()"]
```

`on_job_end` não existe mais: cada UoW reverte sozinha em exceção
(`session_scope`). Engine e clientes Redis são compartilhados
(task-safe); **sessão nunca é**.

## 3. `collect_job` — um segmento

```mermaid
flowchart TD
    A["tick: active_segments()"] --> B["bounded_gather por segmento<br/>(WORKER_MAX_SEGMENTS)"]
    B --> U["abrir UoW<br/>bundle = collect+score+research<br/>montado de SharedDeps + sessão do segmento<br/>(RedisLock próprio)"]
    U --> L{"lock collect:SLUG<br/>token de posse · TTL 30 min"}
    L -- ocupado --> J1["{ok: false, ja_em_execucao}"]
    L -- livre --> N["fase de rede · bounded_gather 2<br/>+ vaga por plataforma (tiktok=1,web=1)"]
    N --> D1["limiter.acquire + discover<br/>youtube"]
    N --> D2["limiter.acquire + discover<br/>instagram"]
    N --> D3["limiter.acquire + discover<br/>web / tiktok"]
    D1 --> RES["DiscoverResult por plataforma<br/>qualquer Exception capturada na task<br/>(fora do contrato: internal_error + log)"]
    D2 --> RES
    D3 --> RES
    RES --> P["fase de persistência SEQUENCIAL<br/>ordem de collectors<br/>upsert + runs.record"]
    P --> K["uow.commit()"]
    K --> O["{ok, partial, found/inserted/...}"]
    L -.->|qualquer saída| RL["lock.release token CAS<br/>finally"]
    J1 --> RL
    O --> RL
    P -.->|exceção fora de CollectorError| RB["uow.rollback()<br/>segmento failed, irmãs seguem"]
```

`CollectorError` (inclui `RateLimited` e `blocked` do breaker) vira
`collect_runs` `failed`/`blocked` + `failed = 1`, sem bater na API quando
bloqueado. `discover_ms` por plataforma na rede, `persist_ms` no bloco
sequencial.

## 4. `score_job` — um segmento

```mermaid
flowchart TD
    A["tick: active_segments()"] --> B["bounded_gather por segmento<br/>(WORKER_MAX_SEGMENTS)"]
    B --> U["abrir UoW"]
    U --> L["latest_for_segment()<br/>1 query · snapshots + anterior"]
    L --> V["fase pura · sem I/O<br/>relevance_of + viral_score<br/>~20 µs/vídeo"]
    V --> W["upsert_many em blocos de 500<br/>1 flush por bloco"]
    W -.->|bloco quebrou| F["fallback linha-a-linha<br/>só DataError/IntegrityError"]
    F --> K["uow.commit()"]
    W --> K
    K --> O["{ok, scored, partial}"]
    W -.->|conexão e afins| RB["propaga · uow.rollback()<br/>scored=0"]
```

`video_scores` é append-only: o lote é direto. Linha ruim isola no
savepoint; `partial = erros mas com persistidos`.

## 5. `research_job` — um segmento

```mermaid
flowchart TD
    A["tick 07:00 · active_segments()"] --> B["bounded_gather por segmento"]
    B --> U["abrir UoW<br/>guard = FanoutGuard(segment.research)"]
    U --> G["guard admite seeds<br/>ANTES do fan-out"]
    G --> WT["web + trends SEQUENCIAL<br/>CollectRun research:web_suggest / research:trends<br/>falha = run failed, ciclo SEGUE"]
    WT --> SIG["upsert signals + observations"]
    SIG --> KW{"algum sinal?"}
    KW -- sim --> SO["bounded_gather 2<br/>socials admitidos pelo guard"]
    KW -- não --> SEED["keywords = seeds do segmento"]
    SEED --> SO
    SO --> S1["discover_by_keywords<br/>youtube"]
    SO --> S2["discover_by_keywords<br/>instagram"]
    S1 --> PE["persistência SEQUENCIAL<br/>upsert_candidates + CollectRun<br/>research:plataforma"]
    S2 --> PE
    PE --> K["uow.commit()"]
    K --> O["{ok, partial, counts}"]
```

O guard é consultado **antes** de agendar cada task (ele muta estado
interno e não é task-safe). Erro de 1 rede social não aborta as demais, e
erro do Suggest/Trends (incluindo `circuit_open`) também não: vira run
`failed` e `web_failed`/`trends_failed`/`source_errors` no envelope.

## 6. CLI — envelope na saída, dados no banco

```mermaid
flowchart LR
    subgraph CLI["cf · exit 0/1/2"]
        C1["collect --segment --limit"]
        C2["score --segment"]
        C3["collect-timings --segment"]
        C4["research start/stop/status"]
        C5["segment load · status · ping"]
    end
    C1 --> E1["{ok, partial, counts}"]
    C2 --> E2["{ok, scored}"]
    C3 --> E3["{ok, timings}"]
    C4 --> E4["{ok, ...}"]
    C5 --> E5["{ok, ...}"]
    E1 -.->|exit 2| P["partial"]
    E1 -.->|exit 1| F["falha / ja_em_execucao"]
```

A CLI monta os deps de cada comando (não usa `AppDeps` do worker) e nunca
imprime linhas do banco — só o envelope.

## 7. Redis — fila, cota e locks

```mermaid
flowchart TB
    subgraph Q["Redis fila · REDIS_URL"]
        Q0["/0 · worker único atual"]
        Q1["/1 · worker-velas legado"]
        Q2["/2 · worker-sabonetes legado"]
        Q3["/3 · worker-resina legado"]
    end
    subgraph SH["Redis compartilhado · rate_limit_redis_url → /0"]
        RL["cf:rl:plataforma<br/>janelas: yt 100/dia · tt 6/min · ig 20/60s"]
        CB["cf:cb:plataforma:*<br/>breaker 3 falhas → blocked 15 min"]
        LK["collect:/research:SLUG<br/>lock com token · TTL 30 min"]
    end
    Q0 --> SH
    Q1 -.->|legado pré-2026-10-19| SH
    Q2 -.->|legado pré-2026-10-19| SH
    Q3 -.->|legado pré-2026-10-19| SH
```

Com um banco Redis por worker legado, cada um tinha sua própria janela e
breaker — a cota real podia ser consumida em até 3x. O worker único + o
Redis compartilhado eliminam o problema por construção; o segundo cliente
permanece para futuros workers por plataforma.
