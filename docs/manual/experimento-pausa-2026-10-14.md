# Experimento — pausa de 2026-10-14

O experimento de 7 dias (3 workers legados, 1 por segmento, imagem
pré-concorrência) foi **pausado antes do prazo**, por decisão do usuário, em
**2026-10-14 15:21:26 UTC (12:21 BRT)**. O objetivo foi aplicar as correções
da [revisão sênior](../REVISAO_2026-10-14.md) e reativar o teste real dos 3
segmentos já no worker único multi-segmento. Os 3 containers estavam
ociosos na parada (`j_ongoing=0`, último job às 12:00 UTC) e saíram com
código 0.

## Backup (local, fora do repositório)

`~/backups/content-factory/2026-10-14-experimento-pausa/`, com permissão `700`:

| Pasta | Conteúdo |
| --- | --- |
| `db/contentfactory.dump` | `pg_dump -Fc` completo (validado com `pg_restore --list`: 18 tabelas com dados) |
| `csv/` | todas as tabelas de `public` em CSV |
| `logs/` | `docker logs --timestamps` dos 3 workers, do Postgres e do Redis |
| `redis/` | chaves com TTL por banco + `dump.rdb` |
| `meta/` | imagens e IDs dos containers, commit da `main`, versão do alembic, compose usados |

Logs e dump **não** vão para o git: podem conter dados de terceiros e
mensagens de bibliotecas não redigidas (ver R3 da revisão).

Restaurar em um Postgres **descartável**:
`pg_restore -d <db> --no-owner db/contentfactory.dump`.

## Janela e volume

| Segmento | Primeira run (UTC) | Última run (UTC) | Runs | Vídeos únicos | Plataformas com vídeo |
| --- | --- | --- | --- | --- | --- |
| resina-artesanal | 2026-10-13 11:00 | 2026-10-14 12:00 | 21 | 12 | 2 |
| sabonetes-artesanais | 2026-10-13 11:00 | 2026-10-14 12:00 | 15 | 13 | 3 |
| velas-artesanais | 2026-10-13 16:50 | 2026-10-14 12:00 | 13 | 11 | 3 |

São cerca de 1,5 dia de coleta. Cada um dos 3 segmentos ficou com
**36 vídeos únicos**.

## Por coletor

| Segmento | Coletor | Status | Runs | Found | Inserted | Updated |
| --- | --- | --- | --- | --- | --- | --- |
| resina | instagram | ok | 6 | 0 | 0 | 0 |
| resina | tiktok | failed (`auth_expired`) | 3 | 0 | 0 | 0 |
| resina | web | ok | 6 | 18 | 9 | 9 |
| resina | youtube | ok | 6 | 18 | 3 | 15 |
| sabonetes | instagram | ok | 4 | 12 | 3 | 9 |
| sabonetes | tiktok | failed (`auth_expired`) | 3 | 0 | 0 | 0 |
| sabonetes | web | ok | 4 | 12 | 5 | 7 |
| sabonetes | youtube | ok | 4 | 12 | 5 | 7 |
| velas | instagram | ok | 4 | 12 | 0 | 12 |
| velas | tiktok | failed (`auth_expired`) | 1 | 0 | 0 | 0 |
| velas | web | ok | 4 | 12 | 5 | 7 |
| velas | youtube | ok | 4 | 12 | 3 | 9 |

## Métricas e score

| Plataforma | Snapshots | Vídeos | Snapshots com views |
| --- | --- | --- | --- |
| youtube | 42 | 11 | 42 (100%) |
| instagram | 24 | 6 | 0 (a Graph API não expõe views de terceiros) |

| Segmento | Scores | Vídeos pontuados | Média | Scores com `velocity` |
| --- | --- | --- | --- | --- |
| resina-artesanal | 68 | 12 | 30,1 | 21 |
| sabonetes-artesanais | 79 | 13 | 33,2 | 11 |
| velas-artesanais | 69 | 11 | 29,2 | 9 |

## O que o período mostrou

- **O research nunca gravou nada.** Não há nenhuma run `research:*` e
  `keyword_signals` está vazia. O `research_job` abortou nos 3 segmentos
  nos 2 dias:
  - dia 13: Trends HTTP 400 (`UpstreamChanged`);
  - dia 14: DNS indisponível (`NetworkError`).

  O código antigo abortava o segmento inteiro; o código novo segue com as
  fontes que responderem (PR #10).
- **Cada worker legado tinha a própria janela `cf:rl:youtube`** (Redis
  db1/2/3). A cota real do YouTube podia ser consumida 3×; o worker único
  usa uma janela compartilhada.
- **Restart do S.O. (13/10 ~21:49 BRT):** o Redis demorou a resolver, os
  workers retentaram (`5 retries remaining…`) e se recuperaram sozinhos.
- **TikTok:** 100% `auth_expired`, porque `TIKTOK_MS_TOKEN` está vazio por
  design.
- **Instagram:** sem views para terceiros e com fallback de mídia própria
  (gate #10). Por isso a `velocity` só sai para YouTube.
- **Volume pequeno demais para validar o ranking (B3):** a validação
  empírica fica para depois da reativação, com a janela nova completa.

## Reativação

Ver o plano
[`ai-plans/2026-10-14-content-factory-revisao-senior.md`](../ai-plans/2026-10-14-content-factory-revisao-senior.md)
(§ Execução 2026-10-14). As campanhas antigas, que terminariam em
2026-10-20 02:25 UTC, são encerradas, e novas campanhas de 7 dias começam
no momento da reativação, para que a janela do teste novo fique limpa.
