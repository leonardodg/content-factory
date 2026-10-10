# Análise — queries SQL prontas

Este capítulo é a **caixa de ferramentas de leitura** do banco: seis queries
prontas para colar no `psql`, cada uma respondendo uma pergunta do dia a dia —
quem está no topo do segmento, como o score evoluiu, se as coletas de 7 dias
estão saudáveis, quais erros aconteceram, quantos vídeos estão sem métricas e
quanto do segmento foi pontuado. O que cada tabela recebe e por quê está no
[Fluxo de busca](fluxo-busca.md); operar o serviço (status, logs, cron):
[Operação](operacao.md); consumir estas queries na rotina:
[Runbook do agente](agentes.md).

Todas as queries abaixo são `SELECT` — nada aqui escreve no banco. Schema
campo a campo: [Data model](../data-model/README.md).

## 1. Acesso

```bash
docker compose -f .devcontainer/docker-compose.yml exec -it postgres psql -U contentfactory -d contentfactory
```

Convenção do manual: compose sempre com `-f .devcontainer/docker-compose.yml`
([Operação §1](operacao.md#1-subir-a-stack)). Por convenção do manual, acesse
o banco **apenas** via `docker compose exec` — a porta `5432` existe, mas o
compose publica só em `127.0.0.1`: não conecte de fora da stack (regra do
[Runbook do agente](agentes.md)).

## 2. Top 10 do segmento

**O que responde:** quais vídeos do segmento `candles` lideram o `viral_score`
(com relevância, recência e horário do cálculo) — a query do passo 3 do
[Runbook do agente](agentes.md).

```sql
SELECT v.title, v.platform, sc.viral_score, sc.relevance, sc.recency, sc.computed_at
FROM video_scores sc
JOIN videos v ON v.id = sc.video_id
JOIN segments seg ON seg.id = sc.segment_id
WHERE seg.slug = 'candles'
ORDER BY sc.viral_score DESC
LIMIT 10;
```

Troque `seg.slug` para analisar outro segmento. `video_scores` é timeseries:
1 linha por cálculo, append sem `ON CONFLICT` ([Fluxo §3](fluxo-busca.md#3-workflow-ate-o-banco)) —
com mais de um `cf score` no histórico o mesmo vídeo aparece uma vez por
`computed_at`.

## 3. Evolução do score (timeseries)

**O que responde:** como o `viral_score` evoluiu nos últimos 20 cálculos —
para ver se o ranking muda entre execuções.

```sql
SELECT computed_at, viral_score
FROM video_scores
ORDER BY computed_at DESC
LIMIT 20;
```

`DESC` entrega os últimos N; troque para `ASC` para ver do começo, ou
acrescente `WHERE video_id = <id>` para acompanhar a curva de um vídeo só.

## 4. Saúde das coletas — 7 dias

**O que responde:** quantas coletas cada plataforma teve por status nos
últimos 7 dias, com o que cada uma achou, inseriu e falhou (a mesma janela do
`cf status`).

```sql
SELECT collector, status, count(*) AS runs,
       sum(found) AS found, sum(inserted) AS inserted, sum(failed) AS failed
FROM collect_runs
WHERE started_at > now() - interval '7 days'
GROUP BY collector, status
ORDER BY collector;
```

Leitura: linha `ok` com `failed = 0` é o estado bom; runs `failed`/`blocked`
caem no diagnóstico por `error_code` da próxima query — códigos e o significado
de cada exit estão no [Primeiro passo §9](guide-start.md#9-primeiras-falhas-o-que-significam-os-exit-codes).

## 5. Últimos erros

**O que responde:** as 10 execuções mais recentes que não terminaram `ok`,
com plataforma, código e horário — os três campos do relato de escalonamento.

```sql
SELECT collector, error_code, started_at
FROM collect_runs
WHERE status <> 'ok'
ORDER BY started_at DESC
LIMIT 10;
```

Ação por `error_code` (`auth_expired`, `rate_limited`, `blocked`, `network`,
`upstream_changed`, `ja_em_execucao`): tabela do
[Fluxo §1](fluxo-busca.md#1-o-que-acontece-quando-cf-collect-roda) e do
[Runbook do agente](agentes.md). Sem `error_code` (ou `status = ok` com
problema em outro passo): o traceback está no log do worker
([Operação §3](operacao.md#3-logs)).

## 6. Vídeos sem métricas (zero-fill)

**O que responde:** quantos vídeos do banco não têm nenhuma linha em
`video_metrics` — o tamanho do zero-fill que hoje afeta todos os scores.

```sql
SELECT count(*)
FROM videos v
LEFT JOIN video_metrics m ON m.video_id = v.id
WHERE m.video_id IS NULL;
```

**Aviso (verdade documentada):** `video_metrics` **não é capturada em
produção** — só testes escrevem nela. Como o `cf score` usa `LEFT JOIN` com
zero-fill, o score desses vídeos é **placeholder**: `performance` e
`engagement` ficam em 0 e o ranking reflete praticamente só recência e
relevância textual. Trate o `viral_score` como ordem de grandeza, não como
métrica. Detalhes: [Fluxo de busca §4](fluxo-busca.md#4-verdade-documentada-limites-honestos).

## 7. Cobertura do segmento

**O que responde:** por segmento, quantos vídeos estão vinculados
(`video_segments`) e quantos já receberam score (`video_scores`) — a lacuna é
o que falta pontuar.

```sql
SELECT seg.slug,
       count(DISTINCT vs.video_id) AS vinculados,
       count(DISTINCT sc.video_id) AS pontuados
FROM segments seg
LEFT JOIN video_segments vs ON vs.segment_id = seg.id
LEFT JOIN video_scores sc ON sc.segment_id = seg.id
GROUP BY seg.slug
ORDER BY seg.slug;
```

`pontuados < vinculados` → faltou `cf score` (ou algum vídeo falhou — como
rodar: [Primeiro passo §7](guide-start.md#7-pontuar)). `pontuados` conta
vídeos **distintos**: um `count(*)` em `video_scores` seria maior, porque a
tabela é timeseries (§2).

---

Esta página é **só leitura**: os dados chegam pela CLI, que devolve apenas o
envelope JSON de uma linha (`{"ok": …}`) e deixa os detalhes no banco
([TRD CLI](../specs/trd-cli.md)) — para operar o serviço:
[Operação](operacao.md). Sair do `psql`: `\q`.
