"""Redação de logs (spec §7): segredos nunca saem em claro."""

import logging
import re

# valor depois do padrão vira *** — cobre forma de query, header e JSON
_SENSITIVE = re.compile(
    r"(?i)(set-cookie|cookie|sessionid|ms_token|access_token)"
    r"(['\"]?\s*[:=]\s*['\"]?)([^\s;,'\"}]+)"
)


def redact(text: str) -> str:
    """Mascara o valor de `sessionid`/`ms_token`/`access_token`/cookies."""
    return _SENSITIVE.sub(r"\1\2***", text)


_TRACEBACK_FORMATTER = logging.Formatter()


class RedactingFilter(logging.Filter):
    """Aplica `redact()` na mensagem final e no traceback de cada registro.

    Vai no HANDLER, não no logger: filtro de logger não vê registros que
    sobem propagados de loggers filhos (`content_factory.app.collect` etc.).
    Muta o registro (msg já interpolada, `args` zerado, `exc_text` pronto) —
    os demais handlers do mesmo registro também recebem a versão redigida.
    Nunca descarta registro.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = _TRACEBACK_FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
