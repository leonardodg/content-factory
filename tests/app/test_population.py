"""Bloco 5 Task 6: população → `ViralInput` (relevância, velocidade, engajamento)."""

from datetime import UTC, datetime, timedelta

from content_factory.app.population import build_population, is_relevant, to_viral_input
from content_factory.domain.models import PopulationRow, SearchConfig, Segment

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
SEG = Segment(
    id=1,
    slug="velas-artesanais",
    name="Velas",
    platforms=["youtube"],
    keywords=["vela artesanal"],
    anchor_terms=["vela", "velas"],
    blocked_terms=["martillo"],
    search=SearchConfig(relevance_language="pt", accepted_language="pt-BR"),
)


def _row(vid: int = 1, **kw: object) -> PopulationRow:
    base: dict[str, object] = {
        "video_id": vid,
        "platform": "youtube",
        "platform_video_id": f"v{vid}",
        "title": "Como fazer vela aromática",
        "description": "",
        "language": "pt-BR",
        "views": 1000,
        "likes": 50,
        "comments": 5,
        "captured_at": NOW,
        "prev_views": 400,
        "prev_captured_at": NOW - timedelta(hours=6),
        "followers": 2000,
    }
    base.update(kw)
    return PopulationRow.model_validate(base)


def test_descricao_com_ingles_de_rodape_nao_derruba_video_pt() -> None:
    """Falso negativo medido na revisão: "Follow me ... for more" na descrição."""
    row = _row(description="Follow me on Instagram for more tips and the best ideas")
    assert is_relevant(row, SEG)


def test_idioma_pt_pt_ou_ingles_fica_fora() -> None:
    assert not is_relevant(_row(language="pt-PT"), SEG)
    assert not is_relevant(_row(language="en"), SEG)
    assert is_relevant(_row(language="pt"), SEG)  # sem região: sem evidência
    assert is_relevant(_row(language=None), SEG)


def test_ancora_na_tag_basta_quando_o_titulo_nao_tem() -> None:
    row = _row(title="Decoração de natal fácil", tags=["velaartesanal"])
    assert is_relevant(row, SEG)
    assert not is_relevant(_row(title="Decoração de natal fácil", tags=["natal"]), SEG)


def test_bloqueio_no_titulo_fica_fora() -> None:
    assert not is_relevant(_row(title="vela martillo tendencia"), SEG)


def test_velocidade_por_hora_e_engajamento() -> None:
    vi = to_viral_input(_row(), segment_id=1)
    assert vi.velocity_per_hour == 100.0  # (1000-400)/6 h
    assert vi.engagement_rate is not None and vi.engagement_rate > 0
    assert vi.channel_followers == 2000 and vi.views == 1000


def test_sem_par_de_medicoes_velocidade_nula_e_queda_vira_zero() -> None:
    assert to_viral_input(_row(prev_views=None, prev_captured_at=None), 1).velocity_per_hour is None
    assert to_viral_input(_row(prev_views=2000), 1).velocity_per_hour == 0.0


def test_build_population_separa_relevantes_e_conta_descartados() -> None:
    inputs, skipped = build_population([_row(1), _row(2, language="en")], SEG)
    assert [i.video_id for i in inputs] == [1] and skipped == 1


# --- relevance-v2 (QA D+2, 19/10) ---


def test_bloqueio_na_tag_fica_fora() -> None:
    """Medido: "Como Analisar Mistura de Combustível pela COR da VELA" com a
    tag `vela de ignicao` passava — o bloqueio só olhava o título."""
    seg = SEG.model_copy(update={"blocked_terms": ["ignicao"]})
    row = _row(title="Como analisar a cor da vela", tags=["vela de ignicao", "moto"])
    assert not is_relevant(row, seg)
    assert is_relevant(_row(title="Como analisar a cor da vela", tags=["vela"]), seg)


def test_sem_idioma_titulo_em_ingles_ou_espanhol_fica_fora() -> None:
    assert not is_relevant(
        _row(language=None, title="25 Candle Products You Can Make and Sell in 2026"), SEG
    )
    assert not is_relevant(
        _row(language=None, title="El detalle de corazón en una vela artesanal"), SEG
    )


def test_sem_idioma_titulo_pt_com_hashtag_inglesa_fica() -> None:
    assert is_relevant(
        _row(language=None, title="Vela artesanal de lavanda #handmade #candle"), SEG
    )
    # idioma declarado pt-BR: a regra do título não se aplica
    assert is_relevant(_row(language="pt-BR", title="Easy candle with vela #diy"), SEG)
