# Relevância web e potencial de conteúdo — plano de implementação

Data: 2026-10-16 (revisado no mesmo dia com as decisões do usuário).
Origem: especificação técnica "relevância web e potencial de conteúdo",
confrontada com o código e os dados reais antes de propor mudanças.

## Três objetivos, três perguntas, nunca um score só

| Objetivo | Pergunta | Onde é tratado |
| --- | --- | --- |
| **1. Qualidade da coleta** | Os resultados são páginas legítimas, sem anúncios, duplicatas ou ruído evitável? | F0, F1 (filtro e proveniência) |
| **2. Relevância temática** | A página corresponde ao segmento e à intenção da consulta? | F3 (score `web-v1`), F4 |
| **3. Potencial criativo** | O conteúdo pode gerar uma ideia original e útil? | F2 (rótulo humano), F6 (conteúdo) |

Cada um tem sua métrica. Nenhum é misturado ao `viral_score` dos vídeos.

## Separação entre o experimento web e o de vídeos

- O experimento de vídeos (coleta até 2026-10-21 e B3 depois) **não muda**:
  `viral_score`, `FORMULA_VERSION`, `video_scores` e critérios do B3 ficam
  como estão.
- O experimento web tem **dados, versão e relatório próprios**: as tabelas
  `web_search_hits` e `web_page_scores`, os protocolos `web-queries-q1` e
  `web-v1`, e o relatório `docs/manual/validacao-web.md`.
- A web já está fora do B3 (decisão de 16/10). Diversificar as consultas
  web muda o conjunto de páginas, não os vídeos. Por isso a comparação de
  rankings é sempre **pareada**, sobre os mesmos candidatos (F4).
- O score web novo fica **só na CLI** até haver evidência (F5).

## 0. Diagnóstico (inventário de 16/10)

### Como a web funciona hoje

| Etapa | Código | O que acontece |
| --- | --- | --- |
| Busca | `infra/collectors/web.py` | `ddgs` 9.16 `DDGS().text(" OR ".join(keywords[:3]), max_results=limit)`. **Uma consulta fixa por segmento.** |
| Mapeamento | idem | `CandidateVideo(platform="web")`, `platform_video_id = sha1(href)`, `title`, `description = body`, `raw = {href, title, body}`. Sem métricas. |
| Persistência | `VideoRepositoryImpl.upsert_candidates` | `videos` (dedupe por `sha1(href)` cru), `video_segments` (`discovery_source="web"`) e `video_metrics` com tudo `NULL`. |
| Score | `app/score.py` + `domain/scoring.py` | O `viral_score` dos vídeos: sem métricas, sobram `recency` (sem data, 50 fixo) e `relevance_of`. |
| Relevância | `relevance_of` | Substring de keyword em título+descrição, `hits / len(keywords)`. Com 16 keywords, uma página que casa 1 delas recebe 6,25%. |

### Campos × o que existe

| Campo | Situação |
| --- | --- |
| segmento | ✅ `video_segments` |
| consulta exata | ❌ não gravada |
| provedor | ❌ o `ddgs` agrega engines e não diz a origem: é **desconhecido** |
| posição | ❌ só existe a ordem da lista do `ddgs` (agregada), que não é gravada |
| URL original | ✅ `videos.url` (pelo dedupe atual) |
| URL canônica | ❌ |
| data da coleta | ✅ `videos.first/last_collected_at` (sem vínculo com a consulta) |
| versão do coletor | ❌ |
| data de publicação | ❌ o `ddgs.text` não devolve: 0 de 40 páginas |
| score, versão e explicação web | ❌ |
| marcação explícita de anúncio | ❌ o `ddgs.text` só devolve `title/href/body`: não existe flag de publicidade |

### Dados reais (produção, 16/10)

| Segmento | Páginas | `bing.com/aclick` | Títulos distintos | Marketplaces |
| --- | --- | --- | --- | --- |
| velas-artesanais | 5 | 0 | 5 | 0 |
| sabonetes-artesanais | 7 | 0 | 7 | 0 |
| resina-artesanal | 15 | **12** | 10 | 2 |

