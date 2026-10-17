import asyncio
import hashlib
import logging
from typing import Any, ClassVar, Protocol

# classe real: o re-export `ddgs.DDGS` é proxy lazy — parcialmente Unknown p/ pyright
from ddgs.ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from content_factory.domain.errors import (
    CollectorError,
    NetworkError,
    RateLimited,
    UpstreamChanged,
)
from content_factory.domain.models import CandidateVideo, SegmentConfig
from content_factory.domain.web_queries import PlannedQuery
from content_factory.domain.web_urls import AD_RULES_VERSION, ad_signal, canonicalize

logger = logging.getLogger(__name__)

# sobe quando o coletor muda de comportamento (o hit grava a versão)
COLLECTOR_VERSION = "web-c2"


class WebQuerySource(Protocol):
    """Fonte de consultas prontas por segmento (o protocolo `q1`).

    Devolve as consultas do run atual, ou `None` quando não há protocolo —
    aí o coletor cai no legado de 1 consulta (`" OR ".join(keywords[:3])`).
    """

    def queries_for(self, segment: SegmentConfig) -> list[PlannedQuery] | None: ...


_LEGACY_INTENT = "seed"
_LEGACY_QUERY_ID = "legacy:seed:00"


class WebCollector:
    """Busca web (`ddgs`) → candidatos platform="web" com proveniência no raw.

    Cada resultado (mantido OU descartado por anúncio/duplicata) vira um
    `CandidateVideo`; o `raw` carrega a proveniência. A partição em
    mantidos x hits acontece em `domain.web_hits` (o coletor não conhece o
    banco). Sem fonte de consultas, usa o legado (1 consulta) — salvo em modo
    `required` (`WEB_QUERIES_REQUIRED=1`), em que a ausência de protocolo
    levanta `CollectorError` em vez de cair no legado.
    """

    platform: ClassVar[str] = "web"

    def __init__(self, queries: WebQuerySource | None = None, *, required: bool = False) -> None:
        self._queries = queries
        self._required = required

    def _planned(self, segment: SegmentConfig) -> list[PlannedQuery]:
        if self._queries is not None:
            planned = self._queries.queries_for(segment)
            if planned:
                return planned
            if self._required:
                # Falha explícita (WEB_QUERIES_REQUIRED=1): melhor o collect
                # `failed` do que rodar em silêncio um experimento diferente do
                # aprovado. O legado NÃO roda neste modo.
                raise CollectorError(
                    "missing_protocol",
                    self.platform,
                    f"segmento {segment.slug} sem protocolo q1 válido",
                )
        return [
            PlannedQuery(
                query_id=_LEGACY_QUERY_ID,
                query=" OR ".join(segment.keywords[:3]),
                intent=_LEGACY_INTENT,
            )
        ]

    async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
        planned = self._planned(segment)
        # DDGS é síncrono (rede) — roda em thread; o lambda lê o global DDGS a
        # cada chamada, que é o que monkeypatch.setattr(web_mod, "DDGS", ...) troca.
        raw = await asyncio.to_thread(self._search_all, planned, limit)
        return self._build(raw)

    def _search_all(
        self, planned: list[PlannedQuery], limit: int
    ) -> list[tuple[PlannedQuery, int, dict[str, Any]]]:
        """Consulta cada query e devolve (query, posição 1-based, resultado).

        Erros esperados viram o contrato de `CollectorError` (`_classify`);
        "No results found" é busca vazia (segue para a próxima consulta).
        """
        ddgs = DDGS()
        collected: list[tuple[PlannedQuery, int, dict[str, Any]]] = []
        for pq in planned:
            try:
                results = ddgs.text(pq.query, max_results=limit)
            except DDGSException as exc:
                classified = _classify(self.platform, exc)
                if classified is None:
                    # auditoria externa 4.3: o ddgs devolve None para status !=
                    # 200 sem exceção (base.py) — vazio legítimo e engine
                    # bloqueada (403/429) são indistinguíveis; não inventar.
                    logger.warning(
                        "web: busca vazia (ambígua: sem resultado ou engines "
                        "bloqueadas) — %s query_id=%s",
                        pq.query,
                        pq.query_id,
                    )
                    continue
                raise classified from exc
            for position, result in enumerate(results, start=1):
                collected.append((pq, position, dict(result)))
        return collected

    def _build(self, raw: list[tuple[PlannedQuery, int, dict[str, Any]]]) -> list[CandidateVideo]:
        seen_canonical: set[str] = set()
        out: list[CandidateVideo] = []
        for pq, position, result in raw:
            href = str(result.get("href", ""))
            title = str(result.get("title", ""))
            body = str(result.get("body", ""))
            canonical = canonicalize(href)
            reason = ad_signal(href, title, body)
            if reason is not None:
                status, discard_reason = "discarded_ad", reason
            elif canonical.ok and canonical.url in seen_canonical:
                status, discard_reason = "duplicate", "duplicate:same_run"
            else:
                status, discard_reason = "kept", None
                if canonical.ok:
                    seen_canonical.add(canonical.url)
            identity = canonical.url if canonical.ok else href
            out.append(
                CandidateVideo(
                    platform=self.platform,
                    platform_video_id=hashlib.sha1(
                        identity.encode(), usedforsecurity=False
                    ).hexdigest(),
                    url=href,
                    creator_handle="",
                    title=title,
                    description=body,
                    raw={
                        "web_query": pq.query,
                        "query_id": pq.query_id,
                        "query_intent": pq.intent,
                        "provider": "unknown",
                        "provider_result_position": position,
                        "url_original": href,
                        "canonical_url": canonical.url,
                        "canonical_ok": canonical.ok,
                        "web_status": status,
                        "discard_reason": discard_reason,
                        "collector_version": COLLECTOR_VERSION,
                        "rules_version": AD_RULES_VERSION,
                    },
                )
            )
        return out


def _classify(platform: str, exc: DDGSException) -> CollectorError | None:
    """`DDGSException` → contrato; `None` = busca vazia.

    `DDGS.text` agrega as engines e levanta `DDGSException(err)` com o erro
    da ÚLTIMA engine como argumento (só "timed out" vira `TimeoutException`),
    então o tipo real pode estar em `exc.args[0]` — e um erro de cliente
    HTTP (429) chega só como texto.
    """
    inner = exc.args[0] if exc.args and isinstance(exc.args[0], BaseException) else exc
    text = str(exc).lower()
    if isinstance(inner, RatelimitException) or "ratelimit" in text or "429" in text:
        return RateLimited(platform, str(exc))
    if isinstance(inner, TimeoutException) or "timed out" in text or "timeout" in text:
        return NetworkError(platform, str(exc))
    if "no results found" in text:
        return None
    return UpstreamChanged(platform, str(exc))
