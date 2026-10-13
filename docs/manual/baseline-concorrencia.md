# Baseline de concorrência — experimento em curso (pré-concorrência)

Congelado em 2026-10-13 ~17:30 UTC, SEM alterar containers (só `SELECT` no
Postgres do experimento). Código: **pré-concorrência** — imagens de
`2026-10-12 23:26`, anteriores ao worker multi-segmento (`c9f1e41`+); cada
worker coleta 1 segmento, sequencialmente, 1 plataforma por vez.

As colunas `discover_ms`/`persist_ms` (Task 4, migração 0009) estão **nulas**
para todo este período — esperado e documentado. A duração por run abaixo é
`finished_at - started_at` (rede + persistência somadas).

## Janela

| Segmento | Primeira run | Última run |
| --- | --- | --- |
| resina-artesanal | 2026-10-13 11:00 UTC | 2026-10-13 17:29 UTC |
| sabonetes-artesanais | 2026-10-13 11:00 UTC | 2026-10-13 17:28 UTC |
| velas-artesanais | 2026-10-13 16:50 UTC | 2026-10-13 17:28 UTC |

## Runs por segmento/plataforma

| Segmento | Plataforma | Status | Runs | Found | Inserted | Updated | Failed | Skipped |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| resina-artesanal | instagram | ok | 4 | 0 | 0 | 0 | 0 | 0 |
| resina-artesanal | tiktok | failed | 1 | 0 | 0 | 0 | 1 | 0 |
| resina-artesanal | web | ok | 4 | 12 | 5 | 7 | 0 | 0 |
| resina-artesanal | youtube | ok | 4 | 12 | 3 | 9 | 0 | 0 |
| sabonetes-artesanais | instagram | ok | 2 | 6 | 3 | 3 | 0 | 0 |
| sabonetes-artesanais | tiktok | failed | 1 | 0 | 0 | 0 | 1 | 0 |
| sabonetes-artesanais | web | ok | 2 | 6 | 3 | 3 | 0 | 0 |
| sabonetes-artesanais | youtube | ok | 2 | 6 | 3 | 3 | 0 | 0 |
| velas-artesanais | instagram | ok | 3 | 9 | 0 | 9 | 0 | 0 |
| velas-artesanais | web | ok | 3 | 9 | 5 | 4 | 0 | 0 |
| velas-artesanais | youtube | ok | 3 | 9 | 3 | 6 | 0 | 0 |

Leituras:

- TikTok `failed` 1x por segmento — `TIKTOK_MS_TOKEN` vazio por design
  (sessão Playwright sem token); esperado, não é regressão.
- Instagram resina 4 runs com `found=0` — fallback own-media sem vídeo novo
  no período (filtro `media_type` da Fase 0); `ok` com zero é normal.
- Vídeos únicos nos 3 segmentos: **28**.

## Duração por run (rede + persistência, sem separar)

| Segmento | Duração média (s) | Duração máx (s) |
| --- | --- | --- |
| resina-artesanal | 2,31 | 4,86 |
| sabonetes-artesanais | 1,46 | 2,25 |
| velas-artesanais | 2,04 | 4,68 |

## Critério de comparação (Etapa A)

Refazer as mesmas queries após a Etapa A num banco de teste com volume
equivalente e comparar: a duração total por segmento deve cair
aproximadamente para o **máximo** das plataformas em vez da **soma**, com
`found`/`inserted`/`updated` equivalentes. O `discover_ms`/`persist_ms` em
`collect_runs` separa então o ganho de rede do custo de persistência.
