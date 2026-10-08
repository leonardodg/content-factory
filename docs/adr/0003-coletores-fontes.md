# ADR-0003 — Coletores: APIs oficiais + TikTok-Api atrás de Protocol

**Situação:** Aceita · **Data:** 2026-10-07

## Contexto

O sistema precisa descobrir conteúdo viral em 4 fontes. Não existe API
oficial de descoberta no TikTok; scraping agressivo do Instagram viola o
princípio adotado de usar APIs oficiais; a lib `davidteather/TikTok-Api`
(v6, MIT, ativa) usa a API privada via Playwright e exige cookies
(`ms_token`), com risco conhecido de anti-bot (`EmptyResponseException`).

## Decisão

Vamos usar **4 coletores atrás do Protocol `Collector`**:

- **YouTube:** Data API v3 oficial (chave no `.env`)
- **Instagram:** Graph API oficial (hashtag top/recent; só API oficial)
- **TikTok:** `TikTok-Api` v6 (unofficial) com cookies via env, rate-limit,
  backoff e circuit breaker — implementação **descartável** por ser uma
  Strategy atrás do Protocol
- **Web:** DuckDuckGo (sem chave) + enriquecimento de URLs com guarda SSRF

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Scraping do Creative Center (Playwright próprio) | Mais frágil que a lib (que já resolve assinaturas/fingerprints) e mantém código próprio de anti-bot |
| API terceira paga (Apify/RapidAPI) desde o dia 1 | Custo e lock-in antes de validar o resto do pipeline (fica como plano B via novo backend do mesmo Protocol) |
| Scraping do Instagram | Fora do princípio de APIs oficiais; risco de bloqueio |
| Só URLs via agente (sem descoberta automática TikTok) | Perde o modo automático; mantido também — `cf ingest` aceita URLs de qualquer fonte |

## Consequências

- **Fica mais fácil:** trocar TikTok-Api por Apify = 1 classe nova; fronteira
  única de retry/rate-limit.
- **Fica mais difícil:** TikTok pode quebrar sem aviso (fica `blocked` com
  erro classificado, não derruba o run); cookies expiram (rotação em
  `docs/dev/`); imagem do worker leva Chromium.

## Como saber que erramos

TikTok-Api falhar de forma estruturada por >7 dias (circuit breaker sempre
dispara) → implementar backend Apify do mesmo Protocol (novo ADR se a
decisão de origem mudar).
