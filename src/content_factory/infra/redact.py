"""Redação de logs (spec §7): segredos nunca saem em claro."""

import logging
import re

# valor depois do padrão vira *** — cobre forma de query, header e JSON.
# `&` encerra o valor (query string): sem isso o resto da URL era engolido.
_SENSITIVE = re.compile(
    r"(?i)(set-cookie|cookie|sessionid|ms_token|access_token)"
    r"(['\"]?\s*[:=]\s*['\"]?)([^\s;,&'\"}]+)"
)
# Revisão 2026-10-14 (A2, R3): nomes genéricos de segredo como PARÂMETRO —
# `key` é a API key do YouTube na query. Fronteira de palavra para não pegar
# `monkey=`/`turkey=`; `access_token` já é coberto acima.
_SECRET_PARAMS = re.compile(
    r"(?i)(\b(?:key|api_?key|token|password|passwd|secret|client_secret)\b)"
    r"(['\"]?\s*[:=]\s*['\"]?)([^\s;,&'\"}]+)"
)
# credencial embutida em URL: scheme://usuario:SENHA@host (usuário pode ser vazio)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s@]*:)([^@\s/]+)(@)")


def redact(text: str) -> str:
    """Mascara segredos: tokens/cookies, parâmetros de segredo e senha em URL."""
    text = _URL_CREDENTIALS.sub(r"\1***\3", text)
    text = _SENSITIVE.sub(r"\1\2***", text)
    return _SECRET_PARAMS.sub(r"\1\2***", text)


_TRACEBACK_FORMATTER = logging.Formatter()


class RedactingFilter(logging.Filter):
    """Aplica `redact()` na mensagem final e no traceback de cada registro.

    Vai no HANDLER, não no logger: filtro de logger não vê registros que
    sobem propagados de loggers filhos (`content_factory.app.collect` etc.).
    Muta o registro (msg já interpolada, `args` zerado, `exc_text` pronto) —
    os demais handlers do mesmo registro também recebem a versão redigida.
    Nunca descarta registro nem levanta (formato com args errados vira a
    mensagem crua, redigida).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # args que não batem com o formato: não sobe para o app
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = _TRACEBACK_FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
