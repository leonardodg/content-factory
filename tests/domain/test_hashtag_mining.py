from content_factory.domain.hashtag_mining import HashtagSource, extract_hashtags, mine
from content_factory.domain.term_filter import TermFilterConfig

VELAS = TermFilterConfig(anchors=("vela", "velas"), blocked=("bajista",))
RAW = {"snippet": {"tags": ["velas artesanais", "decoracao"], "description": "x"}}


def test_extrai_tags_sem_espaco_e_hashtags_do_titulo() -> None:
    tags = extract_hashtags(HashtagSource(1, "youtube", 9.0, RAW, title="Vela #VelaDeSoja"))
    assert {"velasartesanais", "veladesoja", "decoracao"} <= set(tags)


def test_so_top_25_por_cento_e_so_tags_do_nicho() -> None:
    fraca = {"snippet": {"tags": ["velafraca"]}}
    sources = [
        HashtagSource(i, "youtube", float(i), RAW if i >= 8 else fraca) for i in range(1, 11)
    ]
    tags = {h.tag for h in mine(sources, VELAS)}
    assert "velasartesanais" in tags
    assert "velafraca" not in tags  # fora do top 25%
    assert "decoracao" not in tags  # sem âncora


def test_sem_viral_v2_nao_entra_e_top_k_por_plataforma() -> None:
    assert mine([HashtagSource(1, "youtube", None, RAW)], VELAS) == []
    muitas = {"snippet": {"tags": [f"vela{i}" for i in range(30)]}}
    assert len(mine([HashtagSource(1, "youtube", 10.0, muitas)], VELAS)) == 10
