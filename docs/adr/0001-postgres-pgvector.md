# ADR-0001 — Banco de dados: PostgreSQL 16 + pgvector

**Situação:** Aceita · **Data:** 2026-10-07

## Contexto

O content-factory precisa armazenar entidades estruturadas (segments, videos,
metrics, scores, patterns, ideas, scripts), agregar séries temporais de
métricas (velocity, trend decay), guardar metadata heterogênea por plataforma
(JSONB) e, nas fases de IA, busca semântica por embeddings. O projeto adota a
pilha da casa (Alembic, gates `alembic check`, docs data-model/diagram gerados
do schema real), o que pressiona por um banco com migrations formais.

## Decisão

Vamos usar **PostgreSQL 16 com extensão pgvector** (`pgvector/pgvector:pg16`)
como banco único de verdade.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| MongoDB 8 | JSON flexível, mas sem Alembic (gates da casa quebrariam), busca vetorial menos madura, experiência de data-model/diagram divergiria do padrão |
| Híbrido Postgres + Mongo (cru) | Máquina de duas fontes = mais operação e mais custo, sem benefício no volume atual |

## Consequências

- **Fica mais fácil:** SQL/agregações para scores e decay, JSONB para payloads
  por plataforma, embeddings no mesmo banco, `alembic check` como gate real.
- **Fica mais difícil:** payloads muito crus/large precisam de disciplina
  (coluna `raw JSONB` + retenção); sem flexibilidade de schema dinâmico por
  plataforma (mitigado com JSONB).

## Como saber que erramos

Coletores passam a gerar payloads crus maiores que ~1MB/registro com frequência
ou a busca semântica exigir infra vetorial dedicada → reavaliar armazenamento
de objetos (MinIO/S3) ou extensão vetorial separada.
