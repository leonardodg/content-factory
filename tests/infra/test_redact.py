import io
import logging

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