1. **Entrada contaminada:** em resina, 12 das 15 páginas são cliques de
   anúncio do Bing, vários com título repetido.
2. **A amostra não sustenta avaliação:** são 27 páginas, porque a consulta é
   fixa.
3. **As frases por intenção não existem ainda no vocabulário:** o Suggest,
   semeado só com o nome do segmento, devolve ~145 `keyword_signals` por
   segmento quase todos de produto — `resolver`/`tendencia` ficam sem frases.
   Resolvido pela opção (a): **sondas de intenção** (`probe-v2`) alimentam o
   Suggest antes de gerar o `q1` (ver "Protocolo de consultas").
4. **Atualidade não é avaliável** com o `ddgs`, porque não há data.

## Cronograma

> **Deslocamento registrado (16/10):** o deploy do worker foi adiado para
> **21/10 depois das 19:20 UTC** (fim da janela de campanhas). Além disso, o
> `q1` só é gerado **depois do primeiro ciclo de research com as sondas**
> (`probe-v2`), que roda no worker (cron diário). O cronograma é recalculado a
> partir da **primeira coleta efetiva** com o protocolo novo — não se "recupera"
> o atraso reduzindo coletas nem anotando amostra incompleta.

| Fase | Entrega | Quando |
| --- | --- | --- |
| F0 + F1 | Filtro com rastro, proveniência, protocolo `q1` aprovado | código até 21/10; deploy na noite de 21/10 (após 19:20 UTC) |
| — | Ciclo de research com as sondas `probe-v2` | primeiro cron após o deploy |
| — | Gerar o `q1` a partir dos sinais novos; revisão do usuário | após o research |
| — | Coleta com o protocolo `q1` | `ceil(len(protocolo)/3)` coletas (5 quando as 4 classes estão completas = ~2,5 dias) |
| F2 | Amostra depois do último collect do ciclo; anotação | após o ciclo completo |
| F3 | Score `web-v1` e linha de base congelada, na CLI | sobre os dados gravados, **sem olhar a anotação** |
| — | Pré-registro dos critérios da F4 (commit) | já publicado em `validacao-web.md` |
| F4 | Avaliação pareada e relatório | após a anotação |
| F5 | Ativação, só se aprovado | depois da F4 |
| F6 | Potencial criativo por conteúdo | decisão depois da F4 |

## F0 — Filtro conservador e normalização, com rastro dos descartes

**Princípio:** descartar só com **sinal verificável**. Uma página nunca é
descartada por ter parâmetros na URL, por "parecer comercial" ou por ser
de marketplace. Marketplace é página legítima, cuja intenção (comercial) é
avaliada na F2/F3, não no filtro.

**Arquivos:** `src/content_factory/domain/web_urls.py` (novo, puro) e
`infra/collectors/web.py`.

1. `ad_signal(url, title, body) -> str | None` devolve o **motivo**
   verificável, ou `None`. Lista fechada e versionada (`AD_RULES_VERSION =
   "ads-v1"`):
   - domínio e caminho de redirecionamento publicitário: `bing.com/aclick`,
     `bing.com/aclk`, `duckduckgo.com/y.js`, `googleadservices.com`,
     `*.doubleclick.net`, `google.com/aclk`;
   - marcação explícita de publicidade, **se** o provedor expuser. O `ddgs`
     hoje não expõe; a regra fica preparada e documentada como inativa.
   Título com "oferta" ou "desconto" **não** é sinal.
2. `canonical_url(url) -> str`:
   - host em minúsculas, sem fragmento;
   - remove **só** os parâmetros de rastreio de uma lista fechada (`utm_*`,
     `fbclid`, `gclid`, `msclkid`, `mc_eid`, `srsltid`, `ref_src`) e ordena
     os que ficam;
   - **mantém** qualquer outro parâmetro (`?id=`, `?p=`, `?q=` podem mudar o
     conteúdo);
   - URL inválida volta como veio, com a marca `canonical_ok=false`, sem
     levantar exceção.
