# Pré-registro — descoberta de conteúdo viral por tema (teste de 4 dias)

Este documento é commitado **antes** do primeiro dado do teste. Ele trava a
pergunta, as versões, a divisão de buscas e a regra de decisão. Qualquer
mudança depois do início do teste entra como **adendo datado**, no fim deste
arquivo, e não reescreve o que está acima.

Especificação: `docs/ai-plans/2026-10-16-especificacao-viral-por-tema.md`.
Planos: `docs/ai-plans/2026-10-16-viral-por-tema-bloco-*.md`.

## Pergunta

Os termos vindos da web (Suggest, Trends e hashtags) trazem conteúdo com mais
engajamento do que as keywords fixas do segmento? Quais temas e quais vídeos
lideram em cada segmento?

## Plataforma da comparação

YouTube: é a única plataforma com busca por termo hoje. Instagram (mídia
própria) e TikTok (sem acesso; a API oficial não aceita empresa) ficam fora da
comparação. As descobertas deles, se houver, são gravadas como `unattributed`.

## Segmentos

`velas-artesanais`, `sabonetes-artesanais` e `resina-artesanal`, com as
keywords, âncoras, bloqueios e `accepted_language: pt-BR` dos YAMLs em
`segments/` no commit deste documento.

## Versões congeladas

| Peça | Versão | Parâmetros |
|---|---|---|
| Filtro de termos | `term-filter-v1` | idioma só por evidência positiva (listas EN/ES); âncora por palavra inteira; bloqueio por segmento; hashtag por substring |
| Ranking do Suggest | `kw-rank-v1` | posição 0,5; recorrência 0,3 (teto 3 sementes); persistência 0,2 (7 dias) |
| Plano de busca | `search-plan-v1` | 4 vagas por coleta (abaixo) |
| Trends | `trends-anchor-v1` | `today 3-m` (diário); lotes de 4 + âncora; razão com teto 5; 7 dias contra os 21 anteriores; score 0,7 interesse + 0,3 inclinação |
| Score de viralização | `viral-v2` | velocidade 0,35; engajamento 0,25; relativo ao canal 0,25; alcance 0,15; percentis por plataforma e segmento; janela de 30 dias; população mínima 5 |
| Ranking de temas | `term-rank-v1` | top 10% 0,4; mediana de velocidade 0,3; mediana de engajamento 0,3; bootstrap com seed 20261016 e 1.000 reamostragens |
| Hashtags | `hashtag-mine-v1` | top 25% do `viral-v2`; top 10 por plataforma |
| Idioma | `pt-BR` | `pt-PT` sai; `pt` sem região e sem idioma declarado ficam |

## Divisão fixa de buscas (R5)

YouTube: 4 buscas por coleta e por segmento, em 4 coletas por dia (02h, 08h,
14h e 20h, horário de Brasília). Uma vaga por origem:

1. `seed`: rodízio das keywords do segmento (ordem alfabética, estável);
2. `suggest`: pool do Suggest por `kw_rank`, em rodízio;
3. `trends`: o mesmo pool por `trend_score`, em rodízio, sem repetir o termo da
   vaga `suggest`;
4. `hashtag:youtube`: hashtags mineradas por `engagement_score`, buscadas como
   `#tag`.

Vaga sem candidato vai para `suggest` e é gravada como `suggest`: a origem é
sempre quem escolheu o termo. O rodízio é determinístico por (dia UTC, slot).
Não há realocação adaptativa durante o teste.

A vaga `hashtag` é **exploratória**: ela só tem candidatos depois que a Fase B
(`viral-v2` e mineração de hashtags) entra no ar. A data em que isso acontece
é anotada em adendo. Até lá, ela cai em `suggest`.

## Regra de decisão por origem (YouTube, por segmento)

- Unidade: **vídeo único** por origem. O mesmo vídeo trazido pela mesma origem
  em várias coletas conta uma vez; um vídeo trazido por duas origens conta nas
  duas, e a sobreposição é reportada.
- Comparação: origem X contra `seed`, pela diferença das medianas do `viral-v2`
  no corte, com IC 95% por bootstrap (seed 20261016, 1.000 reamostragens).
- Veredito:
  - menos de **30** vídeos únicos em X ou em `seed` → **insuficiente**;
  - limite inferior do IC acima de 0 → **melhor**;
  - limite superior do IC abaixo de 0 → **pior**;
  - IC cruzando 0 → **direcional**.

## Top temas e top vídeos

- Tema: `engagement_score` do termo, só com **5 ou mais** vídeos únicos. Termo
  com menos de 5 sai como "insuficiente", nunca como vencedor.
- Vídeos: os 20 maiores `viral-v2` por segmento (YouTube), no corte.

## Corte e checkpoints

- **Início:** o deploy da Fase A. A data e a hora entram em adendo no próprio
  deploy.
- **Corte:** início + 96 h. O relatório final roda com `--as-of` igual ao
  corte, e é reprodutível.
- **D+0:** fumaça (research, collect e score); a primeira coleta com
  atribuição; a cota usada.
- **D+1:** filtro (amostra cega de 13 termos por segmento e varredura completa
  do pool); remedição; primeiro `cf term-report` (se a Fase B estiver no ar).
- **D+2:** velocidade não nula; hashtags no pool (se a Fase B estiver no ar).
- **D+4:** corte, backup e relatório final.

## Riscos conhecidos antes do início

- **Rendimento das buscas longas:** no teste real de 17/10 (velas), 2 das 4
  buscas (`cera de soja para vela` e `essência para velas lipossolúvel`)
  voltaram sem resultado. A busca usa só vídeos publicados nos últimos 7 dias
  e aceita apenas pt-BR. Origens com rendimento baixo podem não chegar a 30
  vídeos únicos e sair como "insuficiente". Isso é resultado do teste, não
  falha.
- **Volume pt-BR:** restringir a pt-BR tirou cerca de 45% dos vídeos de velas
  (eram `pt-PT`).

## Envs do teste

```text
COLLECT_CRON_HOURS=02,08,14,20
SCORE_CRON_HOURS=02,08,14,20
COLLECT_LIMIT=25
SEARCHES_PER_COLLECT_YOUTUBE=4
TRENDS_TERMS_PER_DAY=16
RESEARCH_SOCIAL_PLATFORMS=none
REMEDIATION_WINDOW_DAYS=7
REMEDIATION_LIMIT=500
REMEDIATION_MIN_GAP_HOURS=3
VIRAL_V2_WINDOW_DAYS=30
VIRAL_V2_MIN_POPULATION=5
```

## Adendos

(nenhum até o início do teste)
