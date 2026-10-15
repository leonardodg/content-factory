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
- **Viés de seleção do Trends (decisão de MVP, registrada):** o Trends é
  consultado só para as **5 keywords de maior peso do Suggest**, não para
  uma amostra independente do nicho. No B3, comparar com uma amostra
  aleatória de 5 keywords do Suggest para ver se a seleção exclui termos de
  alto interesse.
- **Suficiência por fonte (proposta, aguarda aprovação):** além de
  `ok_rate`, o `cf ops-report` mostra `data_rate` (runs ok com `found > 0`).
  Limiares propostos de `data_rate` na janela do experimento: YouTube ≥ 90%,
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
