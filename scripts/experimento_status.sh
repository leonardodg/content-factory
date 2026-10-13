#!/usr/bin/env bash
# Health check do experimento de 7 dias (velas/sabonetes/resina).
# Uso: ./scripts/experimento_status.sh
#
# 6 seções: o que está sendo buscado (config real do banco), exceções
# recentes nos workers, status de campanha, última execução por
# segmento/plataforma, resumo acumulado (candidatos/métricas/momentum)
# e o cron fixo (não há "próxima execução" armazenada — horário é
# sempre o mesmo, BRT).
set -euo pipefail

SEGMENTS=(velas-artesanais sabonetes-artesanais resina-artesanal)
WORKERS=(cf-worker-velas-artesanais cf-worker-sabonetes-artesanais cf-worker-resina-artesanal)
PG_CONTAINER="content-factory-postgres-1"
PG_USER="contentfactory"
SLUG_LIST=$(printf "'%s'," "${SEGMENTS[@]}")
SLUG_LIST=${SLUG_LIST%,}

echo "########## 0. O que está sendo buscado (config real no banco) ##########"
echo "# platforms = onde cada job busca candidatos; keywords = termos usados"
echo "# por research_job (expandidos via Google Suggest/Trends) e collect_job"
echo "# (direto, sem expansão). idioma_en = subconjunto rastreável das keywords"
echo "# em inglês, só metadado (não afeta o pipeline)."
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -c "
select slug,
       config->'platforms' as platforms,
       jsonb_array_length(config->'keywords') as n_keywords,
       config->'subnichos'->'idioma_en' as keywords_en
from segments
where slug in ($SLUG_LIST)
order by slug;
"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -c "
select slug, jsonb_array_elements_text(config->'keywords') as keyword
from segments
where slug in ($SLUG_LIST)
order by 1, 2;
"

echo "########## 1. Exceções/erros recentes nos workers (últimas 2h) ##########"
echo "# vazio = nenhum job quebrou com erro não tratado (saúde do PROCESSO,"
echo "# não dos dados — ver seções 3/4 pra saúde dos dados)."
for c in "${WORKERS[@]}"; do
  echo "--- $c ---"
  docker logs "$c" --since 2h 2>&1 | grep -iE "error|exception|traceback" || echo "(nenhuma)"
  echo
done

echo "########## 2. Status de campanha por segmento ##########"
echo "# active=true + ends_at = a janela de 7 dias ainda está de pé. Só"
echo "# confirma que a campanha existe, não que ela já produziu dado."
for s in "${SEGMENTS[@]}"; do
  cf research status --segment "$s"
done
echo

echo "########## 3. Última execução por segmento/plataforma (collect_runs) ##########"
echo "# 0 linhas = nenhum job rodou ainda nessa combinação segmento+plataforma."
echo "# status ok/partial/failed/blocked; found/inserted/updated/skipped/failed"
echo "# vêm do upsert; error_code só vem preenchido quando status != ok."
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -c "
select s.slug, cr.collector, cr.status,
       cr.started_at, cr.finished_at,
       cr.found, cr.inserted, cr.updated, cr.skipped, cr.failed, cr.error_code
from collect_runs cr
join segments s on s.id = cr.segment_id
join (
  select segment_id, collector, max(started_at) as max_started
  from collect_runs
  group by 1, 2
) latest
  on latest.segment_id = cr.segment_id
  and latest.collector = cr.collector
  and latest.max_started = cr.started_at
where s.slug in ($SLUG_LIST)
order by s.slug, cr.collector;
"

echo "########## 4. Resumo acumulado: candidatos / únicos / cobertura / momentum ##########"
echo "# candidatos_encontrados (soma bruta) vs videos_unicos (sem duplicata);"
echo "# videos_com_metrica = cobertura real; videos_com_momentum_calculavel ="
echo "# só esses têm 2+ capturas de views pra calcular crescimento de verdade."
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -c "
select s.slug,
       sum(cr.found) as candidatos_encontrados,
       count(distinct vs.video_id) as videos_unicos
from collect_runs cr
join segments s on s.id = cr.segment_id
left join video_segments vs on vs.segment_id = s.id
where s.slug in ($SLUG_LIST)
group by 1
order by 1;
"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -c "
select s.slug, count(distinct vm.video_id) as videos_com_metrica
from video_metrics vm
join video_segments vs on vs.video_id = vm.video_id
join segments s on s.id = vs.segment_id
where s.slug in ($SLUG_LIST)
group by 1
order by 1;
"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -c "
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
where s.slug in ($SLUG_LIST)
group by 1
order by 1;
"

echo "########## 5. Cron fixo (não há 'próxima execução' armazenada) ##########"
echo "# horário é sempre o mesmo, BRT — não varia por dia nem fica salvo no banco."
echo "research_job: 07:00 BRT | collect_job: 08:00 e 20:00 BRT | score_job: 08:30 e 21:30 BRT"
