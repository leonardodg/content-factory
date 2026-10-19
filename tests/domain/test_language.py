"""Regra de idioma do segmento (R14/17-10): regra única, usada pela coleta e
pela remediação.

`pt-BR` (público do teste): declarado com outra região sai (`pt-PT`); `pt` sem
região e sem idioma declarado ficam — sem evidência para descartar.
"""

import pytest

from content_factory.domain.language import language_matches, title_conflicts, wanted_language
from content_factory.domain.models import SearchConfig


def test_sem_wanted_ou_sem_declarado_mantem() -> None:
    assert language_matches(None, "pt") is True  # sem evidência
    assert language_matches("pt-BR", None) is True  # sem exigência
    assert language_matches("en", "") is True  # wanted vazio = sem exigência


def test_prefixo_estrangeiro_sai_e_nacional_fica() -> None:
    assert language_matches("en", "pt") is False
    assert language_matches("es", "pt") is False
    assert language_matches("pt-BR", "pt") is True
    assert language_matches("PT-br", "pt") is True  # casefold


def test_regiao_pedida_descarta_outra_regiao() -> None:
    assert language_matches("pt-PT", "pt-BR") is False
    assert language_matches("pt", "pt-BR") is True  # sem região: sem evidência
    assert language_matches("pt-BR", "pt-BR") is True
    assert language_matches("PT-Br", "pt-BR") is True  # casefold nos 2 lados
    assert language_matches(None, "pt-BR") is True


def test_sem_regiao_pedida_pt_pt_continua() -> None:
    assert language_matches("pt-PT", "pt") is True


def test_wanted_language_prefere_accepted_language() -> None:
    assert (
        wanted_language(SearchConfig(relevance_language="pt", accepted_language="pt-BR")) == "pt-BR"
    )
    assert wanted_language(SearchConfig(relevance_language="pt")) == "pt"
    assert wanted_language(SearchConfig()) is None
    assert wanted_language(None) is None


# --- relevance-v2 (QA D+2, 19/10): título sem idioma declarado ---


@pytest.mark.parametrize(
    "title",
    [
        "25 Resin Products You Can Make and Sell in 2026",
        "Mini Resin & Clay Craft Ideas 😍 Easy DIYs To Do When You're Bored!",
        "He used molten aluminum instead of epoxy resin #diy #construction",
        "El detalle de corazón en una vela artesanal 🤍 | Siempre Presente #Shorts",
        "Crea jabones naturales en casa con nuestra masterclass desde cero #jabones",
    ],
)
def test_titulo_em_outra_lingua_conflita(title: str) -> None:
    assert title_conflicts(title)


@pytest.mark.parametrize(
    "title",
    [
        "Efeito Resina Mágica: Uma Textura Flexível que Transforma o Artesanato!",
        "Sabonete artesanal de Bergamota. 🧡🍃 #saboneteartesanal #handmade #soap",
        "RESINA EPÓXI QUE SURPREENDE! 🤯 Hacks e DIY Satisfatórios | 123 GO!",
        "Artesanato em resina",
        "Rosa em Sabonete Artesanal",
    ],
)
def test_titulo_pt_nao_conflita(title: str) -> None:
    assert not title_conflicts(title)
