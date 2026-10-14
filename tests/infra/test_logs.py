"""Handler de log redigido compartilhado por worker e CLI (review do PR de concorrência)."""

import logging

import pytest

from content_factory.infra import logs
from content_factory.infra.redact import RedactingFilter


def test_configure_logging_is_idempotent_with_redacting_filter() -> None:
    logs.configure_logging(logging.WARNING)
    logs.configure_logging(logging.WARNING)
    logger = logging.getLogger("content_factory")
    ours = [h for h in logger.handlers if h.get_name() == logs.LOG_HANDLER_NAME]
    assert len(ours) == 1
    handler = ours[0]
    assert any(isinstance(f, RedactingFilter) for f in handler.filters)
    assert handler.formatter is not None and handler.formatter._fmt is not None
    assert "%(levelname)s" in handler.formatter._fmt and "%(asctime)s" in handler.formatter._fmt
    assert logger.level == logging.WARNING


def test_configure_logging_default_level_is_info() -> None:
    """Worker: INFO (o `startup` chama sem argumento)."""
    logs.configure_logging()
    assert logging.getLogger("content_factory").level == logging.INFO


def test_cli_main_installs_redacted_logging_at_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CLI também roda o `logger.exception` do app: sem handler, o traceback
    sairia em claro pelo lastResort (spec §7). WARNING: INFO não polui o stderr."""
    import content_factory.cli.main as cli

    calls: list[int] = []
    monkeypatch.setattr(cli, "configure_logging", calls.append)
    monkeypatch.setattr("sys.argv", ["cf", "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    assert calls == [logging.WARNING]
