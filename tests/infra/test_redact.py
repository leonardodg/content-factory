from content_factory.infra.redact import redact


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
