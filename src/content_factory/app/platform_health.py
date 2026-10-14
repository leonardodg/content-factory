"""Regra de saúde por plataforma (plano 2026-10-14-content-factory-lacunas-mapa-do-ciclo).

Só o erro do `discover` conta, e só os códigos em que a PLATAFORMA está
falhando. `auth_expired` (config — nem sai requisição), `invalid_payload`
(dado ruim) e `internal_error` (bug nosso) não abrem o breaker. Erro no
`acquire` também não: janela cheia é o nosso limiter e `blocked` ali é o
próprio breaker já aberto.
"""

import logging

from content_factory.app.ports import PlatformHealth

logger = logging.getLogger(__name__)

PLATFORM_FAILURE_CODES = frozenset({"blocked", "rate_limited", "network", "upstream_changed"})


class NoPlatformHealth:
    """Null object: sem breaker (CLI/testes que não montam o Redis)."""

    async def record_failure(self, platform: str) -> None:
        return None

    async def record_success(self, platform: str) -> None:
        return None


async def report_discover(health: PlatformHealth, platform: str, code: str | None) -> None:
    """`code=None` = discover ok (zera); código de plataforma = falha; resto = ignora.

    Nunca levanta: registrar saúde é acessório — se o Redis cair aqui, a
    exceção escaparia de `_discover_one` e cancelaria as plataformas irmãs.
    """
    try:
        if code is None:
            await health.record_success(platform)
        elif code in PLATFORM_FAILURE_CODES:
            await health.record_failure(platform)
    except Exception:
        logger.warning("falha ao registrar saúde de %s", platform, exc_info=True)
