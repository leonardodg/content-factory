# Validação web — pré-registro (F4)

Critérios, versões e seeds **congelados antes** de gerar a amostra da F2
(plano [Relevância web e potencial](../ai-plans/2026-10-16-web-relevancia-potencial.md),
F3/F4). Este arquivo é o pré-registro: depois de publicado em commit, mudar
um critério exige registrar **motivo e data**, e o resultado passa a ser
reportado pelos dois critérios.

## Versões congeladas

| Elemento | Versão | Onde |
| --- | --- | --- |
| Protocolo de consultas | `q1` (`web-queries-q1`) | `segments/web-queries/<slug>.q1.yaml` |
| Léxico de intenção | `intent-v4` (era `intent-v3`; adendo de 17/10) | `domain/web_queries.py` |
| Filtro objetivo pré-sorteio | `filter-v2` (era `filter-v1`; adendo de 17/10) | `domain/web_queries.py` |
| Sondas de intenção do Suggest | `probe-v2` | `domain/suggest_probes.py` |
| Regras de anúncio | `ads-v1` | `domain/web_urls.py` |
| Normalização de URL | `canon-v1` | `domain/web_urls.py` |
| Score novo | `web-v1` | `domain/web_relevance.py` |
| Linha de base congelada | `base-fase-h` | `domain/web_relevance.py` |

Seeds fixas: sorteio de consultas `20261017`, amostra `20261019`, bootstrap
`20261020` (1.000 reamostragens).

## O que está sendo comparado

Duas funções ordenam **exatamente o mesmo conjunto**: os itens `kept`
anotados do segmento, excluindo o estrato de descartados. O desempate é
fixo por `video_id`. Nenhum ranking enxerga um candidato que o outro não viu.
Na avaliação **não** há remoção de títulos repetidos (`dedupe_titles` escolhe
o representante pelo próprio score e daria conjuntos diferentes a cada lado).
Páginas de anúncio gravadas antes do filtro `ads-v1` ficam fora do conjunto.

A avaliação é **reprodutível**: `cf web-eval --as-of` lê só os hits com
`collected_at <= as_of` (o `as_of` do envelope do `cf web-sample`) e usa esse
instante como relógio do componente `atualidade`. A amostra só é gerada com o
ciclo `q1` completo (todas as consultas com ao menos uma coleta).

- **`base-fase-h`** — cópia congelada da regra atual (o `viral_score` com
  métricas ausentes + `relevance_of` + `recency`). Se o score de vídeo mudar
  no futuro, a linha de base **não** muda junto.
- **`web-v1`** — score novo: média ponderada dos componentes avaliados
  (`tema` 0,40, `intencao` 0,25, `metadados` 0,15, `atualidade` 0,10,
  `posicao` 0,10), renormalizada, ×100.

## Critérios pré-registrados

Toda métrica sai com **n** e **IC 95% por bootstrap** (1.000 reamostragens
dos itens, seed fixa). Com 40 a 50 itens por segmento, os resultados são
**direcionais**: uma diferença pontual nunca vira garantia.

| Métrica | Cálculo | Meta |
| --- | --- | --- |
| **P@5** | relevantes entre os 5 primeiros / 5 | ≥ 0,6 por segmento |
| **P@10** | relevantes entre os 10 primeiros / 10 | ≥ 0,5 por segmento |
| **Ganho em P@10** | P@10(v1) − P@10(base), no mesmo conjunto | ≥ 0,1, **com o limite inferior do IC 95% > 0** |
| **Cobertura do filtro** | legítimas preservadas / legítimas antes do filtro (estimada pelo estrato de descartados) | ≥ 90% |

Definições de "relevante" e "legítima" em
[Anotação da amostra web](anotacao-web.md).

### Adendo registrado — 2026-10-16: P@k é sobre a amostra

**Motivo:** explicitar o que o cálculo já faz, sem alterar nenhum número. As
métricas P@5, P@10 e o ganho em P@10 são medidas sobre o **conjunto anotado da
amostra** (os itens `kept` anotados), não sobre o ranking completo da coleta em
produção: candidatos que não entraram na amostra não competem pelo topo. O
estrato de 15 itens do topo da linha de base sempre está presente, mas um item
fora da amostra poderia subir no ranking `web-v1` e não ser medido. O teste
pareado (v1 − base, sobre a mesma amostra) continua válido para comparar as duas
funções; as metas absolutas de P@5/P@10 valem como **P@k sobre a amostra** (piso
aproximado), não como a precisão exata do top 5/10 completo.

### Adendo registrado — 2026-10-16: sondas de intenção no Suggest (opção (a))