3. **Rastro:** cada resultado, descartado ou não, gera uma linha em
   `web_search_hits` (F1) com `status` (`kept` | `discarded_ad` |
   `duplicate`) e `discard_reason`. Os descartados **não** entram em
   `videos`, mas ficam auditáveis e amostráveis na F2, para medir o falso
   positivo do filtro. `CollectRun.skipped` soma os descartes, e o
   `ops-report` os mostra por motivo.
4. **Identidade:** `platform_video_id = sha1(canonical_url)` para páginas
   novas. As 27 antigas mantêm o id: a transição admite no máximo uma
   duplicata por página antiga, registrada no relatório.
5. **Duplicata na mesma coleta:** a mesma URL canônica vinda de duas
   consultas grava uma página em `videos` e **dois** hits (as duas
   consultas e posições são proveniência válida).

**Testes (unit):** cada padrão de anúncio é descartado com o motivo certo;
URL de marketplace, com `?id=`, com "oferta" no título ou com `utm_` (é
canonicalizada, não descartada) **não** é descartada; idempotência de
`canonical_url`; fragmento e host; URL inválida não levanta exceção;
`AD_RULES_VERSION` vai para o hit.

## F1 — Proveniência completa e protocolo fixo de consultas

### Tabela `web_search_hits` (migração `0010`)

Uma linha por resultado devolvido, inclusive os descartados.

| Coluna | Tipo | Conteúdo |
| --- | --- | --- |
| `id` | PK | |
| `segment_id` | FK | |
| `video_id` | FK NULL | `NULL` quando descartado |
| `collected_at` | timestamptz | |
| `query` | text | **consulta exata** enviada |
| `query_id` | varchar | id no protocolo (ex.: `q1:resolver:03`) |
| `query_intent` | varchar | `aprender` / `resolver` / `tendencia` / `comercial` / `seed` |
| `provider` | varchar | `unknown` enquanto o `ddgs` agregar engines; nunca uma origem presumida |
| `provider_result_position` | int | posição **na lista devolvida pelo `ddgs`**, 1-based. Não é a posição no Google ou no Bing |
| `url_original` | text | `href` como veio |
| `canonical_url` | text | |
| `status` | varchar | `kept` / `discarded_ad` / `duplicate` |
| `discard_reason` | varchar NULL | ex.: `ad:bing_aclick` |
| `collector_version` | varchar | ex.: `web-c2` (sobe quando o coletor muda de comportamento) |
| `rules_version` | varchar | `ads-v1` |
| `title`, `snippet` | text | como devolvidos (o descartado não tem linha em `videos`) |

É append-only, com índice `(segment_id, collected_at)`. O domínio é
derivado da URL canônica e não ganha coluna.

### Protocolo de consultas `web-queries-q1`

Fixo e versionado **antes** da coleta. Não se escolhem "as que parecem
promissoras".

**Fonte dos sinais — sondas de intenção (`probe-v2`):** o Suggest é semeado
com as keywords do segmento **e** com sondas de intenção
(`domain/suggest_probes.py`) sobre os **termos-cabeça** do segmento
(`SegmentConfig.probe_terms`): `como fazer <termo>`, `<termo> nao`,
`<termo> tendencia`, `<termo> mais vendido`, `<termo> preco`. Medido em 16/10:
a sonda só rende no termo curto (`vela nao` → 10; `vela artesanal nao` → 0).
Sem isso o autocompletar devolve só variações de produto e
`resolver`/`tendencia` ficam sem frases (opção (a) da revisão de 16/10,
registrada em [validacao-web.md](../manual/validacao-web.md) e com os números
brutos em [evidencia-sondas-suggest.md](../manual/evidencia-sondas-suggest.md)).
As sondas
alimentam **só** o Suggest; os termos das redes sociais continuam sendo as
keywords do segmento, e as frases continuam vindo do autocompletar real — nada
é inventado.

