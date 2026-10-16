# Relevância web e potencial de conteúdo — plano de implementação

Data: 2026-10-16. Origem: especificação técnica "relevância web e potencial
de conteúdo" (recebida em 16/10). Este plano a confronta com o código e os
dados reais antes de propor mudanças.

> **Regra do experimento:** a coleta vai até 2026-10-21 e o B3 vem depois.
> Nada aqui altera `viral_score`, `FORMULA_VERSION`, `video_scores` nem os
> critérios do B3. A web já está **fora do B3** (decisão de 16/10), então
> mudar a **coleta web** não contamina a validação dos vídeos. O score web
> novo nasce em modo comparativo, em tabela própria.

## 0. Diagnóstico (inventário feito em 16/10)

### Como a web funciona hoje

| Etapa | Código | O que acontece |
| --- | --- | --- |
| Busca | `infra/collectors/web.py` (`WebCollector.discover`) | `ddgs` 9.16 `DDGS().text(" OR ".join(keywords[:3]), max_results=limit)`. **Uma consulta fixa por segmento**, sempre a mesma. |
| Mapeamento | idem | Vira `CandidateVideo(platform="web")` com `platform_video_id = sha1(href)`, `title`, `description = body` e `raw = {href, title, body}`. Sem métricas. |
| Persistência | `VideoRepositoryImpl.upsert_candidates` | Grava em `videos` (dedupe por `(platform, sha1(href))`), `video_segments` (`discovery_source="web"`) e `video_metrics` com tudo `NULL`. |
| Score | `app/score.py` + `domain/scoring.py` | O mesmo `viral_score` dos vídeos: sem métricas, sobram `recency` (sem data, 50 fixo) e `relevance_of`. |
| Relevância | `relevance_of` | Fração das keywords do segmento que aparecem como substring em título e descrição: `hits / len(keywords)`. Com 16 keywords, uma página que casa 1 delas recebe 6,25%. |

### Campos da especificação × o que existe

| Campo lógico | Situação | Observação |
| --- | --- | --- |
| `segment_id` | ✅ `video_segments` | |
| `query` | ❌ não gravado | A consulta é derivada das keywords e se perde. |
| `provider` | ❌ indisponível | O `ddgs` agrega engines (bing, brave, google…) e o resultado não diz de qual veio. Só fixando `backend=` por chamada. |
| `provider_rank` | ❌ não gravado | Só existe a ordem da lista devolvida pelo `ddgs`, que é **agregada**, não a posição real no provedor. |
| `url` | ✅ `videos.url` | |
| `canonical_url` | ❌ | Hoje o dedupe é por `sha1(href)` cru: os mesmos parâmetros de rastreio com valores diferentes viram páginas diferentes. |
| `title` / `snippet` | ✅ `title` / `description` | |
| `domain` | ❌ coluna | É derivável da URL; não precisa de coluna. |
| `published_at` | ❌ sempre `NULL` | O `ddgs.text` não devolve data (0 de 40 páginas têm). |
| `content_fetched_at` | ❌ | Não há extração de conteúdo. |
| `relevance_score` | ⚠️ | Existe `video_segments.relevance_score`, que hoje não é usado no ranking, e `video_scores.relevance`, que mede só substring. |
| `creative_potential_score` | ❌ | |
| `score_version` | ⚠️ | `video_scores.formula_version` é do score de vídeo, não da web. |
| `score_explanation` | ❌ | |

### Dados reais (produção, 16/10)

| Segmento | Páginas | Anúncios `bing.com/aclick` | Títulos distintos | Marketplaces |
| --- | --- | --- | --- | --- |
| velas-artesanais | 5 | 0 | 5 | 0 |
| sabonetes-artesanais | 7 | 0 | 7 | 0 |
| resina-artesanal | 15 | **12** | 10 | 2 |

Conclusões:

1. **O problema mais grave não é o score, é a entrada.** Em resina, 12 das
   15 páginas são cliques de anúncio do Bing (Temu, "Em Oferta"), e várias
   têm título repetido com URLs diferentes só pelos parâmetros. Nenhum score
   de relevância conserta isso: é filtro e canonicalização.
