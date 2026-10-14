"""Google Trends via pytrends — scraping não-oficial, ver ADR-0004."""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC
from typing import Any

from content_factory.domain.errors import NetworkError, RateLimited, UpstreamChanged
from content_factory.domain.models import TrendPoint
from content_factory.infra.collectors.breaker import CooldownBreaker

PLATFORM = "google_trends"
DEFAULT_BREAKER_THRESHOLD = 5
# 3 ciclos do research (cron diário 07:00) = 72 h: o que "3 ciclos" valia
# com 1 worker por segmento; agora por tempo (Etapa 1 Task 10)
DEFAULT_BREAKER_COOLDOWN_S = 3 * 24 * 3600


class TrendsGoogleProvider:
    """TrendsProvider via pytrends — não-oficial, risco documentado em ADR-0004.

    pytrends é síncrono (requests + pandas) — roda em `asyncio.to_thread`
    para não bloquear o event loop (mesmo padrão de `web.py`/ddgs).
    Circuit breaker: M falhas consecutivas → desativa por `breaker_cooldown_s`
    (por tempo, `CooldownBreaker`).
    """

    def __init__(
        self,
        geo: str = "BR",
        client: Any | None = None,
        *,
        breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
        breaker_cooldown_s: float = DEFAULT_BREAKER_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._geo = geo
        self._client = client  # seam de teste; production cria sob demanda
        self._breaker = CooldownBreaker(
            PLATFORM, threshold=breaker_threshold, cooldown_s=breaker_cooldown_s, clock=clock
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from pytrends.request import TrendReq  # type: ignore[reportMissingTypeStubs]

        return TrendReq(hl="pt-BR", tz=0)

    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]:
        self._breaker.check()
        if not keywords:
            return []
        try:
            df = await asyncio.to_thread(self._fetch, keywords, timeframe)
        except RateLimited:
            self._breaker.record_failure()
            raise
        except NetworkError:
            self._breaker.record_failure()
            raise
        except UpstreamChanged:
            self._breaker.record_failure()
            raise
        except Exception as exc:
            self._breaker.record_failure()
            raise self._classify(exc) from exc
        self._breaker.record_success()
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