1. **Classificação por intenção** por léxico fechado e versionado
   (`INTENT_LEXICON_VERSION = "intent-v3"`, só português), sobre as frases do
   Suggest de cada segmento:
   - `aprender`: como, passo a passo, receita, para iniciantes/iniciante(s);
   - `resolver`: erro, por que, problema, afunda, rachou, manchada, anvisa,
     legislação, tem que, tem cheiro, e `nao` **apenas** junto de um desfecho
     ruim (`nao acende`, `nao endurece`, `nao seca`, `nao queima`, ...);
   - `tendencia`: tendência, novidade, em alta, lançamento, mais vendidos —
     `ideias/natal/moda` só **com** contexto de atualidade/sazonalidade;
   - `comercial`: preço, valor, vender, lucro, dá dinheiro, embalagem,
     fornecedor, comprar.
   Marcadores podem ser **conjunções** de frases (ex.: `ideias` + `2026`): a
   palavra ambígua sozinha não define a classe. Frase que casa mais de uma
   classe vai para a primeira, nessa ordem documentada. Frase sem marcador vai
   para `sem_intencao` e não entra.
   Antes da classificação, um **filtro objetivo** (`filter-v1`, gravado no YAML
   como `filter_version`) descarta frase em inglês/espanhol e frase com marca
   industrial de produto acabado — motivo e efeito medido em
   [validação web](../manual/validacao-web.md).
2. **Seleção estratificada e cega:** de cada classe, **3 frases sorteadas
   com seed fixa** (`seed = 20261017`). O sorteio não usa o peso do Suggest,
   para não favorecer as consultas "boas". Também entra 1 consulta `seed`
   (as keywords do segmento, como hoje), que serve de controle. Uma classe
   com menos de 3 frases leva as que houver, e a falta fica registrada.
3. **Congelamento:** `cf web-queries --segment X --propose` gera
   `segments/web-queries/<slug>.q1.yaml` com as consultas, a classe, a seed e
   as versões. **O usuário revisa e aprova** (só pode vetar uma frase
   inadequada; o veto e o motivo são registrados e a substituta é a próxima
   do sorteio). O arquivo vai para o commit e é carregado no banco por
   `cf segment load`.
4. **Rodízio:** o protocolo tem `4 × 3 + 1 = 13` consultas quando as quatro
   classes têm frases suficientes; com uma classe parcial o total cai, e o
   ciclo completo passa a ser `ceil(len(protocolo)/3)` coletas. Cada coleta usa
   `WEB_QUERIES_PER_RUN` (default 3) em ordem cíclica determinística. Com 2
   coletas por dia, 13 consultas levam cerca de dois dias e meio (5 coletas), o
   que garante que **todas as intenções** sejam coletadas antes da amostra. Um
   segmento **parcial** (classe sem frases suficientes) é marcado como parcial
   e exploratório — **não** conta como aprovação da F1.
5. **Mudar consultas = nova versão** (`q2`). Hits de versões diferentes
   nunca se misturam numa mesma avaliação.

**Arquitetura:** `plan_web_queries(protocol, run_index, n)` fica em
`domain`, puro. O coletor recebe as consultas prontas por um adaptador por
segmento (o padrão do `SearchBoundYoutube`), e a porta `Collector` não muda.

**Vazio × falha:** o `ddgs` não distingue "sem resultado" de "engine
bloqueada" (E-4.3). Por consulta, grava-se `hits = 0` com o status da run.
Uma consulta vazia num ciclo em que as outras trouxeram resultado indica
vazio legítimo; todas vazias continua "ambíguo". Nada além disso é
inventado.

**Testes:** léxico (cada classe, ordem de precedência, frase sem
marcador, marcador-conjunção, positivos e negativos); sondas de intenção
(limitadas, determinísticas, classificáveis e **só** no Suggest); sorteio
determinístico pela seed e independente do peso; classe com menos de 3 frases;
rodízio cobre o protocolo em `ceil(len(protocolo)/3)` coletas;
`provider='unknown'`; posição 1-based; integração da gravação dos hits,
inclusive os descartados, em `cf-test-pg`; isolamento entre segmentos; modo
obrigatório (`WEB_QUERIES_REQUIRED=1`) falha com `missing_protocol` e não roda
o legado.

**Aceite da F1:** depois de um ciclo completo, cada segmento PT tem
resultados de **todas** as classes com frases, e cada página tem a consulta,
a posição, a URL original e a URL canônica. Segmento cuja classe segue sem
frases mesmo após as sondas é marcado **parcial e exploratório** — não conta
como aprovação da F1.

