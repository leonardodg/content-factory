"""Redação de logs (spec §7): segredos nunca saem em claro."""

import re

# valor depois do padrão vira *** — cobre forma de query, header e JSON
_SENSITIVE = re.compile(
    r"(?i)(set-cookie|cookie|sessionid|ms_token|access_token)"
    r"(['\"]?\s*[:=]\s*['\"]?)([^\s;,'\"}]+)"
)


def redact(text: str) -> str:
    """Mascara o valor de `sessionid`/`ms_token`/`access_token`/cookies."""
    return _SENSITIVE.sub(r"\1\2***", text)
