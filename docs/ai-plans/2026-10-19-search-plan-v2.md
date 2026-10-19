# Plano — `search-plan-v2` + `relevance-v2`: buscas sem desperdício e relevância sem homônimos

Origem: QA do D+2 do teste viral-por-tema (19/10). Números no banco de
produção, de 17/10 07:17 a 19/10 10:39 BRT.

## Problemas (medidos)

1. **Seed em inglês sempre vazia.** 6 das 16 keywords de cada segmento são
   inglesas (`resin art`, `soap making`, `candle wick`...). A busca só aceita
   pt-BR, então essas buscas voltam com 0 vídeos: 11 das 27 buscas `seed`, a
   ~100 unidades de cota cada. A `seed` é a linha de base da regra de decisão
   (n ≥ 30): resina tem 12 vídeos únicos e deve chegar ao corte com ~21
   (insuficiente).
2. **Suggest repete o termo da coleta seguinte.** Sem hashtag (Fase B fora),
   a vaga 4 cai em `suggest` e `_rotate(suggest, run_index, taken)` pega o
   item `run_index + 1` — que é o item da vaga `suggest` da coleta seguinte.
   Metade das buscas `suggest` repete a anterior (ex.: `resina epóxi não
   endurece` às 08h e às 14h de 17/10): ~800 unidades/dia sem vídeo novo.
3. **Homônimos passam pela tag.** `is_relevant` checa o bloqueio só no
   título, e uma tag com a âncora basta. Medido (top por views, velas): canal
   de trading (`forex`, `opções binárias`, `bitcoin`, "leitura de vela"),
   mecânica de moto ("cor da vela", tag `vela de ignicao`) passam — a tag
   `vela de ignicao` tem o bloqueio `ignicao`, mas a tag não é checada.
