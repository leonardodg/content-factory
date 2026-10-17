"""Filtro de termos `term-filter-v1` (R1) — puro, sem I/O.

Precedência DOCUMENTADA do motivo: `blocked` → `lang` → `no_anchor`.

Idioma só reprova com EVIDÊNCIA POSITIVA de outra língua (listas fechadas de
inglês e espanhol). Ausência de palavra-função PT NÃO reprova: o Suggest
devolve frases curtas sem acento ("velas aromaticas"), que são PT. Quem
segura o nicho é a âncora.

Hashtags (`filter_hashtag`) não têm espaços nem palavras-função: âncora e
bloqueio casam por SUBSTRING da tag normalizada e não há checagem de idioma
(o idioma vem do vídeo de origem, R14).

O `filter-v1`/`intent-v3` da branch web NÃO é substituído por este: aquele
vive só no protocolo q1; este governa pool, hashtags e descoberta.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from content_factory.domain.text import contains_phrase, word_tokens

TERM_FILTER_VERSION = "term-filter-v1"
REASON_ORDER: tuple[str, ...] = ("blocked", "lang", "no_anchor")

# Marcadores de OUTRA língua — palavras que não existem (normalizadas) em PT.
EN_MARKERS = frozenset(
    {"for", "the", "and", "with", "of", "how", "to", "make", "making", "diy", "best", "easy"}
    # substantivos EN do nicho (revisão QA do PR #50): inglês sem palavra-função
    # ("soy wax candles") caía como no_anchor — descarte certo, motivo errado
    | {"candle", "candles", "wick", "wax", "soap", "soaps", "resin", "handmade", "homemade"}
)
ES_MARKERS = frozenset(
    {
        "y",
        "el",
        "los",
        "las",
        "del",
        "con",
        "en",
        "hacer",
        "caseras",
        "caseros",
        "faciles",
        "paso",
        "una",
        "jabon",
        "jabones",
        "veladora",
        "veladoras",
    }
)
NON_PT_PHRASES: tuple[str, ...] = ("de la", "para la", "para el", "en la")


@dataclass(frozen=True)
class TermFilterConfig:
    anchors: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()


@dataclass(frozen=True)
class TermVerdict:
    accepted: bool
    reason: str | None  # None | "blocked" | "lang" | "no_anchor"


def _is_other_language(words: list[str]) -> bool:
    if any(w in EN_MARKERS or w in ES_MARKERS for w in words):
        return True
    return any(contains_phrase(words, p) for p in NON_PT_PHRASES)


def _verdict(checks: dict[str, bool]) -> TermVerdict:
    reason = next((r for r in REASON_ORDER if checks.get(r, False)), None)
    return TermVerdict(accepted=reason is None, reason=reason)


def filter_term(term: str, config: TermFilterConfig) -> TermVerdict:
    words = word_tokens(term)
    return _verdict(
        {
            "blocked": any(contains_phrase(words, b) for b in config.blocked),
            "lang": _is_other_language(words),
            "no_anchor": bool(config.anchors)
            and not any(contains_phrase(words, a) for a in config.anchors),
        }
    )


def _compact(text: str) -> str:
    return "".join(word_tokens(text))


def filter_hashtag(tag: str, config: TermFilterConfig) -> TermVerdict:
    compact = _compact(tag)
    return _verdict(
        {
            "blocked": any(_compact(b) in compact for b in config.blocked),
            "no_anchor": bool(config.anchors)
            and not any(_compact(a) in compact for a in config.anchors),
        }
    )


def filter_terms(
    terms: Sequence[str], config: TermFilterConfig
) -> tuple[list[str], list[tuple[str, str]]]:
    """(aceitos, [(termo, motivo)] dos rejeitados) — ordem de entrada preservada."""
    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []
    for term in terms:
        verdict = filter_term(term, config)
        if verdict.reason is None:
            accepted.append(term)
        else:
            rejected.append((term, verdict.reason))
    return accepted, rejected