## F2 — Conjunto de avaliação

### Amostra: `cf web-sample --segment X --out arquivo.csv`

Por segmento, **50 candidatos**, seed fixa gravada no CSV:

| Estrato | Quantidade | Para medir |
| --- | --- | --- |
| Topo da linha de base | 15 | precisão do ranking atual |
| Aleatório entre os `kept` | 25 | o que o ranking deixa de fora; base da comparação pareada |
| Descartados pelo filtro | até 10 | falso positivo do filtro (cobertura) |

Total de **≤ 150 itens** nos 3 segmentos. O CSV traz `item_id`, a URL
canônica, o domínio, o título, o snippet, a consulta exata e a classe de
intenção da consulta. **Não** traz score novo, posição da linha de base nem
estrato: a anotação é cega e a ordem das linhas é embaralhada.

### Rótulos (definidos antes de anotar)

| Coluna | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `tema` | não trata do segmento | trata de forma **efetiva** mas secundária | o segmento é o assunto central |
| `intencao` | não atende à intenção da consulta | atende em parte | atende claramente |
| `utilidade` | nada aproveitável | dá uma ideia, sem detalhe | material concreto (técnica, erro, dado, exemplo) para conteúdo original |
| `invalido` | — | marcador: `anuncio` / `vazia` / `duplicata` / `inacessivel` | — |
| `motivo` | obrigatório quando `tema` ≤ 1 ou quando há `invalido`; uma frase | | |

**Positivo (pré-definido):**
- relevante = `tema ≥ 1` **e** sem `invalido`. Página que só menciona o
  tema de passagem é 0;
- útil = `utilidade ≥ 1` e sem `invalido`;
- página legítima (para a cobertura) = sem `invalido`.

**Tempo:** cerca de 1 min por item, o que dá **~2 h 30 min** no total.
Proposta: 2 sessões, 19/10 à noite (velas, sabonetes) e 20/10 (resina). O
protocolo, com exemplos reais de cada nota, fica em
`docs/manual/anotacao-web.md`.

**Anotadores:** um só (o usuário). Por isso a métrica se chama
**"concordância do score com o rótulo humano"**, e nunca concordância entre
anotadores. Opcional: um segundo anotador em 30 itens comuns daria um kappa
de Cohen, reportado à parte, como teto de qualidade do rótulo.

**Versionamento:** `docs/data/web-eval/<slug>.csv` com `item_id`, os
rótulos e a seed. URL e título vêm do banco na avaliação; o repositório é
público, e o teste de privacidade cobre a pasta.

## F3 — Score `web-v1` e linha de base congelada (só CLI)

**Arquivos:** `domain/web_relevance.py` (puro), `app/web_rank.py` e a
tabela `web_page_scores` (migração `0010`): `video_id`, `segment_id`,
`computed_at`, `score`, `score_version`, `components JSONB` e
`explanation JSONB`, append-only.

### Componentes

Cada um em [0, 1] ou `None` (não avaliado). O score é a média ponderada
**só dos avaliados**, com os pesos renormalizados e o resultado × 100. Tudo
`None` dá score `None`, nunca 0.

| Componente | Peso | Regra | `None` quando |
| --- | --- | --- | --- |
| `tema` | 0,40 | keywords do segmento em título+snippet, normalizadas (caixa, acento) e casadas por **todos os tokens** da keyword. Cada keyword conta **uma vez**. Satura em 2: `min(1, casadas/2)` | o segmento não tem keywords |
| `intencao` | 0,25 | o título ou snippet tem um marcador da **mesma classe** da consulta (léxico `intent-v3`); marcador que depende de palavra genérica (`nao`) não conta na página | consulta `seed` ou sem classe |
| `metadados` | 0,15 | média de: título de 15 a 120 caracteres e não só o nome do domínio; snippet ≥ 60 caracteres; `canonical_ok` | nunca |
| `atualidade` | 0,10 | faixas do `trend_decay` | sem data, ou seja, sempre com o `ddgs` hoje |
| `posicao` | 0,10 | `1 − (p − 1)/N`, melhor `provider_result_position` da última coleta | sem hit |