2. **A amostra é pequena demais para avaliar.** São 27 páginas nos 3
   segmentos. Com a consulta fixa, o `COLLECT_LIMIT=25` (a partir do
   deploy noturno de 16/10) aumenta o volume por coleta, mas repete as
   mesmas páginas. Para avaliar, é preciso **diversificar as consultas**.
3. **Os sinais de intenção já existem:** o Suggest grava ~145
   `keyword_signals` por segmento ("como fazer vela aromática", "resina
   epóxi da dinheiro"…). São as consultas por intenção que a especificação
   (§4.2) propõe, sem precisar de um classificador.
4. **Atualidade não é avaliável com o `ddgs`:** nenhuma data. O componente
   existe, mas fica "não avaliado" e sai da média (a mesma política de
   métrica ausente do `viral_score`, Fase G).

## 1. Fases e dependências

```
F0  Entrada limpa ──► F1  Proveniência + consultas diversas ──► (coleta 2–3 dias)
                                                                  │
F3  Score web v1 (puro, comparativo) ◄── F2  Amostra + anotação ◄─┘
        │
        ▼
F4  Avaliação contra os limiares ──► F5  Ativação (ou não)
                                       │
                                       ▼
                       F6  Potencial criativo (conteúdo) — decisão após F4
```

Calendário proposto: F0 e F1 com deploy na janela noturna de 16 ou 17/10;
coleta até 19/10; F2 (anotação) em 19–20/10; F3 em paralelo, sobre os dados
gravados; F4 em 21–22/10, junto do B3; F5 depois do B3.

## F0 — Entrada limpa (bug, não hipótese)

São regras determinísticas e sem peso: corrigem dado errado. Entram antes de
qualquer score, porque um score calculado em cima de anúncio mede ruído.

**Arquivos:** `src/content_factory/domain/web_urls.py` (novo, puro) e
`infra/collectors/web.py`.

1. `canonical_url(url) -> str`: põe o host em minúsculas, remove o fragmento
   e os parâmetros de rastreio conhecidos (`utm_*`, `fbclid`, `gclid`,
   `msclkid`, `ref`, `ref_src`, `srsltid`), **mantém** os outros parâmetros
   (que podem mudar o conteúdo), ordena os que ficam e normaliza a barra
   final só no path vazio.
2. `is_ad_redirect(url) -> bool`: `bing.com/aclick`, `bing.com/aclk`,
   `duckduckgo.com/y.js`, `googleadservices.com`, `doubleclick.net`. O
   coletor **descarta** essas páginas e conta quantas descartou (log e
   `CollectRun.skipped`).
3. O `platform_video_id` passa a ser `sha1(canonical_url)`. As páginas
   antigas (27) mantêm o id antigo. A transição pode gerar no máximo uma
   duplicata por página antiga, o que é aceitável e fica documentado.
4. `raw` passa a guardar `href_original` quando difere da URL canônica
   (auditoria).

**Testes (unit):** parâmetros de rastreio removidos e parâmetros de conteúdo
mantidos (`?id=`, `?p=`); fragmento; host em maiúsculas; idempotência
(`canonical(canonical(u)) == canonical(u)`); cada padrão de anúncio; URL sem
esquema ou inválida (não levanta exceção: devolve a URL como veio e marca
como inválida); coletor descarta o anúncio e conta em `skipped`.

**Aceite:** um collect de resina sem nenhum `bing.com/aclick`, com a
contagem de descartes visível no `cf ops-report`.

## F1 — Proveniência e consultas diversificadas

**Migração `0010`** (só o que falta, conforme a tabela do §0):

```
web_search_hits                      -- 1 linha por (resultado × consulta × coleta)
  id, segment_id FK, video_id FK, collected_at,
  query TEXT, query_source VARCHAR(16)  -- 'seed' | 'suggest'
  position INT                          -- ordem na lista do ddgs (agregada)
  provider VARCHAR(32) NULL             -- NULL enquanto o ddgs agrega engines
  INDEX (segment_id, collected_at)
```

Por que uma tabela própria: a mesma página aparece em várias consultas e
coletas com posições diferentes, então isso não cabe em `videos`. É
append-only, como `trend_observations`. `domain` é derivado da URL e não
ganha coluna.

**Consultas:** `WebCollector.discover` passa a fazer **N consultas por
coleta** (`WEB_QUERIES_PER_RUN`, default 3), cada uma com `max_results =
limit // N`:

- 1 consulta das seeds, em rodízio determinístico pelas keywords do segmento
  (a coleta k usa a janela `k mod len`);
- as N−1 consultas restantes vêm dos `keyword_signals` do Suggest com maior
  peso, também em rodízio, para não repetir sempre os mesmos.

O coletor recebe as consultas prontas: quem escolhe é a camada `app`, numa
função pura `plan_web_queries(segment, signals, run_index, n)`. O coletor
continua sem acesso ao banco. A porta `Collector.discover(segment, limit)`
não muda: as consultas chegam por um adaptador por segmento, no mesmo padrão
do `SearchBoundYoutube`.

**Custo e risco:** o `ddgs` é gratuito, mas sofre rate limit
(`PLATFORM_MAX_CONCURRENCY` web=1 já vale). Com N=3 e 2 coletas por dia, são
6 consultas por segmento por dia. Se `web_empty` subir no `ops-report`, o
N volta para 1 por env, sem deploy.

**Distinguir vazio de falha (critério 4 da especificação):** o `ddgs`
devolve vazio tanto para "sem resultado" quanto para engine bloqueada
(auditoria E-4.3). Com N consultas, passa a haver um sinal: `hits_por_query`
no `raw` da run. Se todas as consultas vierem vazias, o resultado continua
"ambíguo"; se só algumas, foi vazio legítimo daquela consulta. Essa limitação
fica documentada e não é inventada nenhuma classificação.

**Testes:** `plan_web_queries` (rodízio, sem repetição na janela,
segmento sem sinais cai só nas seeds, determinismo pelo `run_index`);
gravação de `web_search_hits` (integração em `cf-test-pg`); `position`
1-based na ordem do `ddgs`; isolamento entre segmentos.

**Aceite:** depois de 2 dias, cada segmento PT com **≥ 60 páginas únicas e
canônicas** e a consulta de origem de cada uma registrada.

## F2 — Conjunto de avaliação (anotação humana)

**Comando:** `cf web-sample --segment X --n 60 --out arquivo.csv`. Faz uma
amostra **estratificada**: metade do topo do ranking atual (a linha de base)
e metade aleatória com seed fixa, gravada no CSV para ser reproduzível.
Assim se mede tanto a precisão do topo quanto o que o ranking deixa de fora.

**Colunas do CSV:** `video_id, url, domain, title, snippet, query,
query_source, position, baseline_rank`, e as colunas a anotar:

| Coluna | Escala | Pergunta |
| --- | --- | --- |
| `relevante` | 0/1/2 | Trata do nicho e do público do segmento? (0 fora, 1 tangencial, 2 central) |
| `intencao` | aprender / resolver / tendencia / comercial / outro | Qual necessidade a página atende? |
| `util` | 0/1/2 | Daria para tirar dela um conteúdo original? |
| `motivo` | texto curto | Por quê, em uma frase |

**Protocolo** em `docs/manual/anotacao-web.md`: definições com exemplos
reais de cada nota, regra para casos ambíguos e instrução para anotar **sem
ver o score novo** (o CSV não traz score novo, só a ordem da linha de base,
que pode ser escondida com `--blind`).

**Versionamento:** os CSVs anotados ficam em `docs/data/web-eval/` só com
`video_id` e as anotações. URL e título vêm do banco na hora da avaliação.
O repositório é público: nada de dados de terceiros além de URLs públicas.
O teste de privacidade cobre a pasta.

**Esforço:** 3 segmentos × 60 páginas ≈ 180 anotações, ~1 h 30 min de
trabalho humano.

**Aceite:** os 3 CSVs anotados, com o protocolo publicado e a seed registrada.

## F3 — Score web v1, puro e comparativo

**Arquivos:** `src/content_factory/domain/web_relevance.py` (puro);
`app/web_rank.py` (caso de uso); tabela `web_page_scores` na migração `0010`.

```
web_page_scores                      -- append-only, como video_scores
  id, video_id FK, segment_id FK, computed_at,
  score FLOAT, score_version VARCHAR(16)   -- 'web-v1'
  components JSONB                          -- {tema: 0.8, intencao: null, …}
  explanation JSONB                         -- evidências legíveis
```

**Componentes**, cada um em [0, 1] ou `None` (não avaliado). O score é a
média ponderada **só dos avaliados**, com os pesos renormalizados e o
resultado × 100:

| Componente | Peso | Regra v1 | `None` quando |
| --- | --- | --- | --- |
| `tema` | 0,40 | Keywords do segmento presentes em título+snippet, com acentos e caixa normalizados e casamento por **todos os tokens da keyword** (não substring). Cada keyword conta **uma vez**, não importa onde ou quantas vezes apareça. Satura em 2: `min(1, casadas / 2)`. | o segmento não tem keywords |
| `intencao` | 0,25 | A consulta de origem tem um marcador de intenção (léxico pt por classe: "como", "passo a passo" = aprender; "erro", "não", "por que", "afunda" = resolver; "tendência", "2026" = tendência; "preço", "vender", "comprar" = comercial) e o título ou snippet atendem à mesma classe. | a consulta não tem marcador |
| `metadados` | 0,15 | Média de 3 testes: título informativo (15–120 caracteres, não é só o nome do domínio), snippet com ≥ 60 caracteres e URL `https` válida. | nunca |
| `atualidade` | 0,10 | Decaimento por idade (mesma faixa do `trend_decay`). | sem `published_at`, ou seja, **sempre** com o `ddgs` hoje |
| `posicao` | 0,10 | `1 − (posição − 1) / N`, a **melhor** posição entre as consultas da coleta mais recente. | página sem `web_search_hits` (anterior à F1) |

Os pesos são hipótese, não valor validado. Ficam numa constante
`WEB_WEIGHTS` versionada com `WEB_SCORE_VERSION = "web-v1"`; mudar um
peso exige subir a versão. A `explanation` guarda, por componente, a
evidência: as keywords que casaram, o marcador de intenção, os testes de
metadados que falharam e a posição usada. É isso que responde "por que esta
página".

**Diversidade (separada do score):** `select_top(scored, k, max_per_domain=2)`
seleciona sobre a lista ordenada **sem alterar** o score de nenhuma página.
Antes dela, `dedupe_titles` remove título normalizado idêntico dentro do
segmento (casefold, sem acento, sem pontuação), mantendo o de maior score.
Similaridade aproximada fica fora da v1.

**Linha de base:** a ordem atual da web (o `viral_score` dos vídeos aplicado
a páginas) é recalculada do banco pela mesma função de avaliação. Assim os
dois rankings são comparáveis.

**Comando:** `cf web-rank --segment X [--top 10] [--compare]`. Calcula e
grava em `web_page_scores` e, com `--compare`, devolve no envelope as duas
listas lado a lado (ids). Fica **só na CLI** até a F5: o worker não muda e
o `score_job` não é tocado durante o experimento.

**Testes (unit, domínio puro):**

- limites: score em [0, 100]; pesos somam 1; renormalização com 1 a 4 `None`;
  tudo `None` dá score `None`, não 0;
- determinismo: mesma entrada e mesma versão dão o mesmo score e a mesma
  explicação;
- sinal contado uma vez: keyword repetida 5 vezes no título = 1 vez;
  keyword no título e no snippet = 1;
- tokens: "cera de soja" não casa com "soja" sozinha; acento e caixa não
  importam;
- dado ausente: sem data, `atualidade` é `None` com motivo "sem data";
  snippet vazio reprova o teste de metadados sem quebrar;
- diversidade: com `max_per_domain=2` e 5 páginas do mesmo domínio no topo,
  entram 2 e o score delas não muda;
- dedupe de títulos: títulos idênticos normalizados mantêm o maior score;
- isolamento: keywords de um segmento não afetam o score de outro;
- não contaminação: `web_rank` não escreve em `video_scores`. É um teste de
  integração que conta as linhas antes e depois.

**Integração:** gravação em `web_page_scores`, falha de infraestrutura
propagada sem ser engolida (padrão da Etapa 1 Task 11) e envelope `{"ok": …}`.

## F4 — Avaliação (limiares fixados ANTES de ver os resultados)

**Comando:** `cf web-eval --annotated docs/data/web-eval/<seg>.csv`,
somente leitura. Calcula as métricas para a linha de base e para a v1 sobre
as mesmas páginas.

Limiares propostos, **para aprovação do usuário antes da F4** (com
`relevante ≥ 1` = relevante e `util ≥ 1` = útil):

| Métrica | Definição | Limiar para a v1 |
| --- | --- | --- |
| Precisão no top 5 | relevantes / 5 (após dedupe e diversidade) | ≥ 0,6 por segmento |
| Precisão no top 10 | relevantes / 10 | ≥ 0,5 **e** ≥ a linha de base + 0,1 |
| Concordância humana | Spearman (score × `relevante + util`) | ≥ 0,5 (mesmo do B3) |
| Cobertura | páginas com `tema` e `metadados` avaliáveis | ≥ 90% |
| Diversidade | domínios distintos no top 10 | ≥ 6 |
| Estabilidade | Spearman entre duas coletas consecutivas (páginas em comum) | ≥ 0,7 (mesmo do B3) |
| Custo e latência | tempo de `web-rank` por segmento; chamadas externas | < 2 s; 0 chamadas externas |

Se a amostra de um segmento tiver menos de 40 páginas anotadas, o resultado
dele é "insuficiente" e não "reprovado": a amostra é ampliada antes de
qualquer conclusão.

**Relatório** em `docs/manual/validacao-web.md`: tabela por segmento
(linha de base × v1), onde a v1 melhora, piora ou não muda, e os 5 piores
erros da v1 com a `explanation`, para orientar a v2. Se reprovar: muda a
regra, sobe a versão (`web-v2`) e repete a F4. Os limiares não mudam.

## F5 — Ativação

Só com a F4 aprovada:

1. O `score_job` passa a calcular `web_page_scores` também (é barato: CPU,
   sem rede).
2. A web **sai do `viral_score`**: o `score()` filtra `platform != "web"`.
   Isso altera o ranking de vídeos, mas a web já estava fora do B3, então só
   muda a mistura que o B3 já não usa. Exige uma nota no diário e
   **não** sobe o `FORMULA_VERSION` (a fórmula não muda, muda o escopo).
3. Um `cf web-top --segment X --k 10` entrega as melhores referências à
   próxima etapa (critério 5 da especificação), sem extrair conteúdo.
4. **Rollback:** desligar a env `WEB_SCORE_ENABLED=0` volta ao estado
   anterior. As tabelas da `0010` são aditivas, e o `downgrade` remove as
   duas sem tocar nas outras.

Se a F4 reprovar e a v2 não resolver, a web continua coletando com
proveniência (F0 e F1 já têm valor sozinhas) e o score fica como sinal
experimental, fora do worker.

## F6 — Potencial criativo (fora do caminho crítico; decisão após a F4)

Precisa do conteúdo da página. Não é possível julgar potencial por título e
snippet com a mesma confiança.

- Extração **só das top-k validadas** (k=10 por segmento por dia), com o
  `assert_public_http` já existente (`infra/ssrf.py`; TOCTOU em
  [ADR-0008](../adr/0008-ssrf-toctou-adiado.md)), timeout, limite de bytes e
  respeito a `robots.txt`.
- Rubrica de 5 dimensões, de 0 a 2 cada (utilidade, especificidade,
  adaptabilidade, ângulo, evidência), **anotada por humano primeiro** sobre
  as mesmas páginas da F2. O nome do resultado é `creative_potential`,
  nunca `viral`.
- IA generativa só entra se uma comparação com a anotação humana mostrar
  ganho que pague o custo, com plano próprio.

## 2. Fora de escopo (da especificação, confirmado)

Estimativa de tráfego; SEO pago; IA sobre página não processada; busca
semântica nova; o `insta_kb` (bloqueado até o content-factory fechar 100%);
score único misturando vídeo, página e potencial.

## 3. Gates por PR

pytest, ruff, pyright strict 0, `alembic check` (F1 e F3), bandit,
pip-audit, `mkdocs --strict`, privacidade. TDD em toda função de `domain/`.
Deploy só na janela noturna (depois do score das 21:30 BRT, com a fila vazia).

## 4. Decisões pendentes do usuário

1. **Limiares da F4** (tabela acima): aprovar ou ajustar **antes** da
   anotação.
2. **Consultas diversificadas na web durante o experimento (F1):** muda a
   coleta web antes de 21/10. Recomendo fazer, porque a web está fora do B3
   e sem isso não há amostra para a F2.
3. **Quem anota a F2** (~1 h 30 min) e quando (proposta: 19–20/10).
4. **F6** só depois do resultado da F4.
