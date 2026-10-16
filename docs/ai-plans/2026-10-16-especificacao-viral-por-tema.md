# Especificação: descoberta de conteúdo viral por tema (teste de 4 dias)

Data: 2026-10-16. Autor: orquestrador/QA. Público: agente de implementação
(transforma esta especificação em plano de tarefas TDD) e revisor.

Esta é a **especificação de requisitos**. O plano de implementação, com tarefas
e testes, deriva daqui e fica em arquivo próprio em `docs/ai-plans/`. Nada aqui
altera o experimento web (F0–F5, protocolo `q1`), que segue em paralelo.

## 1. Objetivo

Encontrar **conteúdo viral de qualidade** de um segmento: o conteúdo que atinge
mais pessoas e gera mais engajamento.

O fluxo desejado:

1. O segmento define as keywords.
2. O Google Suggest expande essas keywords em pesquisas reais relacionadas.
3. O Google Trends mede o interesse por essas pesquisas.
4. As redes sociais são buscadas com os termos mais promissores.
5. O engajamento (views, likes, comentários, compartilhamentos) é medido ao
   longo do tempo.
6. O sistema responde **quais temas** e **quais vídeos** geram mais engajamento.

**Pergunta do teste de 4 dias:** os termos vindos da web (Suggest, Trends e
hashtags) trazem conteúdo com mais engajamento do que as keywords fixas do
segmento? Quais temas lideram em cada segmento?

## 2. Estado atual (fatos medidos em 2026-10-16)

