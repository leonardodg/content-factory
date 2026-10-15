import ipaddress
import socket
from urllib.parse import urlparse

from content_factory.domain.errors import DomainError

# CGNAT (RFC 6598): faixa compartilhada não-roteável publicamente que
# `ip.is_private` NÃO cobre no Python — bloqueio explícito
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def assert_public_http(url: str) -> None:
    """Valida `url` antes de qualquer fetch: scheme http(s), DNS e IP público.

    Bloqueia IP privado/loopback/link-local (incl. metadata 169.254.169.254)/
    multicast/reserved/unspecified/CGNAT. Levanta `DomainError` — nunca rede.

    Limite conhecido: o httpx resolve o DNS de novo ao conectar (TOCTOU /
    DNS rebinding). Aceito enquanto o ingest for CLI local com URL do
    operador — ver ADR-0008 antes de expor o ingest a automação ou rede.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DomainError(f"scheme/host inválido: {url}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DomainError(f"porta inválida: {url}") from exc
    try:
        infos = socket.getaddrinfo(parsed.hostname, port or 443)
    except socket.gaierror as exc:
        raise DomainError(f"DNS falhou para {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or ip in _CGNAT
        ):
            raise DomainError(f"IP não-público bloqueado: {ip} ({url})")
