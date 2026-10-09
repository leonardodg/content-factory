import pytest

from content_factory.domain.errors import DomainError
from content_factory.infra.ssrf import assert_public_http


def test_rejects_loopback_ip() -> None:
    with pytest.raises(DomainError):
        assert_public_http("http://127.0.0.1:8080/x")


def test_rejects_link_local_metadata() -> None:
    with pytest.raises(DomainError):
        assert_public_http("http://169.254.169.254/latest/meta-data/")


def test_rejects_non_http_schemes() -> None:
    for url in ("file:///etc/passwd", "ftp://example.com/a", "gopher://x"):
        with pytest.raises(DomainError):
            assert_public_http(url)


def test_rejects_private_ip_behind_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str, port: int, *a: object, **kw: object
    ) -> list[tuple[int, int, int, int, tuple[str, int]]]:
        return [(2, 1, 6, 1, ("10.0.0.5", port))]

    import socket

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DomainError):
        assert_public_http("https://interno.example.com/x")


def test_allows_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str, port: int, *a: object, **kw: object
    ) -> list[tuple[int, int, int, int, tuple[str, int]]]:
        return [(2, 1, 6, 1, ("93.184.216.34", port))]

    import socket

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert_public_http("https://example.com/x")  # não levanta


def test_rejects_cgnat_ip() -> None:
    """RFC 6598: 100.64/10 não é is_private no Python — precisa de guarda explícita."""
    with pytest.raises(DomainError):
        assert_public_http("http://100.64.10.7/x")


def test_rejects_ipv6_loopback() -> None:
    with pytest.raises(DomainError):
        assert_public_http("http://[::1]:8080/x")


def test_rejects_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def boom(
        host: str, port: int, *a: object, **kw: object
    ) -> list[tuple[int, int, int, int, tuple[str, int]]]:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(DomainError):
        assert_public_http("https://nao-existe.invalid/x")


def test_rejects_invalid_port() -> None:
    """Porta malformada é URL inválida (ValueError do urlparse vira DomainError)."""
    with pytest.raises(DomainError):
        assert_public_http("http://example.com:abc/")
