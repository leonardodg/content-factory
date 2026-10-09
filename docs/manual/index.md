# Manual de operação

Este manual é o ponto de entrada para **operar o content-factory** no dia a dia:
subir o serviço, rodar buscas, pontuar e analisar resultados. É escrito para dois
públicos ao mesmo tempo — o **humano** que opera e o **agente de IA** que executa
via CLI `cf`. Cada capítulo é autossuficiente e linka de volta para este índice; o
contrato da CLI (envelope JSON de uma linha, exit codes) está em
[TRD CLI](../specs/trd-cli.md).

## Roteamento — quero… → capítulo

| Quero… | Ir para |
|---|---|
| Rodar a primeira busca (do zero ao primeiro coletor verde) | [Primeiro passo](guide-start.md) |
| Entender a lógica das buscas por plataforma | [Fluxo de busca](fluxo-busca.md) |
| Subir/verificar o serviço, logs e rotinas | [Operação](operacao.md) |
| Configurar o agente de IA (operador diário) | [Runbook do agente](agentes.md) |
| Analisar resultados (queries SQL prontas) | [Análise](analise.md) |
| Evitar ban / segurança operacional | [Anti-ban](anti-ban.md) |
| Criar chaves das APIs (YouTube, Instagram, TikTok) | [Chaves de API](chaves-api.md) |

## Pipeline

```mermaid
flowchart LR
    Y["segments YAML"] --> C["cf collect<br/>(lock → limiter → discover → upsert)"]
    C --> DB[("videos · video_segments<br/>collect_runs")]
    DB --> S["cf score"]
    S --> SC[("video_scores")]
    SC --> ST["cf status"]
```

Ciclo típico: segmento YAML carregado → `cf collect` busca candidatos (lock
Redis por segmento, limiter por plataforma, discover, upsert idempotente) e
audita tudo em `collect_runs` → `cf score` recalcula viral score + trend decay
em `video_scores` → `cf status` resume o estado operacional do segmento.

## Tabela verdade dos comandos `cf`

| Comando | O que faz | Exit codes |
|---|---|---|
| `cf ping` | healthcheck sem banco | `0` · `1` |
| `cf segment load PATH` | valida (pydantic) e grava/atualiza o segmento | `0` · `1` |
| `cf collect --segment SLUG [--platform a,b] [--limit N]` | coleta → upsert idempotente; `N` 1–50, default 10 | `0` · `2` · `1` |
| `cf ingest --url URL [--segment SLUG]` | enriquece URL fornecida e grava candidato | `0` · `1` |
| `cf score --segment SLUG` | recalcula viral score + trend decay | `0` · `2` · `1` |
| `cf status --segment SLUG` | resumo operacional | `0` · `1` |

**Exit codes:** `0`=ok · `2`=parcial (alguma plataforma falhou; o lote seguiu) ·
`1`=erro/uso — contrato normativo em [TRD CLI](../specs/trd-cli.md).

Notas de uso:

- A CLI roda **dentro** do container: `docker exec cf-python uv run cf <cmd>`.
- Saída é **uma linha JSON** (envelope `{"ok": …}` / `{"ok": false, "error": …}`);
  a CLI **não dumpa dados** — resultados ficam no banco e são lidos por lá
  ([ADR-0004](../adr/0004-cli-first.md)).

## Comandos por capítulo

O [Primeiro passo](guide-start.md) monta o caminho `ping → segment load →
collect → score → status` do zero. Detalhes de cada estágio: [Fluxo de
busca](fluxo-busca.md). Rotinas de subida, logs e worker: [Operação](operacao.md).
Rotina diária do agente: [Runbook do agente](agentes.md).
