class DomainError(Exception):
    """Erro de negócio do domínio."""


class SegmentNotFound(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"segmento não encontrado: {slug}")
        self.slug = slug


class CollectorError(DomainError):
    """Erro classificado de coleta (gravado em collect_runs.error_code)."""

    def __init__(self, code: str, platform: str, message: str = "") -> None:
        super().__init__(message or f"{platform}:{code}")
        self.code = code
        self.platform = platform


class AuthExpired(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("auth_expired", platform, message)


class RateLimited(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("rate_limited", platform, message)


class UpstreamChanged(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("upstream_changed", platform, message)


class InvalidPayload(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("invalid_payload", platform, message)


class NetworkError(CollectorError):
    def __init__(self, platform: str, message: str = "") -> None:
        super().__init__("network", platform, message)
