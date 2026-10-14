import io
import logging

import pytest

from content_factory.infra.redact import RedactingFilter, redact


def test_redact_masks_tokens() -> None:
    text = (
        "GET /reels Cookie: sessionid=SESS10N; path=/ | "
        "Set-Cookie: ms_token=MS888888; HttpOnly | "
        'auth access_token=TOK3NB3R | json {"access_token": "OUTR4B3R"}'
    )
    clean = redact(text)
    for secret in ("SESS10N", "MS888888", "TOK3NB3R", "OUTR4B3R"):
        assert secret not in clean
    assert clean.count("***") == 4


def test_redact_leaves_plain_text_untouched() -> None:
    msg = "coleta candles: 10 videos encontrados em youtube"
    assert redact(msg) == msg


def _logger_with_filter(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_redacting_filter_masks_message_args_and_traceback() -> None:
    """Etapa 0 Task 8.2: o log do worker não pode vazar segredo (spec §7)."""
    logger, stream = _logger_with_filter("tests.redact.filter")
    try:
        raise RuntimeError("upstream GET ?ms_token=MS7777777")
    except RuntimeError:
        logger.exception("falhou com sessionid=%s", "SESS10N")
    out = stream.getvalue()
    assert "MS7777777" not in out and "SESS10N" not in out
    assert "ERROR falhou com sessionid=***" in out
    assert "Traceback" in out and "ms_token=***" in out


def test_redacting_filter_never_drops_records() -> None:
    logger, stream = _logger_with_filter("tests.redact.plain")
    logger.info("coleta %s ok", "candles")
    assert stream.getvalue() == "INFO coleta candles ok\n"


def test_redacting_filter_never_raises_on_bad_format_args() -> None:
    """Review: getMessage() com args errados não pode subir para o app."""
    logger, stream = _logger_with_filter("tests.redact.badargs")
    logger.info("dois %s %s", "um-só")  # não levanta
    assert "dois %s %s" in stream.getvalue()


# --- revisão 2026-10-14 A2 (R3): formatos reais de segredo ---


def _httpx_status_error_message(url: str) -> str:
    """Mensagem REAL do httpx para um 403 — embute a URL com a query."""
    import httpx

    request = httpx.Request("GET", url)
    response = httpx.Response(403, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return str(exc)
    raise AssertionError("raise_for_status não levantou")


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        (
            _httpx_status_error_message(
                "https://www.googleapis.com/youtube/v3/search?q=vela&key=AIzaSyFAKE0123456789"
            ),
            "AIzaSyFAKE0123456789",
        ),
        (
            _httpx_status_error_message(
                "https://graph.facebook.com/v21.0/x?access_token=EAAFAKE99"
            ),
            "EAAFAKE99",
        ),
        (
            "connect failed: postgresql+psycopg://cf:S3nh4Forte@postgres/contentfactory",
            "S3nh4Forte",
        ),
        ("Error connecting to redis://:R3disPass@redis:6379/0", "R3disPass"),
        ("POST /token body: client_secret=SuperSecret123&grant_type=x", "SuperSecret123"),
        ("api_key=K3yValue123 password=P4ssValue", "K3yValue123"),
        ("api_key=K3yValue123 password=P4ssValue", "P4ssValue"),
    ],
    ids=[
        "youtube-key",
        "instagram-token",
        "postgres-dsn",
        "redis-url",
        "client-secret",
        "api-key",
        "password",
    ],
)
def test_redact_masks_real_secret_formats(message: str, secret: str) -> None:
    assert secret in message  # sanidade: o formato real carrega o segredo
    assert secret not in redact(message)


def test_redact_keeps_url_shape_readable() -> None:
    out = redact("postgresql+psycopg://cf:S3nh4@postgres/db?q=vela&key=AIza123")
    assert out == "postgresql+psycopg://cf:***@postgres/db?q=vela&key=***"


async def test_youtube_network_error_message_is_redacted() -> None:
    """Paridade com o Instagram: o erro de transporte que embute a URL não vaza a key."""
    import httpx

    from content_factory.domain.errors import NetworkError
    from content_factory.infra.collectors.youtube import YoutubeCollector

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"falhou {request.url}", request=request)

    collector = YoutubeCollector(api_key="AIzaSySECRET", transport=httpx.MockTransport(_boom))
    with pytest.raises(NetworkError) as exc_info:
        await collector._get("search", {"q": "vela"})  # pyright: ignore[reportPrivateUsage]
    assert "AIzaSySECRET" not in str(exc_info.value)
