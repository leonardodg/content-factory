# ADR-0005 — Google Trends e Suggest via scraping não-oficial (pytrends + httpx)

**Situação:** Aceita · **Data:** 2026-10-12

## Contexto

O pipeline de pesquisa precisa de sinais de interesse ao longo do tempo
(`TrendsProvider`, Fase A). Não existe API oficial de Google Trends
self-serve — o Trends oferece só: (1) exportação manual via interface web,
(2) BigQuery (requer projeto GCP faturado), (3) Data API do Google (não
expõe trends históricos). A lib `pytrends` (Apache 2.0, ativa) faz
scraping da endpoint interna do Trends via `requests` + `pandas`.

## Decisão

Usar **`TrendsGoogleProvider`** atrás do Protocol `TrendsProvider`:

- `asyncio.to_thread` p/ não bloquear o event loop (mesmo padrão de
  `web.py`/ddgs — precedente estabelecido)
- Mapeamento de erros: `requests.HTTPError` 429 → `RateLimited`;
  `requests.RequestException`/`ConnectionError`/`OSError` → `NetworkError`;
  resto → `UpstreamChanged`
- **Circuit breaker**: 5 falhas consecutivas → desativa por 3 ciclos
  (curto-circuito `circuit_open` sem bater na rede); sucesso reseta
- Provider é **descartável** — Strategy atrás do Protocol; se o Google
  mudar a endpoint, trocar a implementação sem tocar no domínio

### WebSuggestProvider (KeywordExpander)

Endpoint pública `suggestqueries.google.com/complete/search?client=firefox&q=...`
devolve JSON: `["query", ["sug1", "sug2", ...]]`. Implementação:

- `httpx` + `asyncio.to_thread` (mesmo padrão do TrendsProvider e web.py)
- Mapeamento de erros idêntico: 429 → `RateLimited`, rede → `NetworkError`,
  resto → `UpstreamChanged`
- **Circuit breaker** idêntico: 5 falhas → cooldown 3 ciclos
- Seam de teste: `transport` parametrizável (MockTransport-style)

## Riscos aceitos

| Risco | Mitigação |
|---|---|
| Google muda a endpoint sem aviso | Circuit breaker + `UpstreamChanged` classificado; job não aborta o ciclo (só `trends_found=0`) |
| Rate-limit por IP | Circuit breaker + `RateLimited` mapeado; cooldown respeita o limite |
| Scraping viola ToS do Google | Risco aceito e documentado; alternativa (BigQuery) é custo fixo mensal |
| pandas/numpy como transitivas | Já são dependências comuns; pip-audit cobre CVEs |

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| BigQuery + Trends API | Requer projeto GCP faturado + service account; custo fixo antes de validar o valor do sinal |
| Exportação manual CSV | Sem automação; inviável para ciclo diário |
| API terceira paga (Apify, etc.) | Lock-in e custo; mesmo padrão de risco de scraping |
| Não ter trends | Perde o sinal de crescimento/queda — componente-chave do guard-rail de profundidade |

## Referências

- `pytrends` 4.9.2 (Apache 2.0): https://github.com/GeneralMills/pytrends
- Protocol: `app/ports.py::TrendsProvider` e `app/ports.py::KeywordExpander`
- Implementação: `infra/collectors/trends_google.py`, `infra/collectors/web_suggest.py`
- Testes: `tests/infra/collectors/test_trends_google.py`, `tests/infra/collectors/test_web_suggest.py`
