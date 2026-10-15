# Busca do YouTube com idioma/região configuráveis (2026-10-15)

## Contexto

Avaliação dos dados de 13–14/10: o topo do ranking de `resina-artesanal` e
`velas-artesanais` tinha vídeos fora do nicho e em inglês/espanhol (ex.:
"He used molten aluminum instead of epoxy resin"). A busca não passa
`relevanceLanguage` nem `regionCode`. Pedido do usuário: restringir ao
Brasil **de forma configurável**.

`velas-artesanais` é bilíngue de propósito (`languages: [pt-BR, en]`,
subnicho `idioma_en`), então a configuração é **por segmento**, com padrão
global opcional.

Nota honesta: na API, `relevanceLanguage` **favorece** resultados no idioma
e `regionCode` restringe a vídeos visíveis no país — nenhum dos dois é
filtro rígido de idioma. O efeito é medido no B3 (proporção de vídeos fora
do idioma antes × depois).

## Tasks (TDD)

- **T1** — domínio: `SearchLocale(relevance_language, region_code)` e
  `SegmentConfig.search_locale` opcional (YAML → `segments.config` JSONB,
  sem migração).
- **T2** — `YoutubeCollector(default_locale=...)` passa os parâmetros na
  busca; `discover` usa o locale do segmento, `discover_by_keywords` aceita
  `locale`; padrão global de `Settings` (`YOUTUBE_RELEVANCE_LANGUAGE`,
  `YOUTUBE_REGION_CODE`, vazio = sem restrição).
- **T3** — composição por segmento amarra o locale do segmento ao YouTube
  do research (adaptador; protocolo `SocialDiscoverer` intacto).
- **T4** — YAMLs dos 3 segmentos PT: `relevance_language: pt`,
  `region_code: BR`; `candles` sem restrição; `cf segment load` em produção.
