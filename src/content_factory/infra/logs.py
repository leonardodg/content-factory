"""Handler de log redigido para `content_factory.*` — worker E CLI (spec §7).

Sem handler próprio, os registros do app saem pelo `logging.lastResort`:
sem timestamp, sem nível e SEM redação. O worker (o arq só configura o
logger `arq`) instala em INFO; a CLI em WARNING (stdout é só o envelope;
o stderr recebe só alerta/erro).
"""

import logging

from content_factory.infra.redact import RedactingFilter

LOG_HANDLER_NAME = "cf-app"


def configure_logging(level: int = logging.INFO) -> None:
    """Instala 1 handler (stderr) com `RedactingFilter` no logger `content_factory`.

    Idempotente: chamar de novo só ajusta o nível. `propagate` fica ligado:
    worker e CLI não têm handler no root (sem duplicata) e o `caplog` dos
    testes continua vendo os registros.
    """
    app_logger = logging.getLogger("content_factory")
    app_logger.setLevel(level)
    if any(h.get_name() == LOG_HANDLER_NAME for h in app_logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(LOG_HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    app_logger.addHandler(handler)
