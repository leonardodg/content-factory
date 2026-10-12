# Fase B — Google Trends real (por último, decisão do usuário)

**Goal:** Provider real de `TrendsProvider` (Fase A) via **pytrends**
(biblioteca não-oficial de scraping do Google Trends — sem API oficial
self-serve disponível, mesmo padrão de risco já aceito pelo projeto para
`web.py`/ddgs). Entra por último no roadmap porque é a dependência de
maior risco de quebra (scraping sujeito a mudança do Google sem aviso) —
faz sentido ter o resto do pipeline (C/D/E) validado e estável primeiro.

**Pré-requisito:** Fase A mergeada. Não depende de C/D/E.

### Task B1: Adicionar dependência `pytrends`

- [x] `uv add pytrends`, conferir licença/CVEs conhecidos
  (`uv run pip-audit` já pega isso no gate, mas checar manualmente antes
  de commitar o lockfile).
- [x] Commit: `chore: adiciona dependencia pytrends`

### Task B2: `infra/collectors/trends_google.py`

**Files:**
- Create: `src/content_factory/infra/collectors/trends_google.py`
- Test: `tests/infra/collectors/test_trends_google.py`

```python
class TrendsGoogleProvider:
    """TrendsProvider via pytrends — não-oficial, ver ADR novo sobre risco."""

    def __init__(self, geo: str = "BR") -> None:
        self._geo = geo

    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]:
        # pytrends é síncrono — rodar em thread executor pra não bloquear o
        # event loop (asyncio.to_thread), igual ao padrão já usado em
        # qualquer lib síncrona do projeto (conferir se já existe precedente
        # em web.py/tiktok.py antes de inventar um novo).
        ...
```

Mapear erros de rede/HTTP/rate-limit do pytrends para
`domain/errors.py` (`NetworkError`/`RateLimited`/`UpstreamChanged`) —
mesmo padrão de `instagram.py`/`youtube.py`. Circuit breaker: contador de
falhas consecutivas na própria instância (`self._consecutive_failures`),
desativa o provider por N ciclos após M falhas seguidas (valores exatos
a decidir no plano real desta fase, com teste provando o desligamento
temporário).

TDD: fixture de resposta gravada do pytrends (gravar uma resposta real
uma vez, versionar como fixture — não bater na rede real nos testes),
teste de mapeamento de erro, teste do circuit breaker.

- [x] Steps de TDD completos.
- [x] Commit: `feat(trends): TrendsGoogleProvider via pytrends com circuit breaker`

### Task B3: Registrar no composition root

**Files:**
- Modify: `src/content_factory/infra/collectors/registry.py` (ou onde
  Fase D decidiu montar `ResearchDeps.trends` em `create_app_deps`)

Trocar o provider "noop" temporário (Task D2) pelo `TrendsGoogleProvider`
real agora que existe.

- [x] Commit: `feat(worker): research_job usa TrendsGoogleProvider real`

### Task B4: Gates + docs

- [x] Gates completos (pip-audit cobre a dependência nova).
- [x] ADR novo ou seção em ADR existente documentando o risco "não-oficial"
  do pytrends, igual ao que já existe para TikTok-Api (`ADR-0003`).
- [x] `docs/HANDOFF.md` atualizado, plano copiado, commit final.

> **Nota**: plano de intenção detalhada (não SDD travado como a Fase A) — leia os arquivos reais citados antes de executar e ajuste os detalhes finos; a arquitetura (Protocols, nomes, pontos de integração) é o que está fixado aqui.
