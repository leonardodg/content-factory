"""Regra de idioma do segmento (T3, 2026-10-16; decisão 17/10) — uma só.

Vive no domínio porque é política, não detalhe do YouTube: a **coleta** filtra
os candidatos da busca e a **remediação** filtra os alvos de re-medição com a
MESMA função. O público do teste é o Brasil (`accepted_language: pt-BR`).

Sem evidência, mantém: `relevanceLanguage` é só preferência da API e frases sem
resultado em pt voltam completadas com vídeos em en/es.
"""

import re

from content_factory.domain.models import SearchConfig
from content_factory.domain.term_filter import is_other_language
from content_factory.domain.text import word_tokens

_HASHTAG_RE = re.compile(r"#\S+")
# Palavras que só o PT usa (ES/EN não): uma basta para manter o título.
# "do" fica fora: também é inglês ("Easy DIYs To Do").
PT_ONLY_WORDS = frozenset(
    {"com", "fazer", "voce", "nao", "da", "dos", "das", "na", "nas", "em", "um", "uma"}
    | {"pra", "seu", "sua", "e"}
)


def wanted_language(search: SearchConfig | None) -> str | None:
    """Idioma exigido do segmento.

    `accepted_language` (com região: `pt-BR`) tem precedência sobre
    `relevance_language` (2 letras, o formato que a API aceita). Sem
    `SearchConfig`, nenhuma exigência.
    """
    if search is None:
        return None
    return search.accepted_language or search.relevance_language


def language_matches(declared: str | None, wanted: str | None) -> bool:
    """Filtro pós-busca do idioma (T3, 2026-10-16).

    `relevanceLanguage` é só preferência na API: frases exatas sem resultado em
    pt voltam completadas com vídeos em en/es. Descarta o vídeo cujo idioma
    DECLARADO tem outro prefixo (pt-BR ≈ pt). Sem idioma declarado, mantém:
    não há evidência para descartar.

    `wanted` com região (`pt-BR`, de `accepted_language`): declarado com região
    tem de ser igual (`pt-PT` sai); declarado só com o idioma (`pt`) fica —
    não há evidência de outra região.
    """
    if not wanted or not declared:
        return True
    want_lang, _, want_region = wanted.casefold().partition("-")
    decl_lang, _, decl_region = declared.casefold().partition("-")
    if decl_lang != want_lang:
        return False
    return not (want_region and decl_region) or decl_region == want_region


def title_conflicts(title: str) -> bool:
    """`relevance-v2` (QA D+2, 19/10): título com evidência de EN/ES e sem
    nenhuma palavra só do PT. Para vídeo SEM idioma declarado num segmento
    pt — metade dos "relevantes" de resina eram títulos em inglês.

    As `#hashtags` saem antes: título PT com `#handmade #soap` não é inglês.
    """
    text = _HASHTAG_RE.sub(" ", title)
    if not is_other_language(text):
        return False
    return not any(w in PT_ONLY_WORDS for w in word_tokens(text))
