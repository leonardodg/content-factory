# ADR-0002 — Fila e trabalho assíncrono: Redis + arq

**Situação:** Aceita · **Data:** 2026-10-07

## Contexto

Os jobs do MVP são coleta agendada (2×/dia por segmento) e comandos
disparados por agentes via CLI. Ainda não existe fan-out de muitos tipos de
job nem processamento pesado (transcrição/geração de vídeo vêm depois). O
sistema também precisa de rate-limit por plataforma e lock de execução única
dos coletores.

## Decisão

Vamos usar **Redis 7 + arq** para fila de workers, cache, token-bucket de
rate-limit e locks. O Redis roda em **rede interna do compose, sem porta
pública, sem senha em dev** (rede interna é a autenticação no ambiente local).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| RabbitMQ | Fila mais robusta (routing, DLX, prioridades), mas overkill sem fan-out; mais um serviço para operar no MVP |
| Sem fila (cron/CLI puro) | Jobs longos e retries futuros (transcrição, análise) precisarão de fila; Redis já é necessário p/ rate-limit e lock — custo incremental zero |

## Consequências

- **Fica mais fácil:** 1 container serve fila + cache + rate-limit + lock;
  arq é async nativo com retries.
- **Fica mais difícil:** Redis sem senha não sobrevive a exposição de rede —
  nunca publicar porta fora de `127.0.0.1` explícito; arq é menos maduro que
  Celery em ecossistema.

## Como saber que erramos

Precisarmos de mensagens duráveis com garantia de entrega entre serviços ou
routing complexo → migrar fila para RabbitMQ (novo ADR superando este),
mantendo Redis p/ cache/rate-limit.
