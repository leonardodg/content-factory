# Experimento de 7 dias — 3 segmentos em paralelo

> Runbook operacional, não documentação permanente — não está no `nav` do
> mkdocs. Guarda os comandos exatos e as queries de monitoramento pro
> teste prolongado do worker (velas / sabonetes / resina).

## Pré-condições já verificadas nesta sessão

- Os 3 YAMLs (`segments/velas-artesanais.yaml`, `segments/sabonetes-artesanais.yaml`,
  `segments/resina-artesanal.yaml`) validam contra `SegmentConfig` real
  (16 keywords cada: 10 pt-BR + 6 en, marcadas em `subnichos.idioma_en` —
  metadado puro, não consumido pelo pipeline, só pra separar na análise).
- `platforms: [youtube, tiktok, instagram, web]` em todos — **sem**
  `google_trends` na lista: Trends não é uma plataforma de
  `discover_by_keywords`, roda automático em todo ciclo de pesquisa via
  `deps.trends` (ver `app/research_pipeline.py`).
- **Achado importante**: `.devcontainer/env/app.env` tem
  `COLLECT_PLATFORMS=web,youtube,instagram` fixo (sobra do playtest
  anterior) — sem override, isso ignoraria `tiktok` em TODOS os workers,
  mesmo ele estando na lista de `platforms` do segmento. O compose do
  experimento (abaixo) zera essa variável por worker pra cair no fallback
  real (`segment.platforms`, que inclui tiktok).
- **Achado de arquitetura**: o worker roda 1 único segmento por processo
  (`COLLECT_SEGMENT`) e usa a fila default do `arq`. Rodar 3 cópias do
  mesmo service sem separar faria elas competirem na mesma fila — o cron
  `unique=True` dispararia `research_job` só 1x no total, não 1x por
  segmento. `.devcontainer/docker-compose.experimento-7dias.yml` resolve
  isso dando a cada worker seu próprio índice de banco Redis (`/1`, `/2`,
  `/3` — `/0` fica livre pro uso normal do CLI/dev). Zero mudança de
  código, só configuração.
- `segment.research` (guard-rail de fan-out) É respeitado pelo worker real
  (`create_app_deps`: `FanoutGuard(segment.research or DepthConfig())`) —
  os 3 YAMLs deixam em branco (default: `max_depth=2`,
  `max_nodes_per_cycle=200`), suficiente pra 16 keywords seed.

## Fase 1 — carregar os segmentos (ainda não executado)

```bash
cf segment load segments/velas-artesanais.yaml
cf segment load segments/sabonetes-artesanais.yaml
cf segment load segments/resina-artesanal.yaml
```

Confirmar os 3 (`{"ok": true, ...}` sem `segment_not_found` depois):

```bash
cf research status --segment velas-artesanais
cf research status --segment sabonetes-artesanais
cf research status --segment resina-artesanal
```

## Fase 2 — iniciar as campanhas e subir os 3 workers

```bash
cf research start --segment velas-artesanais --weeks 1
cf research start --segment sabonetes-artesanais --weeks 1
cf research start --segment resina-artesanal --weeks 1

docker compose -f .devcontainer/docker-compose.yml \
  -f .devcontainer/docker-compose.experimento-7dias.yml \
  --profile experimento up -d worker-velas worker-sabonetes worker-resina
```

Cron herdado do `WorkerSettings` (igual pros 3, horário BRT):
research 07:00 · collect 08:00/20:00 · score 08:30/21:30.

### Dia 1 — validar os primeiros registros

```bash
# roda na hora, sem esperar o cron (1x por segmento)
cf collect --segment velas-artesanais --limit 10
cf collect --segment sabonetes-artesanais --limit 10
cf collect --segment resina-artesanal --limit 10
cf score --segment velas-artesanais
cf score --segment sabonetes-artesanais
cf score --segment resina-artesanal
cf status --segment velas-artesanais
```

Checar logs dos 3 containers (`docker logs cf-worker-velas-artesanais`
etc.) pra confirmar que o cron 07h/08h já rodou sozinho pelo menos uma vez
antes de fechar o dia 1.

## Fase 3 — o que medir no banco (rodar a cada dia 2-6)

Todas as queries abaixo pedem o `segment_id` — resolver uma vez:

```sql
select slug, id from segments
where slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal');
```

**1. Execuções realizadas e falhas (estabilidade da coleta), por segmento/plataforma:**

```sql
select s.slug, cr.collector, cr.status, count(*) as runs,
       sum(cr.failed) as failed_total, sum(cr.skipped) as skipped_total
from collect_runs cr
join segments s on s.id = cr.segment_id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1, 2, 3
order by 1, 2, 3;
```

**2. Candidatos encontrados vs. vídeos únicos, por segmento:**

```sql
select s.slug,
       sum(cr.found) as candidatos_encontrados,
       count(distinct vs.video_id) as videos_unicos
from collect_runs cr
join segments s on s.id = cr.segment_id
left join video_segments vs on vs.segment_id = s.id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1;
```

**3. Inseridos / atualizados / ignorados (upsert comportando certo), por segmento/plataforma:**

```sql
select s.slug, cr.collector,
       sum(cr.inserted) as inserted, sum(cr.updated) as updated,
       sum(cr.skipped) as skipped
from collect_runs cr
join segments s on s.id = cr.segment_id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1, 2
order by 1, 2;
```

**4. Vídeos com métricas disponíveis (cobertura real):**

