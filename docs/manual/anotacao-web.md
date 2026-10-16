# Anotação da amostra web (F2)

Protocolo de anotação **cega** da amostra de páginas web (plano
[Relevância web e potencial](../ai-plans/2026-10-16-web-relevancia-potencial.md),
F2). O
objetivo é medir a qualidade da coleta e a relevância/da utilidade do que a
busca web traz para cada segmento — nunca misturar com o `viral_score` dos
vídeos.

## O que o anotador recebe

`cf web-sample --segment <slug> --out docs/data/web-eval/<slug>.csv` gera um
CSV com **50 linhas por segmento** e as colunas:

| Coluna | Conteúdo |
| --- | --- |
| `item_id` | chave do item (a URL canônica) |
| `canonical_url` | URL normalizada (sem rastreio) |
| `domain` | domínio (host em minúsculas) |
| `title` | título como o provedor devolveu |
| `snippet` | trecho/descrição como o provedor devolveu |
| `query` | consulta exata que trouxe a página |
| `query_intent` | classe da consulta: `aprender` / `resolver` / `tendencia` / `comercial` / `seed` |

A amostra tem **três estratos** (não identificados na planilha):

| Estrato | Quantidade | Para medir |
| --- | --- | --- |
| Topo da linha de base | 15 | precisão do ranking atual |
| Aleatório entre os mantidos | 25 | o que o ranking deixa de fora |
| Descartados pelo filtro | até 10 | falso positivo do filtro (cobertura) |

!!! warning "Anotação cega"
    O CSV **não** traz score, posição da linha de base nem estrato, e a ordem
    das linhas é embaralhada. Não consulte a lista completa de resultados
    antes de terminar: a nota precisa refletir só o que o item mostra.

## Como anotar

1. Abra o CSV na planilha. Mantenha `item_id` intacto.
2. Preencha **`tema`**, **`intencao`** e **`utilidade`** com `0`, `1` ou `2`.
3. Preencha **`invalido`** só quando o item não é uma página legítima.
4. Preencha **`motivo`** (uma frase) quando `tema ≤ 1` ou quando houver
   `invalido`.
5. Salve com o mesmo nome; esse arquivo vai para o commit (é o registro da
   avaliação).

Ritmo esperado: cerca de **1 min por item**, ~2 h 30 min no total. Proposta em
duas sessões (noite de 19/10 e dia 20/10).

### Rótulos (definidos antes de anotar)

| Coluna | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `tema` | não trata do segmento | trata de forma efetiva, mas secundária | o segmento é o assunto central |
| `intencao` | não atende à intenção da consulta | atende em parte | atende claramente |
| `utilidade` | nada aproveitável | dá uma ideia, sem detalhe | material concreto (técnica, erro, dado, exemplo) para conteúdo original |
| `invalido` | — | marcador: `anuncio` / `vazia` / `duplicata` / `inacessivel` | — |
| `motivo` | obrigatório quando `tema ≤ 1` ou quando há `invalido`; uma frase | | |

**Exemplos de `tema` (segmento `velas-artesanais`):**

- `2`: "Como fazer vela de soja em casa, passo a passo" — o assunto central é
  fazer velas artesanais.
- `1`: "Kit de aromaterapia: óleos e velas para relaxar" — fala de velas, mas
  o centro é aromaterapia.
- `0`: "10 ideias de presente personalizado" — só cita vela de passagem.

**Exemplos de `utilidade`:**

- `2`: página com receita, proporção de cera/pavio e o erro comum.
- `1`: "velas artesanais podem virar renda extra" — dá o ângulo, sem detalhe.
- `0`: página de loja listando produtos, sem conteúdo aproveitável.

### Página inválida

Use `invalido` quando o item **não é uma página legítima de conteúdo**:

| `invalido` | Quando |
| --- | --- |
| `anuncio` | clique de anúncio ou redirecionamento publicitário |
| `vazia` | a página não tem conteúdo (só casca, bloqueio, "sem resultados") |
| `duplicata` | mesmo conteúdo de outro item já visto na amostra |
| `inacessivel` | não abriu (erro, tempo esgotado, login/portal exigido) |

Quando o item é inválido, dê `tema = 0` e explique em `motivo`.

## Definições positivas (usadas na avaliação)

Definidas **antes** de anotar, para não escolher o critério depois de ver os
números:

- **relevante** = `tema ≥ 1` **e** sem `invalido`. Página que só menciona o
  tema de passagem é `0`.
- **útil** = `utilidade ≥ 1` **e** sem `invalido`.
- **página legítima** (base da cobertura do filtro) = sem `invalido`.

## Versionamento e privacidade

- O CSV anotado fica em `docs/data/web-eval/<slug>.csv`, com `item_id`, os
  rótulos e a seed. URL e título são relidos do banco na avaliação.
- O repositório é público: não escreva dado pessoal em `motivo`. O teste de
  privacidade (`scripts/check_privacy.py`) cobre a pasta.

## Anotadores

Um só anotador (o usuário). Por isso a métrica correspondente se chama
**"concordância do score com o rótulo humano"**, e nunca concordância entre
anotadores. Opcional: um segundo anotador em 30 itens comuns daria um
**kappa de Cohen**, reportado à parte como teto de qualidade do rótulo.

## Depois de anotar

`cf web-eval --segment <slug> --csv docs/data/web-eval/<slug>.csv` produz as
métricas e o veredito, pelos critérios pré-registrados em
[Validação web](validacao-web.md).