| Etapa | Situação | Evidência |
|---|---|---|
| Keywords do segmento | ok | `segments/<slug>.yaml` |
| Suggest | ~150 frases por dia por segmento (~220 com as sondas `probe-v2`), **todas com peso 0,5** | `keyword_signals`: `web_suggest` min = max = 0,5 (520 linhas) |
| Trends | só **5 termos por dia**: as 5 primeiras frases, não as melhores. O resultado não influencia a busca nem o score | `TRENDS_MAX_KEYWORDS = 5`; ordenação por peso empatado |
| Busca nas redes | a coleta usa **só as keywords do segmento**. O research usa as frases, mas depois do PR #44 as keywords vêm primeiro e o YouTube/TikTok cortam em 5 termos | `youtube.discover` usa `segment.keywords[:5]`; `[*keywords, *hashtags][:5]` |
| Instagram | **não descobre conteúdo de terceiros**: lê só a mídia da própria conta (gate #10, App Review pendente) | `instagram._own_media_by_terms` |
| TikTok | quebrado desde 14/10 (`msToken` vazio) | HANDOFF |
| Métricas no tempo | só **103 de 427** vídeos têm 2 ou mais medições: um vídeo só é remedido quando a busca o encontra de novo | `video_metrics` |
| Criadores | tabela `creators` **vazia**; `videos.creator_id` NULL em 514/514 vídeos do YouTube. O número de inscritos chega só em `video_metrics.followers_at_capture` | consulta ao banco |
| Atribuição | o banco **não guarda o termo que trouxe cada vídeo** (exceto na web, `web_search_hits`) | schema |
| Score | `fase-h`: performance 0,25 (views/1M, absoluto), velocity 0,20, engagement 0,15, relevance 0,20, recency 0,20. **40% não é engajamento**; o valor absoluto favorece canais grandes | `domain/scoring.py` |
| Termos da web | o teste real com sorteio cego trouxe de 4 a 7 de 13 consultas fora do nicho ou em outro idioma (vela de bolsa em es, marcas industriais, en, resina odontológica) | QA, 16/10 |

## 3. Fluxo alvo

```
07:00  RESEARCH (1x/dia)
  keywords do segmento ──► Suggest (+ sondas probe-v2) ──► FILTRO DE TERMOS ──► kw-rank
                                                                │
                                                     top-N ──► Trends (lotes com âncora)
                                                                │
                                                     POOL DE TERMOS do segmento
                                                     (origem: seed | suggest | trends | hashtag:<plataforma>)

02/08/14/20  COLLECT (a cada 6 h)
  plano de consulta por plataforma (cota fixa por origem, rodízio determinístico)
      ──► busca ──► vídeos + ATRIBUIÇÃO (termo, origem, posição)
  REMEDIÇÃO dos vídeos já conhecidos (curva de métricas a cada 6 h)

02:30/08:30/14:30/20:30  SCORE
  fase-h (linha de base) + viral-v2 (experimental)
  RANKING DE TEMAS (engajamento por termo e por origem)
  MINERAÇÃO DE HASHTAGS dos vídeos de maior engajamento ──► POOL DE TERMOS
```

## 4. Requisitos

Cada requisito tem critério de aceite verificável. "QA" = verificado pelo
orquestrador com dados reais antes do deploy.

### R1. Filtro de termos (`term-filter-v1`)

Um único filtro, puro (em `domain/`), usado pelo pool de termos, pelas hashtags
e pelo protocolo `q1` da web.

- **Idioma:** só português. Descartar frases com marcadores de outro idioma
  (lista fechada e versionada, por exemplo `for`, `the`, `and`, `with`, `en`,
  `con`, `los`, `las`, `del`, `para la`) e frases sem nenhuma palavra em
  português. Sem heurística opaca: lista fechada e testes com casos reais.
- **Âncora no nicho:** a frase precisa conter, como palavra inteira, pelo menos
  um termo-âncora do segmento (novo campo `SegmentConfig.anchor_terms`, ex.
  velas: `vela`, `velas`, `pavio`, `parafina`, `cera`). Normalização sem acento.
- **Bloqueio de fora do nicho:** lista curta e versionada por segmento
  (`SegmentConfig.blocked_terms`, ex. velas: `tendencia alcista`, `bajista`,
  `grafico`, `ignicao`, `motor`; sabonetes: marcas industriais `natura`,
  `phebo`, `dove`, `lux`, `protex`; resina: `odontologica`, `dente`,
  `clareia`).
- Todo descarte é registrado com motivo (`lang`, `no_anchor`, `blocked`).

**Aceite (QA):** no teste real com o Suggest (sorteio `q1` e top-20 do pool),
no máximo **1 em 13** termos fora do nicho ou de outro idioma por segmento,
e nenhum segmento com classe de intenção vazia por causa do filtro (se
acontecer, reportar, não afrouxar o filtro em silêncio).

### R2. Ranking dos termos do Suggest (`kw-rank-v1`)

O peso fixo de 0,5 deixa de existir. Peso em [0, 1], puro e determinístico:

- **posição** na lista do Suggest (`1/posição`, média entre as sementes que a trouxeram);
- **recorrência**: em quantas sementes distintas a frase apareceu;
- **persistência**: em quantos dias distintos (dos últimos 7) ela apareceu.

Pesos da combinação como constante versionada. O `KeywordSignal.weight` passa
a carregar esse valor. O Suggest precisa expor a posição de cada sugestão
(hoje descartada em `_fetch_all`).

**Aceite:** testes com casos literais; no banco, `web_suggest` deixa de ter
peso único (QA confere a distribuição após o primeiro research).

### R3. Trends aplicado

- Consultar os **top-N termos do pool** (por `kw-rank`, depois do filtro), não
  as 5 primeiras frases. N configurável (`TRENDS_TERMS_PER_DAY`, padrão 16).
- O Trends aceita 5 termos por consulta e normaliza dentro da consulta. Para
  comparar lotes, cada lote leva **1 termo-âncora fixo** (a primeira keyword do
  segmento) + 4 termos. O interesse de cada termo é expresso relativo à âncora.
- `trend_score` por termo: média do interesse relativo nos últimos 7 dias +
  inclinação (subindo/descendo). Guardado por termo e dia.
- Respeitar o breaker e o limitador; pausa entre lotes. Falha do Trends não
  derruba o research (comportamento atual).

**Aceite:** ao menos 12 termos por segmento por dia com `trend_score`; teste
de normalização pela âncora com casos literais.

### R4. Pool de termos do segmento

Tabela nova (migração aditiva), por exemplo `segment_terms`:
`segment_id`, `term`, `origin` (`seed` | `suggest` | `trends` |
`hashtag:youtube` | `hashtag:instagram` | `hashtag:tiktok`), `kw_rank`,
`trend_score`, `engagement_score` (R10), `status` (`active` | `excluded`),
`exclude_reason`, `first_seen_at`, `last_seen_at`, `rules_version`.

- As keywords do segmento entram como `seed` e nunca são excluídas.
- O pool é a fonte única dos termos usados nas buscas (R5).

**Aceite:** após o research, o pool tem seeds + termos filtrados com
origem e scores; nenhum termo excluído sem motivo.

### R5. Plano de consulta por coleta

- Por plataforma, uma **cota fixa de buscas por coleta**, dividida por
  origem. Para o teste de 4 dias a divisão é **fixa e pré-registrada** (sem
  realocação adaptativa durante o teste, para não confundir a comparação entre
  origens). Proposta para o YouTube, 3 buscas por coleta por segmento:
  1 `seed` (rodízio das keywords), 1 `suggest`/`trends` (melhores do pool, em
  rodízio), 1 `hashtag:youtube` (melhores hashtags; enquanto o pool de hashtags
  estiver vazio, a vaga vai para `suggest`).
- Rodízio determinístico e sem estado (mesmo padrão de `run_index_for`, ajustado
  para 4 coletas por dia).
- `COLLECT_LIMIT=25`. Idioma e região do YouTube como hoje (`pt`/`BR`), com o
  filtro `_language_matches`.
- Orçamento configurável por env (`SEARCHES_PER_COLLECT_<PLATAFORMA>`).
- A web segue o seu próprio protocolo (`q1`/legado); não entra neste plano.

**Aceite:** teste do plano por coleta (determinismo, divisão por origem,
fallback quando uma origem está vazia); QA confere nos `collect_runs` a cota
usada por coleta.

### R6. Atribuição: qual termo trouxe cada vídeo

Tabela nova `video_discoveries`: `video_id`, `segment_id`, `platform`, `term`,
`term_origin`, `query_id`, `result_position`, `run_kind` (`collect` |
`research`), `discovered_at`. Uma linha por (vídeo, termo, coleta).

- Toda busca de toda plataforma (coleta e research) grava a atribuição.
- Vídeo achado por vários termos tem várias linhas: a análise usa todas.

**Aceite:** 100% dos vídeos encontrados nas coletas novas têm ao menos 1 linha
em `video_discoveries` (QA confere no primeiro dia).

### R7. Remedição dos vídeos conhecidos

Sem isso não existe curva de engajamento.

- A cada coleta, remedir os vídeos do segmento **descobertos nos últimos 7
  dias** (teto configurável, padrão 500 por segmento):
  - YouTube: `videos.list` em lotes de 50 ids (1 unidade por lote);
  - Instagram: só mídia própria (sem valor para descoberta, manter como está);
  - TikTok: fora enquanto o token não existir.
- Cada remedição grava um `video_metrics` com `captured_at`.
- Vídeo removido ou privado: marcar e parar de remedir.

**Aceite:** a cada janela de 6 h, ao menos 90% dos vídeos do YouTube
acompanhados têm medição nova; depois de 12 h, a `velocity` deixa de ser nula
para pelo menos 90% deles (QA confere).

### R8. Criadores

- Preencher `creators` (YouTube: `channelId`, título, `subscriberCount`) e
  ligar `videos.creator_id`. Corrigir o motivo de a tabela estar vazia.
- Guardar `subscriberCount` também em `creators.followers_count` (histórico
  continua em `video_metrics.followers_at_capture`).

**Aceite:** vídeos do YouTube novos com `creator_id` preenchido; teste de
integração.

### R9. Score de viralização `viral-v2` (experimental, em paralelo)

O `fase-h` continua como **linha de base**, intocado (mesma lógica do
`base-fase-h` da web). O `viral-v2` é calculado ao lado, com versão e explicação
por componente, em tabela própria ou coluna versionada.

Componentes, todos normalizados **por plataforma e por segmento** (percentil na
janela do teste), para que plataformas diferentes não se misturem:

- **velocidade**: views por hora entre medições (R7), em escala log;
- **taxa de engajamento**: (likes + comentários + compartilhamentos + salvos) / views;
- **desempenho relativo ao canal**: views / inscritos do canal (proxy de
  "viralizou além da base"), em escala log;
- **alcance**: views totais em escala log (peso menor).

Relevância e recência deixam de ser peso:
- **relevância vira filtro**: vídeo fora do nicho (R1 aplicado ao título e à
  descrição) não entra no ranking;
- **recência vira janela**: só vídeos publicados nos últimos 30 dias (configurável).

Pesos iniciais como hipótese versionada (exemplo: velocidade 0,35, engajamento
0,25, relativo ao canal 0,25, alcance 0,15), registrados no pré-registro antes
de qualquer dado do teste novo.

**Aceite:** testes com casos literais; `viral-v2` nunca grava em
`video_scores.total` do `fase-h`; explicação por componente.

### R10. Ranking de temas

A resposta direta à pergunta do teste. Por segmento, plataforma e termo:

- `n` vídeos atribuídos (R6);
- mediana de velocidade, de taxa de engajamento e de desempenho relativo;
- fração dos vídeos do termo no top 10% do `viral-v2`;
- `engagement_score` do termo (combinação versionada) gravado no pool (R4);
- IC 95% por bootstrap (seed fixa, 1.000 reamostragens) para as medianas;
- termo com `n < 5` sai como "insuficiente", nunca como vencedor.

Também por **origem** (`seed` x `suggest` x `trends` x `hashtag`): é a comparação
principal do teste.

- CLI `cf term-report --segment <slug> [--platform]`: grava no banco (tabela de
  resultado do relatório, versão e data de corte) e devolve só o envelope com
  contagens (regra "dados no banco"). Exportação para CSV opcional, para fora do
  repositório (o repositório é público e os títulos são de terceiros).

**Aceite:** testes com dados sintéticos e caso literal; QA roda no D+1 com
dados reais.

### R11. Mineração de hashtags por plataforma

- Fonte: vídeos do segmento no **top 25% do `viral-v2`** (não todos).
  - YouTube: `snippet.tags` + `#hashtags` do título e da descrição (já no `raw`
    de parte dos vídeos; garantir `tags` no `videos.list`);
  - Instagram: hashtags da legenda (mídia própria: só como referência, não vira
    busca enquanto não houver busca por hashtag);
  - TikTok: `textExtra`/`challenges`, quando o token existir.
- Passa pelo filtro R1 (idioma, âncora, bloqueio).
- Ranking por frequência ponderada pelo `viral-v2` dos vídeos de origem.
- Entra no pool como `hashtag:<plataforma>`; no YouTube é buscada como `#tag`.

**Aceite:** após o primeiro score com `viral-v2`, o pool do YouTube tem
hashtags filtradas com score; teste de extração com `raw` real anonimizado.

### R12. Agendamento a cada 6 h

- Collect: `COLLECT_CRON_HOURS=02,08,14,20` (BRT).
- Score: hoje fixo em `{8, 21}` no código; passa a ser configurável
  (`SCORE_CRON_HOURS`, padrão igual ao collect, minuto 30).
- Research: 1x por dia às 07:00 (mantido, antes da coleta das 08:00).
- Remedição (R7) dentro da coleta ou como job logo após; a duração total de
  uma coleta precisa ficar bem abaixo de 6 h (QA mede).
- O catch-up de startup e o teste "catch-up e cron leem os mesmos horários"
  passam a ler as envs.

**Aceite:** testes do agendamento a partir das envs; QA confere 4 coletas e 4
scores no primeiro dia.

### R13. Observabilidade

`cf ops-report` passa a mostrar, por segmento:
- cota usada por plataforma e por coleta;
- vídeos descobertos por origem de termo;
- cobertura da remedição (R7);
- tamanho do pool por origem e descartes do filtro por motivo.

### R14. Idioma

`videos.language` preenchido onde a plataforma informa; filtro de português
do YouTube (`_language_matches`) mantido; o filtro R1 aplica-se também aos
títulos no ranking (`viral-v2`).

## 5. Orçamento de cota (YouTube, limite 10.000 unidades por dia)

| Item | Cálculo | Unidades/dia |
|---|---|---|
| Buscas da coleta | 3 segmentos x 4 coletas x 3 buscas x 100 | 3.600 |
| `videos.list` + `channels.list` das buscas | 3 x 4 x 3 x 2 | 72 |
| Remedição | 3 x 4 x 10 lotes (500 vídeos) | 120 |
| Research (1x/dia) | ~3 x 100 + listas | ~330 |
| **Total** | | **~4.100** |

Margem confortável. Teto por env para não ultrapassar 8.000 por dia.

## 6. Protocolo do teste de 4 dias

**Pré-registro** (commit em `docs/manual/` antes de abrir as campanhas):
- pergunta (seção 1), versões (`term-filter-v1`, `kw-rank-v1`, `viral-v2`,
  pesos), divisão fixa de buscas por origem (R5), datas de corte;
- regra de decisão por origem: a origem X é **melhor** que `seed` em um
  segmento se a mediana do `viral-v2` dos vídeos trazidos por X for maior, com
  o limite inferior do IC 95% da diferença acima de zero; com IC cruzando zero,
  **direcional**; com menos de 30 vídeos atribuídos, **insuficiente**;
- critério de "top temas": `engagement_score` com `n >= 5`.

**Execução:**
- Encerrar as campanhas 856–858 (o teste antigo foi congelado em 16/10 15:40
  BRT; backup em `~/content-factory-backups/2026-10-16-teste7d-codigo-antigo/`).
- Deploy com a fila vazia, migrações, `cf segment load` (sobe `probe_terms`,
  `anchor_terms`, `blocked_terms`), envs da seção R12 e `COLLECT_LIMIT=25`.
- Abrir campanhas novas de **4 dias** para os 3 segmentos.

**Checkpoints (QA):**
- **D+0, fumaça:** catch-up, primeira coleta com atribuição, cota.
- **D+1:** filtro (R1) com amostra manual de 20 termos por segmento; remedição
  (R7); primeiro `cf term-report`.
- **D+2:** `velocity` não nula, hashtags no pool, relatório parcial.
- **D+4:** encerramento, backup completo, relatório final por segmento: top
  temas, top vídeos por plataforma e veredito por origem.

## 7. Ordem de entrega sugerida

1. R1 + R2 + R4 (filtro, ranking, pool), que também destravam o `q1` da web.
2. R6 + R8 (atribuição e criadores).
3. R7 + R12 (remedição e agendamento a cada 6 h).
4. R5 (plano de consulta) + R3 (Trends aplicado).
5. R9 + R10 + R11 (score `viral-v2`, ranking de temas, hashtags).
6. R13 + R14 + pré-registro.

Cada bloco: plano, TDD, gates do CLAUDE.md, revisão e teste real do QA.
Deploy único no fim, numa janela sem cron e com a fila vazia.

## 8. Fora de escopo deste teste

- **TikTok:** depende do `msToken` (decisão do usuário).
- **Descoberta no Instagram:** a busca por hashtag depende do App Review (limite
  de 30 hashtags distintas a cada 7 dias por conta quando liberada).
- **Score web F3–F5 e ativação do `web-v1`:** seguem o próprio pré-registro.
- **Realocação adaptativa** de buscas por desempenho do termo: só depois do
  teste, com os dados dele.

## 9. Riscos

- **Deriva do nicho:** termos e hashtags puxam conteúdo fora do segmento.
  Mitigação: R1 em todas as entradas e checagem manual no D+1.
- **Rate limit do Suggest/Trends:** mais consultas por dia. Mitigação: pausa
  entre requisições, breaker existente, falha de fonte não derruba o ciclo.
- **4 dias é pouco para crescimento longo:** o teste mede velocidade inicial
  e engajamento. A leitura de longo prazo fica como direcional.
- **Normalização por percentil com poucos vídeos:** exigir `n` mínimo por
  plataforma e segmento antes de comparar.
