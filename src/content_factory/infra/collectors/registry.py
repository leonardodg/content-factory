import logging

from content_factory.app.ports import Collector
from content_factory.domain.errors import DomainError

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Collector] = {}


def register(collector: Collector) -> None:
    if collector.platform in _REGISTRY:
        logger.warning(
            "collector para a plataforma %r ja registrado — sobrescrevendo",
            collector.platform,
        )
    _REGISTRY[collector.platform] = collector


def get(platform: str) -> Collector:
    try:
        return _REGISTRY[platform]
    except KeyError:
        raise DomainError(f"plataforma desconhecida: {platform}") from None


def resolve(platforms: list[str]) -> list[Collector]:
    return [get(platform) for platform in platforms]


def snapshot() -> dict[str, Collector]:
    """Cópia do registry — suporte a teste (registry é global no processo)."""
    return dict(_REGISTRY)


def restore(state: dict[str, Collector]) -> None:
    """Substitui o conteúdo do registry pelo de `snapshot()` (suporte a teste)."""
    _REGISTRY.clear()
    _REGISTRY.update(state)
