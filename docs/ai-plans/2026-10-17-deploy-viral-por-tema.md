# Runbook — deploy do teste viral-por-tema (Fase A e Fase B)

Orquestrador/QA executa; o usuário aprova cada fase. Pré-registro:
`docs/manual/pre-registro-viral-por-tema.md`. Especificação e planos:
`docs/ai-plans/2026-10-16-especificacao-viral-por-tema.md` e blocos 1–6.

## Estado em 2026-10-17

| Item | Estado |
|---|---|
| Blocos 1–4 (Fase A: filtro, pool, atribuição, criadores, log de buscas, remedição, cron 4×/dia, vagas por origem, Trends com âncora) | na `main` (#50, #51, #52, #53, #55) |
| Pré-registro | na `main` (#55) |
| TikTok: Chromium da imagem e busca v7 | na `main` (#54); TikTok fora do teste |
| Bloco 5 (Fase B: `viral-v2`, `cf term-report`, hashtags) | em andamento (`feat/viral-por-tema-bloco-5`) |
| Bloco 6 (`ops-report` novo) | a fazer |
| Produção | worker `cf-worker-multi-segmento` **parado** desde 16/10 15:40 BRT; banco na migração **0009**; imagem anterior ao épico |
| Campanhas ativas no banco | **155, 156, 157** (segmentos 856 velas, 857 sabonetes, 858 resina), até 21/10 19:20 UTC — do teste antigo |
| Disco `/home` | 97% (19 GB livres) |

## Configuração do worker (versionada)

As envs do teste estão **explícitas** no `environment:` de
`.devcontainer/docker-compose.multi-segmento.yml`. Motivo: o `defaults.env`
não é montado em nenhum serviço, e o `environment:` vence o `app.env`. Os
segredos continuam em `.devcontainer/secrets/secrets.env` (fora do git).

| Env | Valor | Por quê |
|---|---|---|
| `COLLECT_PLATFORMS` | `youtube,web` | YouTube é a comparação; a web segue o experimento q1 próprio. TikTok (bloqueado, sem API oficial para empresa) e Instagram (só mídia própria) ficam fora |
| `COLLECT_LIMIT` | `25` | vídeos por busca |
| `COLLECT_CRON_HOURS` / `SCORE_CRON_HOURS` | `02,08,14,20` | 4 coletas e 4 scores por dia (score no minuto 30) |
| `SEARCHES_PER_COLLECT_YOUTUBE` | `4` | uma vaga por origem: seed, suggest, trends, hashtag |
| `TRENDS_TERMS_PER_DAY` | `16` | 4 lotes de 4 + âncora por segmento |
| `RESEARCH_SOCIAL_PLATFORMS` | `none` | research só faz Suggest/Trends/pool (cota) |
| `REMEDIATION_*` | `7` / `500` / `3` | janela, teto e intervalo mínimo da remedição |
| `VIRAL_V2_ENABLED` | `0` → `1` na Fase B | score novo ligado só com o bloco 5 no ar |
| `VIRAL_V2_WINDOW_DAYS` / `VIRAL_V2_MIN_POPULATION` | `30` / `5` | pré-registro |

Cota estimada do YouTube: ~5.100 de 10.000 unidades por dia.

## Decisões pendentes do usuário (antes do deploy)

1. **`COLLECT_PLATFORMS=youtube,web`** — confirmar a saída do Instagram (mídia
   própria) e do TikTok.
2. **Rendimento das buscas longas.** No teste real de 17/10, 2 das 4 buscas
   voltaram vazias: só vídeos publicados nos últimos 7 dias e em pt-BR.
   Manter como está (é resultado do teste; está no pré-registro) ou ampliar a
   janela de publicação da busca antes do início. Ampliar exige uma mudança
   de código pequena e uma nova versão do plano de busca.
3. **Limpeza dos 213 segmentos de teste** (`teste-partial-*`/`teste-skip-*`,
   criados de 09/10 a 13/10, com 396 vínculos de vídeo) no banco de produção.
   Nenhum tem campanha ativa (os jobs não os tocam), mas poluem as consultas.
   Proposta: apagar depois do backup, num passo próprio.

## Fase A — passos (executada em 2026-10-17; início 07:17 BRT)

Todos os comandos rodam na árvore principal do repositório.

```bash
DC="docker compose -f .devcontainer/docker-compose.yml -f .devcontainer/docker-compose.multi-segmento.yml --profile worker-multi"
RUN="$DC run --rm --no-deps -T worker sh -c"   # -T: sem TTY a saída se perde
SECRETS='set -a; . /run/secrets/app_secrets; set +a;'
```

1. **Pré-condições.** `main` atualizada com este PR; worker parado; fila
   vazia:
   ```bash
   git switch main && git pull --ff-only
   docker ps --filter name=cf-worker-multi-segmento --format '{{.Status}}'   # Exited
   docker exec content-factory-redis-1 redis-cli ZCARD arq:queue            # 0
   ```
2. **Backup** (banco de ~12 MB):
   ```bash
   B=~/content-factory-backups/2026-10-17-pre-fase-a && mkdir -p "$B"
   docker exec content-factory-postgres-1 pg_dump -U contentfactory -Fc contentfactory > "$B/db.dump"
   docker exec -i content-factory-postgres-1 pg_restore --list < "$B/db.dump" | grep -c "TABLE DATA"
   ```
3. **Imagem nova** (construída, ainda sem subir):
   ```bash
   $DC build worker
   ```
4. **Migrações 0010 → 0015** — a imagem do worker NÃO leva `alembic.ini` nem
   `alembic/` (o Dockerfile copia só `src/`, `README.md` e `segments/`).
   Rodar da árvore principal, na mesma `main` do deploy, contra o Postgres de
   produção pela porta local:
   ```bash
   DATABASE_URL='postgresql+psycopg://contentfactory:<senha>@127.0.0.1:5432/contentfactory' \
     .venv/bin/alembic upgrade head && .venv/bin/alembic check
   ```
5. **Segmentos** (sobe `probe_terms`, `anchor_terms`, `blocked_terms`,
   `accepted_language`):
   ```bash
   for s in velas-artesanais sabonetes-artesanais resina-artesanal; do
     $RUN "$SECRETS uv run cf segment load segments/$s.yaml"; done
   ```
6. **Campanhas:** encerrar as antigas e abrir as do teste. A CLI só aceita
   semanas: abre com 1 semana e encerra no D+4.
   ```bash
   for s in velas-artesanais sabonetes-artesanais resina-artesanal; do
     $RUN "$SECRETS uv run cf research stop --segment $s"
     $RUN "$SECRETS uv run cf research start --segment $s --weeks 1"; done
   ```
7. **Smoke da imagem — NÃO se aplica neste deploy.** O
   `scripts/smoke_worker_segments.sh` confere os protocolos `q1` da web
   (`segments/web-queries/<slug>.q1.yaml`), que **não existem** na `main`:
   foram removidos para serem regenerados depois do primeiro research com as
   sondas e o filtro R1. Com `WEB_QUERIES_REQUIRED` desligado (padrão), a
   coleta web roda no modo legado de 1 consulta, sem efeito no teste do
   YouTube. Regenerar os `q1` é pendência do experimento web (abaixo).
8. **Subir o worker.** O catch-up do startup roda research → collect → score
   perdidos; essa é a fumaça.
   ```bash
   $DC up -d worker && docker logs -f cf-worker-multi-segmento
   ```
9. **Adendo no pré-registro** com a data e a hora de início (BRT e UTC) e o
   commit da imagem — commit na `main`.

### Verificação D+0 (QA, depois da 1ª coleta)

```sql
-- buscas pelas vagas, cota e rendimento
SELECT s.slug, q.origin, count(*), sum(q.results), sum(q.quota_used)
FROM search_queries q JOIN segments s ON s.id = q.segment_id GROUP BY 1, 2 ORDER BY 1, 2;
-- 100% dos vídeos YouTube novos com atribuição
SELECT count(*) FROM video_segments vs JOIN videos v ON v.id = vs.video_id
WHERE v.platform = 'youtube' AND vs.discovered_at > now() - interval '1 day'
  AND NOT EXISTS (SELECT 1 FROM video_discoveries d WHERE d.video_id = v.id AND d.term_origin <> 'unattributed');
-- pool e Trends do research
SELECT s.slug, origin, status, count(*), count(trend_score) FROM segment_terms t
JOIN segments s ON s.id = t.segment_id GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
-- nenhum par de medições a menos de 1 h
SELECT count(*) FROM (SELECT captured_at - lag(captured_at) OVER (PARTITION BY video_id ORDER BY captured_at) g
FROM video_metrics) x WHERE g < interval '1 hour';
```

E `docker logs` sem traceback; `collect_runs` com `youtube` ok e `quota_used`
≈ 408 por segmento e coleta.

### Rollback da Fase A

```bash
docker stop cf-worker-multi-segmento
docker exec -i content-factory-postgres-1 pg_restore -U contentfactory -d contentfactory --clean < "$B/db.dump"
```

As migrações são aditivas: sem restaurar o backup, a imagem antiga ainda roda
sobre o schema novo (as colunas e tabelas novas ficam sem uso).

## Fase B — passos (depois do merge dos blocos 5 e 6)

1. Worker parado numa janela sem cron (entre os minutos 31 e 59 de um horário
   de score, ou fora de 02/08/14/20 h); fila vazia.
2. `$DC build worker` e `$RUN "$SECRETS uv run alembic upgrade head"` (0015).
3. `VIRAL_V2_ENABLED: "1"` no compose (commit) e `$DC up -d worker`.
4. Adendo no pré-registro: início da Fase B e da vaga de hashtag.
5. No score seguinte: `video_scores_v2` com linhas e `segment_terms` com
   `hashtag:youtube` ativas.

## Checkpoints do teste

| Quando | O quê |
|---|---|
| D+0 | verificação acima |
| D+1 | filtro (amostra cega de 13 termos por segmento + varredura completa do pool), remedição, `cf term-report --as-of` (se Fase B) |
| D+2 | velocidade não nula; hashtags no pool (se Fase B) |
| D+4 | `cf research stop` nos 3 segmentos; backup; **deploy do #64** (`$DC build worker`, `cf segment load` dos 3 segmentos, adendo com a hora); `cf term-report --as-of <início + 96 h> --out <fora do repo>` nos 3 — o relatório tem de sair com `rules_version` `term-filter-v1+relevance-v2+viral-v2` |

## Pendências fora do teste

- Experimento web (q1): regenerar os protocolos `q1` depois do 1º research
  da Fase A (`cf web-queries`), passar pelo veto do usuário, commitar em
  `segments/web-queries/`, rebuild e ligar `WEB_QUERIES_REQUIRED`; 5 coletas
  antes da amostra.

- Disco do `/home` em 97%: depois do deploy, apagar imagens antigas sem uso
  (`docker image prune`), depois de conferir o que é.
- TikTok: sem caminho oficial (a Research API não aceita empresa). Proxy
  residencial ou Display API (conta própria) só por decisão do usuário.
- Instagram: descoberta depende do App Review (busca por hashtag).
