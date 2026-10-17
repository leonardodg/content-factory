from pathlib import Path

import pytest

from content_factory.app.segment import load_segment_file, upsert_segment
from content_factory.domain.errors import DomainError, SegmentNotFound
from content_factory.domain.models import Segment, SegmentConfig
from content_factory.domain.suggest_probes import suggest_probes
from content_factory.domain.term_filter import TermFilterConfig, filter_term


class FakeSegmentRepository:
    def __init__(self) -> None:
        self.saved: list[SegmentConfig] = []

    async def get_by_slug(self, slug: str) -> Segment:
        raise SegmentNotFound(slug)

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        self.saved.append(cfg)
        return Segment(id=1, **cfg.model_dump())


def test_load_candles_yaml() -> None:
    cfg = load_segment_file(Path("segments/candles.yaml"))
    assert cfg.slug == "candles"
    assert "youtube" in cfg.platforms
    assert any("vela" in k for k in cfg.keywords)


def test_invalid_yaml_raises_domain_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("segment:\n  name: x\n")  # sem slug/keywords/platforms
    with pytest.raises(DomainError):
        load_segment_file(bad)


@pytest.mark.parametrize("slug", ["velas-artesanais", "sabonetes-artesanais", "resina-artesanal"])
def test_pt_segments_declare_probe_terms(slug: str) -> None:
    """Opcao (a): o Suggest so rende no termo-cabeca (medido 16/10)."""
    cfg = load_segment_file(Path(f"segments/{slug}.yaml"))
    assert cfg.probe_terms, f"{slug} sem probe_terms: as sondas nao rodam"
    probes = suggest_probes(cfg.probe_terms)
    # as quatro familias de sonda sao geradas para cada termo-cabeca
    assert any(p.startswith("como fazer ") for p in probes)  # aprender
    assert any(p.endswith(" nao") for p in probes)  # resolver
    assert any(p.endswith(" tendencia") for p in probes)  # tendencia
    assert any(p.endswith(" mais vendido") for p in probes)  # tendencia
    assert any(p.endswith(" preco") for p in probes)  # comercial


async def test_upsert_segment_persists_config_via_repository() -> None:
    repo = FakeSegmentRepository()
    seg = await upsert_segment(repo, Path("segments/candles.yaml"))
    assert seg.id == 1
    assert repo.saved[0].slug == "candles"
    assert "artesanato" in repo.saved[0].audience


_REPO = Path(__file__).resolve().parents[2]


def test_segmentos_pt_tem_ancoras_e_bloqueios() -> None:
    for slug in ("velas-artesanais", "sabonetes-artesanais", "resina-artesanal"):
        cfg = load_segment_file(_REPO / "segments" / f"{slug}.yaml")
        assert cfg.anchor_terms, slug
        assert cfg.blocked_terms, slug


# Revisão QA do PR #50: varredura de TODOS os termos ativos do pool real
# (Suggest de 16/10) achou marcas e outros sentidos que a amostra de 13 não
# pegou. Os casos são literais e passam pelos YAMLs reais.
_FORA_DO_NICHO = {
    "resina-artesanal": [
        "pigmento para resina odontologia",
        "pigmento para resina composta",
        "resina epóxi para piso",
        "como fazer resina epóxi para piso",
        "pigmento para resina 3d",
    ],
    "sabonetes-artesanais": [
        "sabonete mais vendido da boticário",
        "preco sabonete boticario",
        "marcas de sabonetes mais vendidos",
    ],
    "velas-artesanais": [
        "preco vela carro",
        "vela aromática boticário",
        "vela aromática granado",
    ],
}
_NO_NICHO = {
    "resina-artesanal": ["chaveiro de resina", "artesanato em resina epoxi para iniciantes"],
    # "facial" NÃO é bloqueio: sabonete facial artesanal é tema do nicho (o
    # resíduo "sabonetes faciais mais vendidos" é aceito conscientemente)
    "sabonetes-artesanais": [
        "como fazer sabonete artesanal",
        "sabonete glicerinado de bebê",
        "sabonete facial artesanal",
    ],
    "velas-artesanais": ["como fazer velas perfumadas", "vela nao acende"],
}


def _filter_cfg(slug: str) -> TermFilterConfig:
    cfg = load_segment_file(_REPO / "segments" / f"{slug}.yaml")
    return TermFilterConfig(anchors=tuple(cfg.anchor_terms), blocked=tuple(cfg.blocked_terms))


def test_yamls_bloqueiam_marcas_e_outros_sentidos_do_pool_real() -> None:
    for slug, termos in _FORA_DO_NICHO.items():
        cfg = _filter_cfg(slug)
        for termo in termos:
            assert filter_term(termo, cfg).reason == "blocked", (slug, termo)


def test_yamls_nao_bloqueiam_o_nicho() -> None:
    for slug, termos in _NO_NICHO.items():
        cfg = _filter_cfg(slug)
        for termo in termos:
            assert filter_term(termo, cfg).accepted, (slug, termo)
