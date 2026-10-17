"""Regra de idioma do segmento (T3, 2026-10-16; decisão 17/10) — uma só.

Vive no domínio porque é política, não detalhe do YouTube: a **coleta** filtra
os candidatos da busca e a **remediação** filtra os alvos de re-medição com a
MESMA função. O público do teste é o Brasil (`accepted_language: pt-BR`).

Sem evidência, mantém: `relevanceLanguage` é só preferência da API e frases sem
resultado em pt voltam completadas com vídeos em en/es.
"""

from content_factory.domain.models import SearchConfig


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
