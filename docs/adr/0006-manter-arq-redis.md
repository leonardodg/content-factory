# ADR-0006 — Manter `arq` + Redis (sem migração para RabbitMQ)

**Situação:** Aceita · **Data:** 2026-10-13

## Contexto

Durante o plano de concorrência do pipeline
(`docs/ai-plans/2026-10-13-content-factory-concorrencia-pipeline.md`), o
`insta_kb` foi avaliado como referência. Ele é boa referência de
**confiabilidade** (durabilidade, retry, DLQ, idempotência, separação
API/worker), mas não de paralelismo — seu worker também consome uma
mensagem por vez (`prefetch_count=1`, `pika.BlockingConnection`, callback
síncrono).

## Decisão

Manter **Redis + arq** (ADR-0002). A concorrência necessária é de I/O
dentro do processo (`asyncio.TaskGroup` + semáforo na descoberta, Etapa A;
segmentos concorrentes com sessão por unidade de trabalho, Etapa B) e de
round-trips (`upsert_many`, Etapa C) — nenhum gargalo está na fila.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Migrar para RabbitMQ | Mudança arquitetural sem relação com o gargalo de I/O medido (Task 12: CPU ~20 µs/vídeo, custo em round-trips Postgres); sem fan-out de milhares de tarefas independentes, o ganho é zero e o custo operacional é real |

## Consequências

- **Revisitar se:** o pipeline passar a gerar milhares de tarefas
  independentes por vídeo/keyword (fan-out que justifique routing/DLX).
- **Fica mais fácil:** nenhuma migração, nenhum serviço novo para operar
  durante o experimento.
