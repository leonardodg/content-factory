"""Google Trends via pytrends — scraping não-oficial, ver ADR-0004."""

import asyncio
from datetime import UTC
from typing import Any

from content_factory.domain.errors import CollectorError, NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.models import TrendPoint

PLATFORM = "google_trends"
DEFAULT_BREAKER_THRESHOLD = 5
DEFAULT_BREAKER_COOLDOWN = 3  # ciclos


class TrendsGoogleProvider:
    """TrendsProvider via pytrends — não-oficial, risco documentado em ADR-0004.

    pytrends é síncrono (requests + pandas) — roda em `asyncio.to_thread`
    para não bloquear o event loop (mesmo padrão de `web.py`/ddgs).
    Circuit breaker: M falhas consecutivas → desativa por N ciclos.
    """

    def __init__(
        self,
        geo: str = "BR",
        client: Any | None = None,
        *,
        breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown_cycles: int = DEFAULT_BREAKER_COOLDOWN,
    ) -> None:
        self._geo = geo
        self._client = client  # seam de teste; production cria sob demanda
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown = breaker_cooldown_cycles
        self._consecutive_failures = 0
        self._cooldown_remaining = 0

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from pytrends.request import TrendReq  # type: ignore[reportMissingTypeStubs]

        return TrendReq(hl="pt-BR", tz=0)

    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]:
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            raise CollectorError("circuit_open", PLATFORM, "provider em cooldown")
        if not keywords:
            return []
        try:
            df = await asyncio.to_thread(self._fetch, keywords, timeframe)
        except RateLimited:
            self._register_failure()
            raise
        except NetworkError:
            self._register_failure()
            raise
        except UpstreamChanged:
            self._register_failure()
            raise
        except Exception as exc:
            self._register_failure()
            raise self._classify(exc) from exc
        self._consecutive_failures = 0  # sucesso reseta o breaker
        return self._map(keywords, df)

    def _fetch(self, keywords: list[str], timeframe: str) -> Any:
        client = self._get_client()
        try:
            client.build_payload(kw_list=keywords, timeframe=timeframe, geo=self._geo)
            return client.interest_over_time()
        except Exception as exc:
            raise self._classify(exc) from exc

    def _classify(self, exc: Exception) -> Exception:
        """Mapeia erros do pytrends/requests para erros de domínio."""
        import requests

        if isinstance(exc, requests.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429:
                return RateLimited(PLATFORM, "429 Too Many Requests")
            return UpstreamChanged(PLATFORM, f"HTTP {status}")
        if isinstance(exc, requests.RequestException | ConnectionError | TimeoutError | OSError):
            return NetworkError(PLATFORM, str(exc))
        return UpstreamChanged(PLATFORM, str(exc))

    def _register_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._breaker_threshold:
            self._cooldown_remaining = self._breaker_cooldown
            self._consecutive_failures = 0

    def _map(self, keywords: list[str], df: Any) -> list[TrendPoint]:
        """Converte DataFrame do pytrends em lista de TrendPoint."""
        out: list[TrendPoint] = []
        for kw in keywords:
            if kw not in df.columns:
                continue
            for ts, value in df[kw].items():
                period = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                if period.tzinfo is None:
                    period = period.replace(tzinfo=UTC)
                out.append(
                    TrendPoint(
                        keyword=kw,
                        period_start=period,
                        period_granularity="week",
                        interest=float(value),
                    )
                )
        return out
