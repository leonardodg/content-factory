# Experimento reativado (worker único, 2026-10-14 → 2026-10-21)

Relatório diário do teste reativado, conforme o §Cadência do
[plano de revisão sênior](../ai-plans/2026-10-14-content-factory-revisao-senior.md):
**1 linha por dia**, levantada depois do collect das 20:00 BRT, só com
leitura (`collect_runs`, chaves `cf:cb:*` do Redis, log do worker).

- **Janela:** 3 segmentos (`velas-artesanais`, `sabonetes-artesanais`,
  `resina-artesanal`) até **2026-10-21 19:20 UTC**.
- **Linha de base (D0):** ✅ ok, mas parcial: sem TikTok (`auth_expired`).
- **Sinal real hoje:** YouTube, web e Suggest. O Instagram sai `ok` com a
  mídia da própria conta (gate #10, App Review pendente) e fica fora do
  limiar "runs ok ≥ 90%"; Trends e TikTok falham em 100% das runs.

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