Os pesos são hipótese: constante `WEB_WEIGHTS` com `WEB_SCORE_VERSION =
"web-v1"`. Mudar regra ou peso sobe a versão. A `explanation` registra, por
componente, a evidência: as keywords casadas, o marcador, os testes de
metadados que falharam e a posição usada.

### Linha de base reproduzível

`baseline_web_score` (`base-fase-h`) é uma cópia congelada da regra atual
aplicada a uma página: `viral_score` com métricas ausentes, `relevance_of` e
`recency` = 50. É uma função pura com teste de equivalência contra
`domain/scoring.py` na data do congelamento. Assim, se o score de vídeo
mudar no futuro, a linha de base não muda junto.

### Diversidade e duplicatas: seleção, não score

- `dedupe_titles`: título normalizado idêntico dentro do segmento fica só
  com o de maior score. O descartado aparece na `explanation` do mantido.
- `select_top(k, max_per_domain)`: limita páginas por domínio **na lista
  final**, sem mexer em score nenhum. Na F4 o top-k é medido **com e sem**
  esse limite, para ver se a diversidade rebaixa páginas excelentes.

**Comando:** `cf web-rank --segment X [--compare]` grava `web_page_scores`
e devolve no envelope os dois rankings (ids). **O worker não muda.**

**Testes:** limites e renormalização (de 1 a 4 componentes `None`);
determinismo (mesma entrada e versão dão o mesmo score e a mesma
explicação); keyword repetida ou em título e snippet conta 1; casamento por
tokens ("cera de soja" ≠ "soja"); acento e caixa; snippet vazio; ausência
de data; `select_top` não altera scores; `dedupe_titles`; isolamento entre
segmentos; **não contaminação** (integração: `web-rank` não escreve em
`video_scores`); erro de infraestrutura propaga; envelope `{"ok": …}`.

## F4 — Avaliação pareada com critérios pré-registrados

### Pré-registro

Em 19/10, **antes** de gerar a amostra, um commit fixa em
`docs/manual/validacao-web.md` as definições abaixo, as versões (`q1`,
`ads-v1`, `intent-v3`, `filter-v1`, `web-v1`, `base-fase-h`) e as seeds. Depois disso,
mudar um critério exige registrar o motivo e a data, e o resultado é
reportado pelos dois critérios.

### Comparação pareada

As duas funções (`base-fase-h` e `web-v1`) ordenam **exatamente o mesmo
conjunto**: os itens `kept` anotados do segmento, excluindo o estrato de
descartados. O empate é desfeito por `video_id`. Nenhum ranking enxerga um
candidato que o outro não viu.

### Métricas: definição operacional e papel

| Métrica | Cálculo | Papel | Meta |
| --- | --- | --- | --- |
| **P@5** | relevantes entre os 5 primeiros / 5 | **critério** | ≥ 0,6 por segmento |
| **P@10** | relevantes entre os 10 primeiros / 10 | **critério** | ≥ 0,5 |
| **Ganho em P@10** | P@10(v1) − P@10(base), no mesmo conjunto | **critério** | ≥ 0,1, **com o limite inferior do IC 95% > 0** |
| **Cobertura do filtro** | legítimas preservadas / legítimas antes do filtro, estimada pelo estrato de descartados (legítimas descartadas = falso positivo) | **critério** | ≥ 90% |
| Concordância score × rótulo | Spearman entre o score e `tema + intencao + utilidade`, sobre os `kept` anotados | indicador | referência: 0,5 |
| Diversidade | domínios distintos no top 10, com e sem `max_per_domain` | indicador | referência: 6 |
| Estabilidade | sobreposição média do top 10 (\|A∩B\|/10) entre 200 reamostragens bootstrap dos candidatos; e entre as duas últimas coletas | indicador | referência: 0,7 |
| Utilidade no top 10 | úteis entre os 10 primeiros / 10 | indicador (prepara a F6) | — |
| Custo/latência | tempo de `web-rank` por segmento; chamadas externas | indicador | < 2 s; 0 chamadas |

