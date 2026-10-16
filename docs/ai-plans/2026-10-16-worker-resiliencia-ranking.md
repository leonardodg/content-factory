# Worker: resiliência a parada, catch-up de cron, ranking por plataforma e YouTube em PT

Data: 2026-10-16. Origem: revisão do worker em 16/10 (incidente das 10:02 BRT).

## Incidente (evidência)

- 16/10 10:02:10 BRT: o VS Code (Dev Containers, `shutdownMonitor`) rodou
  `docker compose --project-name content-factory -f .devcontainer/docker-compose.yml stop -t 0`
  ao fechar a janela. Isso parou `cf-python`, `content-factory-redis-1` e
  `content-factory-postgres-1`. `devcontainer.json` não define `shutdownAction`,
  então vale o default `stopCompose`.
- O worker (`profiles: [worker-multi]`) não foi parado e entrou em loop de
  restart (65 vezes) sem Redis. Com `restart: unless-stopped`, postgres e redis
  ficaram parados, porque foram parados explicitamente.
- O `-t 0` derrubou o Postgres sem shutdown limpo e exigiu recuperação de crash
  ("database system was not properly shut down; automatic recovery").
- Nenhum cron foi perdido desta vez. Mas em 15/10, num reboot às 00:29, o worker
  voltou às 10:12 e perdeu research, collect e score da manhã. O arq calcula o
  próximo cron a partir do startup (`calculate_next(now)`) e **não recupera**
  horários perdidos. O `run_at_startup` rodaria o job em todo restart,
  sem critério.

## Achados da revisão de dados (16/10)

1. Ranking misturado: sem `views`, o Instagram tem só `recency` e `relevance`
   (pesos renormalizados) e fica com score ~50–56. Isso o coloca acima do melhor
   vídeo do YouTube (43–50), que tem métricas reais. Os scores de plataformas
   diferentes não são comparáveis.
2. Topo do YouTube contaminado (es/en, fora do nicho). O research busca com as
   5 frases longas do Suggest entre aspas. Medido em 16/10 com
   `pt`/`BR`/`relevance`: resina com as frases do Suggest deu 1 vídeo em pt de 25;
   com as keywords do segmento, 19 de 25 (o resto em en). Quase todo vídeo
   declara `defaultAudioLanguage`/`defaultLanguage`.
3. `COLLECT_LIMIT=3` ficou do teste E2E. O `search.list` custa 100 unidades,
   qualquer que seja o `maxResults`, então 25 não custa mais cota.
4. `videos.language` nunca é preenchido (o `CandidateVideo` não tem o campo).

## Tarefas (TDD)

### T1 — Dev Container não para a produção
- `devcontainer.json`: `"shutdownAction": "none"`.
- Doc em `docs/manual/operacao.md` com o incidente, como religar e por quê.

### T2 — Catch-up de cron no startup
- `app/catchup.py` (puro): `missed_jobs(now, last_runs, schedule, tz, min_gap)`.
  Para cada job, toma o horário agendado mais recente `<= now` (olhando até 24 h
  para trás). É "perdido" se `last_run` é `None` ou anterior a esse horário.
  Não recupera se o próximo horário do mesmo job estiver a menos de `min_gap`
  (60 min), porque o cron cobre. A ordem é research → collect → score.
- Port `JobRunReader` (`last_research_at`, `last_collect_at`, `last_score_at`),
  que são globais (todos os segmentos rodam juntos):
  - research: `max(collect_runs.started_at)` com `collector LIKE 'research:%'`;
  - collect: com `collector NOT LIKE 'research:%'`;
  - score: `max(video_scores.computed_at)`.
- Worker: `catchup_job` roda os perdidos em sequência, no mesmo processo, e
  devolve um envelope. O `startup` enfileira com `_job_id` por hora, o que deixa
  vários restarts seguidos idempotentes. A env `CATCHUP_ON_STARTUP` (default `1`)
  desliga a função.
- Overlap com o cron: o lock por segmento já devolve `ja_em_execucao`.

### T3 — YouTube em PT
- `CandidateVideo.language`, vindo de `defaultAudioLanguage` ou
  `defaultLanguage` e persistido em `videos.language`.
- `_search`: com `relevance_language` configurado, descarta vídeo cujo idioma
  declarado existe e tem prefixo diferente. Vídeo sem idioma declarado passa
  (não há evidência para descartá-lo). Vale para collect e research.
- research: `keywords_for_social = seeds + sinais`, com as seeds primeiro. Os
  coletores que cortam em 5 termos (YouTube e TikTok) passam a buscar pelas
  keywords do segmento. O Instagram já usa a união e não muda.

### T4 — Ranking por plataforma no B3
- `cf score-sample --platform youtube|instagram|…` (opcional). O CSV ganha a
  coluna `platform`. O repositório filtra por plataforma.
- No B3, cada critério é medido **por plataforma**, e a web fica fora (não tem
  métricas). A fórmula não muda, porque a regra do B3 é não mexer em pesos
  antes da validação.

### T5 — COLLECT_LIMIT=25
- `.devcontainer/env/app.env` não é rastreado. Corrigir a nota em
  `docs/manual/operacao.md`/`anti-ban.md`: a cota é por busca, não por item.

## Deploy
- Merge a qualquer hora. Rebuild do worker só depois do score das 21:30 BRT, com a
  fila vazia.
- Fumaça: reiniciar o worker e ver no log o `catchup_job` com `ran: []`. O
  research de 07:00 do dia seguinte deve trazer YouTube majoritariamente pt.
- Os dados de 15–16/10 do YouTube no research ficam registrados como calibração.
