from pathlib import Path

import pytest

from content_factory.app.segment import load_segment_file, upsert_segment
from content_factory.domain.errors import DomainError, SegmentNotFound
from content_factory.domain.models import Segment, SegmentConfig
from content_factory.domain.suggest_probes import suggest_probes


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