**Motivo:** o Suggest, semeado só com o nome do segmento, devolve variações de
produto; `resolver` e `tendencia` ficavam praticamente sem frases (cobertura
medida no banco real: 0 a 1 frase de tendência por segmento). A opção (a) passa
a **sondar o autocompletar pela intenção**, com os termos-cabeça declarados por
segmento (`SegmentConfig.probe_terms`) e os modelos `probe-v2`
(`domain/suggest_probes.py`): `como fazer <termo>`, `<termo> nao`,
`<termo> tendencia`, `<termo> mais vendido`, `<termo> preco`. As frases
continuam vindo do autocompletar real: nada é inventado. Isso muda o
**research** (a coleta de sinais que alimenta a seleção do `q1`), não os
critérios — nenhum número pré-registrado muda.

**Evidência (16/10, autocompletar real, sem escrever no banco; números brutos em
[Evidência — sondas do Suggest](evidencia-sondas-suggest.md)):** a sonda só
rende na **cabeça do termo**, não na cauda longa — `vela artesanal nao` e
`vela artesanal tendencia` devolvem **0** sugestões, enquanto `vela nao` (10),
`vela tendencia` (10), `pavio nao` (8), `sabonete mais vendido` (10) e
`artesanato mais vendido` (10) devolvem frases classificáveis. Por isso os
termos sondados são `probe_terms` (velas: `vela`/`velas`/`pavio`; sabonetes:
`sabonete`/`sabonetes`/`saboaria`; resina: `resina`/`resina epoxi`/`artesanato`),
não as keywords do segmento. Trocar o `client` do endpoint (firefox/chrome) não
muda o resultado: o limite é o **dado** do nicho, não a consulta.

!!! warning "Limitação: o estrato `tendencia` é induzido pela sonda"
    A sonda de tendência leva o próprio marcador, então a frase resultante cai
    na classe por construção. A consulta é uma busca real de usuário (o Google
    só completa o que as pessoas buscam), mas o estrato `tendencia` do `q1` é
    **induzido** pela sonda, não "descoberto" passivamente. Na leitura da F4,
    tratar `tendencia` como piso exploratório — não como amostra natural do
    vocabulário do segmento.

!!! warning "Limitação: termos-cabeça são ambíguos em PT"
    `resina` também é resina odontológica (`resina nao clareia`), `vela` também
    é vela de ignição (`vela nao sai do motor`) e vela de gráfico
    (`velas tendencia alcista`). Parte das frases do `resolver`/`tendencia`
    fica fora do segmento. O `q1` é revisado pelo usuário, que **veta** a frase
    inadequada (`cf web-queries --veto/--reason`); o veto é registrado no YAML.

### Adendo registrado — 2026-10-16: `intent-v3` + filtro `filter-v1`

**Motivo:** o QA do commit `4f56a0a` (sorteio cego com a seed `20261017` contra o
autocompletar real) mostrou que as 4 classes fechavam nos 3 segmentos, mas de 4 a
7 das 13 consultas saíam **fora do nicho ou em outro idioma**: frases em inglês
(os marcadores `tutorial`/`for beginners`/`sale`/`price`/`wholesale` deixavam
passar), marcas industriais (`sabonete mais vendido da natura`, `preco sabonete
phebo`) e o `nao` solto (`pavio parafinado ou nao`, `velas que nao pingam` —
atributo desejável, não problema). O veto resolvia em tese, mas a substituta
vinha do mesmo conjunto contaminado.

**O que muda (nenhum critério pré-registrado muda):**

