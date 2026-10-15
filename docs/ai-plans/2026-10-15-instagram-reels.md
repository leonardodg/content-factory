# Coletor do Instagram: Reels de verdade pela busca por hashtag (2026-10-15)

## Contexto

O app Meta próprio (`content-factory`, publicado em 2026-10-15) libera
`ig_hashtag_search` para o operador **sem App Review**. Sondagem na API
real (token do app novo, `#velaartesanal`, v21.0) antes de ligar o token no
worker revelou 4 defeitos que hoje ficam escondidos pelo gate #10:

| # | Defeito | Evidência real |
| --- | --- | --- |
| 1 | `FIELDS` pede `username`/`thumbnail_url`, que **não existem** para mídia de hashtag | `recent_media`: `(#100) Please read documentation for supported fields` — o collect inteiro falharia |
| 2 | Keyword com espaço vai direto como hashtag | `q=vela artesanal` → `(#24) The requested resource does not exist`; `velaartesanal` ok. As 3 keywords principais têm espaço |
| 3 | `top_media` não traz Reels | `top_media`: 0 Reels em 50 (31 carrosséis, 19 imagens); `recent_media`: 13 Reels em 25 |
| 4 | Só `IMAGE` é descartado | carrossel seria gravado como vídeo — inclusive no fallback de mídia própria |

Bônus observado: `limit=50` com `caption` é recusado (`#1 reduce the
amount of data`); `limit=25` funciona (o coletor já usa `min(limit, 25)`).

## Tasks (TDD, 1 commit por task)

- **T1** — `HASHTAG_FIELDS` sem `username`/`thumbnail_url`, com
  `media_product_type`; descoberta usa `recent_media` (Reels das últimas
  24 h) no lugar de `top_media`.
- **T2** — consulta normalizada: `casefold`, sem `#`, sem espaços
  (`vela artesanal` → `velaartesanal`). Acentos ficam (hashtag aceita).
- **T3** — só `media_type == VIDEO` passa (Reels e vídeos de feed);
  carrossel e imagem saem, na hashtag e no fallback de mídia própria.

## Fora de escopo (registrado)

- `research` do Instagram continua em mídia própria: termos dinâmicos
  consumiriam o limite de **30 hashtags únicas por 7 dias**; precisa de
  controle de cota antes.
- 400 sem gate (`#100`, `#24`) vira `auth_expired`; reclassificar fica para
  o C3 (contrato de erros).
- `creator_handle` fica vazio para mídia de hashtag (a API não expõe o autor).

## Ativação

Depois do merge: trocar `INSTAGRAM_ACCESS_TOKEN` pelo token do app novo nos
dois arquivos (ação do usuário ou com o ok dele), rebuild do worker entre o
score das 21:30 e o research das 07:00 BRT, tick manual de fumaça.
