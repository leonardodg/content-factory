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
| Léxico de intenção | `intent-v1` | `domain/web_queries.py` |
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

- A ativação no `worker` (F5) só ocorre com veredito **aprovado em ≥ 2 dos 3
  segmentos PT**, sem nenhum "não atingido" por cobertura, e é reversível por
  `WEB_SCORE_ENABLED=0`.
- Uma eventual `web-v2` sobe a versão e repete a F4 com o **mesmo**
  pré-registro.