1. **Léxico `intent-v3`** (`domain/web_queries.py`): só português (o experimento é
   em PT) e o `nao` deixou de ser marcador isolado — vale apenas junto de um
   **desfecho ruim** (`nao acende`, `nao endurece`, `nao seca`, `nao queima`,
   `nao faz espuma`, ...). Saíram `significado`, `vs`, `derrete` (casava "que não
   derretem", atributo desejável) e todos os marcadores em inglês.
2. **Filtro `filter-v1`** (pré-sorteio, versionado e gravado no YAML do protocolo
   como `filter_version`): descarta frase com palavra funcional ou de conteúdo
   típica de EN/ES (`for`, `the`, `en`, `con`, `soap`, `candle`, ...) e frase com
   **marca industrial de produto acabado** (`natura`, `phebo`, `boticario`,
   `granado`, `dove`, `ngk`, ...). Fornecedor de matéria-prima do artesão **não**
   é bloqueado (ex.: `nossa terra`).

**Efeito medido (mesma seed `20261017`, 16/10, sem escrever no banco):** as 4
classes continuam fechando nos 3 segmentos (`missing` vazio) e as 13 consultas
saem limpas — 0 candidatas a veto em sabonetes e resina; em velas restam 3
resíduos de homonímia (`vela de aniversario`, `velas 7 dias`, `vela não queimou
toda umbanda`). Cobertura elegível por classe e as 13 consultas por segmento em
[Evidência — sondas do Suggest](evidencia-sondas-suggest.md).

!!! note "Margem estreita em sabonetes/resolver"
    Depois do filtro, `resolver` em sabonetes tem 4 frases elegíveis (3 entram no
    `q1`): um veto abriria `missing`. A revisão do usuário decide se vale ampliar
    a sonda antes do congelamento.

## Indicadores (não são critérios até haver calibração)

| Indicador | Cálculo | Referência |
| --- | --- | --- |
| Concordância score × rótulo | Spearman entre o score e `tema + intencao + utilidade`, sobre os `kept` anotados | 0,5 |
| Diversidade | domínios distintos no top 10, com e sem `max_per_domain` | 6 |
| Estabilidade | sobreposição média do top 10 entre 200 reamostragens bootstrap; e entre as duas últimas coletas | 0,7 |
| Utilidade no top 10 | úteis entre os 10 primeiros / 10 (prepara a F6) | — |
| Custo/latência | tempo de `web-rank` por segmento; chamadas externas | < 2 s; 0 chamadas |

!!! note "Concordância não é entre anotadores"
    Um único anotador. A métrica é **"concordância do score com o rótulo
    humano"**. Um segundo anotador em 30 itens comuns daria um kappa de
    Cohen, reportado à parte como teto de qualidade do rótulo.

## Veredito por segmento

| Veredito | Condição |
| --- | --- |
| **aprovado** | os 4 critérios atingidos, com o ganho de IC inferior > 0 |
| **direcional** | metas pontuais atingidas, mas o IC do ganho inclui 0 |
| **insuficiente** | < 40 itens `kept` anotados, ou uma classe de intenção sem dados (amplia-se a amostra antes de concluir) |
| **não atingido** | algum critério abaixo da meta com n suficiente (investigar dado, métrica ou segmento — não necessariamente falha do projeto) |

O valor de máquina é ASCII: `aprovado` / `direcional` / `insuficiente` /
`nao_atingido`.

## Caso extremo: cobertura e o filtro

A cobertura usa o estrato de descartados da amostra. Uma **legítima
descartada** é falso positivo do filtro. Se a cobertura ficar abaixo de 90%,
o filtro `ads-v1` precisa ser revisto (nova versão, `ads-v2`, com a
evidência registrada) antes de qualquer ativação.

!!! warning "Limitação conhecida da cobertura (amostragem estratificada)"
    A cobertura é `legítimas preservadas / (legítimas preservadas + legítimas
    descartadas)` sobre as contagens **anotadas**, mas os estratos têm frações
    de amostragem diferentes (até 40 mantidos vs. ≤ 10 descartados) e o topo
    dos mantidos é enviesado por construção. Logo, a cobertura é um indicador
    **direcional** do falso positivo do filtro, não uma estimativa pontual
    precisa. Só o estrato de descartados mede falso positivo; uma reponderação
    exigiria desmembrar os estratos (quebraria a anotação cega) — decisão
    adiada, registrada aqui.

## Próximos passos

- A coleta em produção roda com `WEB_QUERIES_REQUIRED=1`: segmento sem
  protocolo `q1` válido faz o collect da web **falhar** com
  `missing_protocol` (o legado de 1 consulta **não** roda), em vez de degradar
  em silêncio.
- A ativação no `worker` (F5) só ocorre com veredito **aprovado em ≥ 2 dos 3
  segmentos PT**, sem nenhum "não atingido" por cobertura, e é reversível por
  `WEB_SCORE_ENABLED=0`.
- Uma eventual `web-v2` sobe a versão e repete a F4 com o **mesmo**
  pré-registro.

### Adendo registrado — 2026-10-17: `intent-v4` + `filter-v2` (revisão externa)

**Motivo.** Uma revisão externa de 17/10 mostrou, e o QA reproduziu, que o
`intent-v3` classificava frases de problema e de venda como aprendizagem:
o marcador genérico `como` era avaliado primeiro (`aprender` é a 1ª classe) e
vencia o marcador específico — "como resolver vela que não acende", "como
vender velas" e "como definir preço" saíam como `aprender`; "tendências"
(plural) não casava nada; e o `filter-v1` barrava "tutorial", palavra
corrente em PT.

**Mudança.** `intent-v4`: os marcadores específicos decidem primeiro (na ordem
documentada); o genérico (`como`) só decide quando nada específico casa;
entram plurais (`tendencias`, `novidades`) e problemas (`evitar`, `consertar`,
`corrigir`, `bolha(s)`). `filter-v2`: `tutorial` sai dos marcadores
estrangeiros. Casos ambíguos e negativos viraram testes literais.

**Efeito no experimento.** Nenhum: os protocolos `q1` ainda não tinham sido
gerados nem amostrados — a mudança entra antes de congelar o protocolo, então
não há resultado a reportar pelos dois critérios.

**Regras acrescentadas à análise da F4:**

- **Sondas (induzem `tendencia`/`comercial`).** As frases que vieram das
  sondas `probe-v2` são em parte induzidas pela própria sonda: a cobertura
  dessas classes **não** é evidência de vocabulário espontâneo do segmento, e
  o relatório deve dizer isso ao apresentá-las.
- **Contaminação residual do filtro.** O filtro é heurística determinística,
  não detector de idioma: a anotação manual registra quantas consultas da
  amostra ainda estão fora do nicho ou do idioma, e esse número sai no
  relatório da F4.