**Incerteza:** toda métrica sai com **n** e **IC 95% por bootstrap**
(1.000 reamostragens dos itens, seed fixa). Com 40 a 50 itens por segmento,
os resultados são **direcionais**. O relatório diz isso e nunca transforma
uma diferença pontual em garantia.

### Veredito por segmento

| Veredito | Condição |
| --- | --- |
| **aprovado** | os 4 critérios atingidos, com o ganho de IC inferior > 0 |
| **direcional** | metas pontuais atingidas, mas o IC do ganho inclui 0 |
| **insuficiente** | < 40 itens `kept` anotados, ou uma classe de intenção sem dados. Amplia-se a amostra antes de concluir |
| **não atingido** | algum critério abaixo da meta com n suficiente. Leva a investigar dado, métrica ou segmento, não necessariamente a uma falha do projeto |

**Relatório** (`docs/manual/validacao-web.md`): tabela por segmento
(base × v1, com IC), os 5 piores erros da v1 com a `explanation`, os falsos
positivos do filtro e a decisão. Uma v2 sobe a versão e repete a F4 com o
**mesmo** pré-registro.

## F5 — Ativação (só com evidência)

Condição: veredito **aprovado** em pelo menos 2 dos 3 segmentos PT, sem
nenhum "não atingido" por cobertura.

1. O `score_job` passa a gravar `web_page_scores` (só CPU), atrás de
   `WEB_SCORE_ENABLED`.
2. A web sai do `viral_score` (`score()` filtra `platform != "web"`). Muda
   só a mistura que o B3 já não usa, fica anotado no diário e o
   `FORMULA_VERSION` não sobe (a fórmula não muda, muda o escopo).
3. `cf web-top --segment X --k 10` entrega as melhores referências à próxima
   etapa, sem extrair conteúdo.
4. **Rollback:** `WEB_SCORE_ENABLED=0` volta ao comportamento anterior. A
   migração `0010` é aditiva, e o `downgrade` remove só as duas tabelas.

Sem aprovação, F0 e F1 continuam (qualidade da coleta e proveniência valem
sozinhas) e o `web-v1` segue como sinal experimental, fora do worker.

## F6 — Potencial criativo por conteúdo (fora do caminho crítico)

Decisão depois da F4. Esboço:

- extração só das top-k aprovadas (k = 10 por segmento por dia), com o
  `assert_public_http` (`infra/ssrf.py`; TOCTOU em
  [ADR-0008](../adr/0008-ssrf-toctou-adiado.md)), timeout, limite de bytes e
  `robots.txt`;
- rubrica de 5 dimensões de 0 a 2 (utilidade, especificidade,
  adaptabilidade, ângulo, evidência), **anotada por humano primeiro**;
  resultado `creative_potential`, nunca `viral`;
- IA generativa só com ganho medido contra o rótulo humano, em plano próprio.

## Fora de escopo

Estimativa de tráfego; SEO pago; IA sobre página não processada; busca
semântica; o `insta_kb` (bloqueado até o content-factory fechar 100%);
score único misturando vídeo, página e potencial.

## Gates por PR

pytest, ruff, pyright strict 0, `alembic check`, bandit, pip-audit,
`mkdocs --strict`, privacidade. TDD em todo `domain/`. Deploy só na janela
noturna (depois do score das 21:30 BRT, com a fila vazia).

## Decisões registradas (usuário, 2026-10-16)

- **Limiares:** P@5, P@10 com ganho e cobertura são **critérios**, com as
  definições acima. Concordância, diversidade e estabilidade são
  **indicadores** até haver calibração. Os critérios são pré-registrados
  antes da F4.
- **Consultas diversificadas durante o experimento:** sim, com o protocolo
  fixo `q1` estratificado por intenção e cego ao peso, versionado e com a
  consulta exata gravada por resultado.
- **Anotação:** pelo usuário, em 19/10 (noite) e 20/10, com a coleta e a
  normalização estáveis.

## Pendente do usuário

- Revisar e aprovar o arquivo `segments/web-queries/<slug>.q1.yaml` de cada
  segmento (só veto com motivo), em 17/10, antes do deploy.