4. **Vídeo sem idioma declarado sempre passa.** Metade dos relevantes de
   resina são títulos em inglês sem `language` ("25 Resin Products You Can
   Make and Sell"); em velas, títulos em espanhol ("El detalle de corazón en
   una vela artesanal").

## Correção (`search-plan-v2`)

- **Task 1 — seed pelo idioma.** `domain/term_filter.is_other_language(term)`
  (público; mesma regra do `term-filter-v1`). `domain/search_plan.
  searchable_seeds(seeds, wanted)`: com idioma exigido `pt*`, tira as seeds
  com evidência positiva de EN/ES; sem idioma ou outro idioma, não mexe.
  `app/collect._pool_snapshot` aplica nas seeds do pool e no fallback das
  keywords. Sem seed restante, a vaga não busca (já é o comportamento).
- **Task 2 — passo do Suggest.** `plan_queries` conta, antes, quantas vagas
  vão para `suggest` nesta coleta (`n`: vaga `suggest` + vaga `trends`/
  `hashtag` sem lista + vagas extra) e a j-ésima escolha usa o índice
  `run_index * n + j`. Coletas seguidas não se sobrepõem. A vaga que cair em
  `suggest` sem estar prevista (todos os termos do Trends já tomados) segue o
  cursor — caso raro, aceito.
- **Task 3 — `relevance-v2` (`app/population.is_relevant`):**
  (a) bloqueio checado no título **e em cada tag** (palavra inteira); (b)
  vídeo **sem idioma declarado**, com idioma exigido `pt*`: sai se o título
  (sem `#hashtags`) tem evidência positiva de EN/ES (`term_filter`) e nenhuma
  palavra exclusiva do PT (`com`, `fazer`, `voce`, `nao`, `da`, `dos`,
  `das`, `na`, `nas`, `em`, `um`, `uma`, `pra`, `seu`, `sua`, `e`). A função
  pura é `domain/language.title_conflicts(title)`. `RELEVANCE_VERSION =
  "relevance-v2"` entra no `rules_version` do `term_report`.
- **Task 4 — bloqueios dos YAMLs:** velas += `forex`, `bitcoin`, `btc`,
  `trader`, `traders`, `trade`, `trading`, `candlestick`, `opcoes binarias`,
  `price action`, `deriv`, `moto`, `carburador`, `cabecote`, `combustivel`;
  resina += `impressora`, `cutelaria`. Deploy: `cf segment load` (o config é
  lido do banco).
- **Versão:** `SEARCH_PLAN_VERSION = "search-plan-v2"` (vai no `query_id`; o
  relatório separa as buscas antes/depois pelo prefixo).

## Testes (TDD — RED antes)

- `tests/domain/test_search_plan.py`:
  - `test_suggest_nao_repete_o_termo_da_coleta_seguinte`: pool sem trends e
    sem hashtag, 4 vagas; os termos `suggest` das coletas `i` e `i+1` são
    disjuntos e, juntos, são 4 termos distintos.
  - `test_passo_do_suggest_com_trends_e_hashtag`: com as 4 listas, 1 vaga
    `suggest` → rodízio de 1 em 1 (comportamento v1 mantido).
  - `test_seed_so_no_idioma_do_segmento`: `searchable_seeds` com `pt-BR` tira
    `resin art`/`soap making` e mantém `resina epóxi`; com `None`, mantém tudo.
- `tests/domain/test_term_filter.py`: `is_other_language` casa com o
  `filter_term` (`lang`).
- `tests/app/test_collect.py`: com `accepted_language: pt-BR`, a vaga `seed`
  nunca busca keyword inglesa; prefixo `search-plan-v2:`.
- `tests/app/test_population.py`:
  - `test_bloqueio_na_tag_fica_fora`: título com âncora, tag `vela de
    ignicao` → fora.
  - `test_sem_idioma_titulo_em_ingles_ou_espanhol_fica_fora`: `language=None`
    + "25 Resin Products You Can Make and Sell" / "El detalle de corazón en
    una vela artesanal" → fora.
  - `test_sem_idioma_titulo_pt_com_hashtag_inglesa_fica`: "Sabonete artesanal
    de Bergamota #handmade #soap" e "RESINA EPÓXI QUE SURPREENDE! Hacks e DIY"
    → dentro.
- `tests/domain/test_language.py`: `title_conflicts` (casos acima).
- Os testes v1 existentes **não mudam** de asserção, exceto o prefixo da
  versão (`search-plan-v1:` → `search-plan-v2:`) e o `rules_version` do
  `term-report` (`+relevance-v2+`).

## Efeito medido (população real de 19/10, publicados nos últimos 30 dias)

| Segmento | Relevantes antes | Depois |
|---|---|---|
| velas | 62 de 94 | 38 |
| sabonetes | 88 de 115 | 86 |
| resina | 39 de 80 | 20 |

Saíram: canais de trading (`forex`, `bitcoin`, "leitura de vela"), mecânica de
moto, títulos em EN/ES sem idioma declarado, cutelaria e impressora de resina.
Dois vídeos em PT saíram pela tag, e os dois ficam fora do nicho: "RESINA EPÓXI
QUE SURPREENDE!" (canal de hacks genéricos, tag `caneta 3d`) e "7 Sabonetes
Que o Brasil Aceita..." (sabonete industrial, tags `protex`/`dove`). Nenhum
vídeo entrou.

## Deploy

A relevância (Tasks 3–4) é aplicada **no corte**: o `cf term-report` recalcula
a população com o código e o config do momento em que roda. Basta a imagem
nova + `cf segment load` antes do relatório (adendo datado antes do corte).
Para o score ao vivo do worker, idem com rebuild.

Muda os braços do teste em andamento: **só com decisão do usuário** e adendo
datado no pré-registro (versão nova, data/hora da troca, motivo). Sem decisão,
o PR fica na `main` e entra no deploy depois do corte (21/10 07:17 BRT).

## Fora deste plano (registrado, não corrigido)

- 4 pares de medições a 0,2 ms (vídeo em 2 segmentos, coletas concorrentes).
  Não afeta o `viral-v2` (par ≥ 1 h no SQL da população); afeta só a
  velocidade do `fase-h` desses vídeos. Correção exige trava/índice por vídeo.
- `resina epóxi preço portugal` passou no `term-filter-v1` (pt-PT). Mudar o
  filtro é `term-filter-v2`; fica para depois do corte.
