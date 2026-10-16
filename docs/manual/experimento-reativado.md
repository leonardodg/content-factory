# Experimento reativado (worker único, 2026-10-14 → 2026-10-21)

Relatório diário do teste reativado, conforme o §Cadência do
[plano de revisão sênior](../ai-plans/2026-10-14-content-factory-revisao-senior.md):
**1 linha por dia**, levantada depois do collect das 20:00 BRT, só com
leitura. A partir de D+1 a fonte é `cf ops-report --segment <slug>
--hours 24` (um por segmento), mais `ERROR` no log do worker.

- **Janela:** 3 segmentos (`velas-artesanais`, `sabonetes-artesanais`,
  `resina-artesanal`) até **2026-10-21 19:20 UTC**.
- **Linha de base (D0):** ✅ ok, mas parcial: sem TikTok (`auth_expired`).
- **Fontes com sinal real:** YouTube, web e Suggest desde D0. Trends e
  TikTok falham em 100% das runs.
- **Troca de fonte do Instagram — 2026-10-14 22:50 BRT (01:50 UTC de
  15/10):** até aí, mídia da própria conta (gate #10), fora do limiar
  "runs ok ≥ 90%". Desde então, **Reels reais da hashtag** do segmento
  (`recent_media`, app Meta próprio; conta de consulta provisória:
  conta de terceiro, a trocar por conta do operador). Entre 22:47 e 22:50 BRT
  as runs saíram `auth_expired` (`IG_USER_ID` inválido). **No B3, o
  Instagram entra só com dados a partir da troca**, reportado à parte por
  ter janela menor.

## Regras do B3 (registradas antes de olhar os resultados)

- **TikTok fora do B3** enquanto `TIKTOK_MS_TOKEN` não for rotacionado:
  `auth_expired` é falha de credencial, não evidência sobre o nicho
  (auditoria 3, item 4). Se o token voltar, o TikTok entra só a partir da
  data da volta, reportado à parte.
- **Trends:** válido a partir do research de **15/10 07:00 BRT** (PR #37);
  antes disso, 100% `upstream_changed` por defeito de código. A semana
  corrente (`isPartial`) não entra na série (auditoria 3, N-2).
- **YouTube: busca corrigida a partir de 15/10 ~00:10 BRT.** Frases unidas
  por `OR` sem aspas faziam a busca do YouTube voltar 0 ou poucos vídeos
  (`research:youtube` de velas e resina com `found = 0`; `collect` com 3).
  Com aspas, a mesma busca traz até o limite. No B3, o volume do YouTube
  antes e depois dessa data é reportado separadamente.
- **YouTube restrito ao Brasil a partir de 15/10 (antes do collect das
  08:00 BRT):** `search` por segmento (`pt`/`BR`, `order=relevance`). Nos
  dados de 13–14/10 o topo de resina e velas tinha vídeos fora do nicho e em
  inglês/espanhol. Esses dias ficam como **período de calibração**, fora do
  B3.
- **Viés de seleção do Trends (decisão de MVP, registrada):** o Trends é
  consultado só para as **5 keywords de maior peso do Suggest**, não para
  uma amostra independente do nicho. No B3, comparar com uma amostra
  aleatória de 5 keywords do Suggest para ver se a seleção exclui termos de
  alto interesse.
- **Manhã de 15/10 perdida:** a máquina desligou às 00:29 BRT e o worker
  voltou às 10:12. Research, collect e score da manhã não rodaram, e o
  research foi disparado à mão às 17:48. Desde 2026-10-16 o worker recupera
  horário perdido no startup (`catchup_job`).
- **Queda de 16/10 10:02–11:06 BRT:** fechar o VS Code parou o Postgres e o
  Redis de produção (`compose stop -t 0` do Dev Container). Nenhum cron foi
  perdido, porque os da manhã já tinham rodado. Corrigido com
  `shutdownAction: none`.
- **YouTube do research em en/es até 16/10:** o research buscava com as 5
  frases longas do Suggest como frase exata. Mesmo com `pt`/`BR`/`relevance`,
  a API completava os 25 resultados com vídeos em en/es (resina: 1/25 em
  pt). Os vídeos do research de 15 e 16/10 ficam como **calibração**. A
  partir do deploy da noite de 16/10, o research busca pelas keywords do
  segmento e o coletor descarta vídeo declarado em outro idioma.
- **Ranking do B3 por plataforma:** sem `views`, o Instagram fica só com
  recency e relevância (pesos renormalizados), pontua ~50–56 e fica acima
  do melhor YouTube (43–50). Os scores de plataformas diferentes não são
  comparáveis, então o B3 mede cada critério **por plataforma**
  (`cf score-sample --platform`), com a web fora (sem métricas).
- **`COLLECT_LIMIT` = 3 até 16/10:** valor do teste E2E esquecido no
  `app.env`. Com ele, o collect trazia 3 vídeos por fonte. Passa a 25 no
  deploy da noite de 16/10, sem custo extra de cota do YouTube.
- **Suficiência por fonte (aprovada pelo usuário em 2026-10-15):** além de
  `ok_rate`, o `cf ops-report` mostra `data_rate` (runs ok com `found > 0`).
  Limiares de `data_rate` na janela do experimento: YouTube ≥ 90%,
  Instagram (desde a troca) ≥ 80%, Suggest ≥ 90%, Trends ≥ 80%, web ≥ 50%
  (ambíguo pelo `ddgs`). Abaixo disso, a fonte é reportada como
  **insuficiente**, sem virar falha.

## Linhas diárias

Legenda: contagens `ok/failed/blocked` por coletor nas 12 runs do collect
das 20:00 BRT (3 segmentos × 4 coletores); research = runs `research:*` do
dia; breakers = chaves `cf:cb:*:blocked` abertas; **`web_empty`** = runs
`web` `ok` com `found = 0`, a partir de D+1. Esse número é **ambíguo**: o
`ddgs` não distingue busca sem resultado de engine bloqueada (403/429),
porque devolve vazio sem exceção nos dois casos (auditoria externa 4.3).

| Dia | Collect 20:00 BRT (ok/failed/blocked) | Research do dia | Breakers | `ERROR` no log | Notas |
| --- | --- | --- | --- | --- | --- |
| D+0 (14/10) | youtube 3/0/0 · web 3/0/0 · instagram 3/0/0 · tiktok 0/3/0 (`auth_expired`) | tick manual 18:22 BRT: youtube, instagram e web_suggest ok (518 sinais); trends `upstream_changed` (HTTP 400), tiktok `auth_expired` | nenhum | 0 | YouTube `quota_used=102` por run; web `found=0` em `resina-artesanal` (A-2(b)); `found` 0–3 por run |
| D+1 (15/10) | youtube 3/0/0 · web 3/0/0 · instagram 3/0/0 · tiktok 0/3/0 (`auth_expired`, token **vazio**) | 07:00 **perdido** (máquina desligada 00:29–10:12); tick manual 17:48 BRT: **trends ok, 260 pontos por segmento** (primeira vez); youtube 25/segmento (majoritariamente en/es, calibração); web_suggest ~145; instagram 6/5/0 (mídia própria); tiktok `auth_expired` | nenhum | 0 | `web_empty` 0/3; collect com `COLLECT_LIMIT=3`; score 21:30 ok |
