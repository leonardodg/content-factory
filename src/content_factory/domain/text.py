"""Normalização de texto pura (caixa, acento) — base do casamento por tokens."""

import re
import unicodedata

_DOMAIN_RE = re.compile(r"(?i)^(?:[a-z0-9-]+\.)+[a-z]{2,}$")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Minúsculas, sem acento, espaços colapsados."""
    return " ".join(strip_accents(text).casefold().split())


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def looks_like_domain(text: str) -> bool:
    """Título que é só um nome de domínio (`exemplo.com.br`)."""
    stripped = text.strip()
    return bool(stripped) and " " not in stripped and _DOMAIN_RE.match(stripped) is not None