```sql
select s.slug, count(distinct vm.video_id) as videos_com_metrica
from video_metrics vm
join video_segments vs on vs.video_id = vm.video_id
join segments s on s.id = vs.segment_id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1;
```

**5. Vídeos com 2+ capturas válidas de `views` (momentum calculável — o
indicador mais importante da semana, direto da limitação que a review
apontou):**

```sql
select s.slug, count(*) as videos_com_momentum_calculavel
from (
  select vs.segment_id, vm.video_id
  from video_metrics vm
  join video_segments vs on vs.video_id = vm.video_id
  where vm.views is not null
  group by vs.segment_id, vm.video_id
  having count(*) >= 2
) x
join segments s on s.id = x.segment_id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1;
```

Se esse número ficar muito menor que "vídeos com métricas disponíveis"
(indicador 4), confirma a limitação já esperada: snapshot só acontece
quando o candidato é redescoberto — vídeo achado 1x no dia 1 e nunca mais
pode nunca ganhar 2ª captura na semana. Não inventar rotina de
recaptura agora — só medir e decidir ao fim da semana.

**6. Keywords e pontos de Trends (a descoberta está gerando sinal útil?):**

```sql
select s.slug, ks.source, count(*) as keyword_signals
from keyword_signals ks
join segments s on s.id = ks.segment_id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1, 2
order by 1, 2;

select s.slug, count(*) as trend_observations, count(distinct ko.keyword) as keywords_distintas
from trend_observations ko
join segments s on s.id = ko.segment_id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1;
```

Não esperar uma série diária completa aqui — o upsert por período lógico
(Fase H Task 6) atualiza a observação daquele período, não acumula 1
linha por consulta. `trend_observations` por keyword tende a ficar baixo
mesmo rodando 7 dias (é esperado, não é bug).

**7. Separar grupo pt-BR do grupo en (rastreável via `subnichos.idioma_en`):**

```sql
select s.slug, ks.source,
       case when ks.keyword in (
         select jsonb_array_elements_text(s.config->'subnichos'->'idioma_en')
       ) then 'en' else 'pt-BR' end as idioma,
       count(*) as n
from keyword_signals ks
join segments s on s.id = ks.segment_id
where s.slug in ('velas-artesanais', 'sabonetes-artesanais', 'resina-artesanal')
group by 1, 2, 3
order by 1, 2, 3;
```

(testada contra o Postgres real nesta sessão — sintaxe válida, 0 linhas
porque os segmentos ainda não foram carregados).

## Dia 7 — encerrar e exportar

```bash
cf research stop --segment velas-artesanais
cf research stop --segment sabonetes-artesanais
cf research stop --segment resina-artesanal
docker compose -f .devcontainer/docker-compose.yml \
  -f .devcontainer/docker-compose.experimento-7dias.yml \
  --profile experimento stop worker-velas worker-sabonetes worker-resina
```

## Pós-2026-10-19 — troca para worker único multi-segmento

> NÃO aplicar antes de 2026-10-19 (fim do experimento). Ver Task 1 (guard de
> arranque) e `docs/HANDOFF.md`.

```bash
# 1. Exportar dados do experimento ANTES de parar os containers
# 2. Parar os 3 workers legados
docker compose -f .devcontainer/docker-compose.yml \
  -f .devcontainer/docker-compose.experimento-7dias.yml \
  --profile experimento stop worker-velas worker-sabonetes worker-resina
# 3. Subir o worker único
docker compose -f .devcontainer/docker-compose.yml \
  -f .devcontainer/docker-compose.multi-segmento.yml \
  --profile worker-multi up -d --build worker
# 4. Conferir: guard da Task 1 não dispara (sem COLLECT_SEGMENT),
#    os 3 crons registram e active_segments() devolve os 3 segmentos.
```

## Fase 4 — avaliar o ranking (depois de acumular dados)

```bash
cf score-sample --segment velas-artesanais --limit 50 --out /tmp/velas.csv
cf score-sample --segment sabonetes-artesanais --limit 50 --out /tmp/sabonetes.csv
cf score-sample --segment resina-artesanal --limit 50 --out /tmp/resina.csv
```

1. Preencher `avaliacao_manual` (1-5) nos 3 CSVs — considerar relevância,
   potencial de interesse, qualidade, utilidade pra gerar conteúdo
   original.
2. `cf score-correlation --csv /tmp/velas.csv` (e os outros 2) — Spearman
   entre o rank do sistema e a avaliação manual.
3. Olhar separadamente os vídeos com `velocity` vazio no CSV — ausência de
   momentum não é desempenho baixo, é histórico insuficiente (indicador 5
   da Fase 3 já deveria ter avisado se isso seria comum).
4. Só decidir sobre `VELOCITY_VIEWS_PER_HOUR_CAP`/pesos depois disso —
   nunca antes (ver memória `content-factory-fase-h-pendencias-nao-bloqueantes`).

## Nota — poluição do banco por testes

O Postgres usado neste experimento (`contentfactory`, mesmo banco do
`DATABASE_URL` default) acumulou ~150 segmentos `teste-*`/`teste-partial-*`
de execuções passadas da suíte `@requires_infra` (não têm `ON CONFLICT`
limpo sempre, alguns ficam). Não afeta a correção do experimento (tudo é
escopado por `segment_id`), mas qualquer query que não filtre por slug vai
trazer lixo. As queries acima já filtram pelos 3 slugs — mantenha esse
hábito nas suas próprias consultas.
