from content_factory.cli.output import envelope_err, envelope_ok, exit_code_for


def test_ok_envelope() -> None:
    e = envelope_ok("collect", inserted=10)
    assert e["ok"] is True and e["command"] == "collect" and e["inserted"] == 10


def test_err_envelope() -> None:
    e = envelope_err("collect", code="blocked", message="tiktok bloqueou")
    assert e["ok"] is False
    assert e["error"] == {"code": "blocked", "message": "tiktok bloqueou"}


def test_exit_codes() -> None:
    assert exit_code_for(ok=True, partial=False) == 0
    assert exit_code_for(ok=True, partial=True) == 2
    assert exit_code_for(ok=False, partial=True) == 1
