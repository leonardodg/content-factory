import pytest

from content_factory.domain.models import SegmentConfig
from content_factory.infra.collectors import web as web_mod
from content_factory.infra.collectors.web import WebCollector

SEG = SegmentConfig(slug="candles", name="Velas", platforms=["web"], keywords=["vela artesanal"])


class StubDDGS:
    def text(self, query: str, max_results: int = 10) -> list[dict[str, str]]:
        return [
            {
                "title": "Tutorial de velas",
                "href": "https://ex.com/vela",
                "body": "aprenda a fazer velas de soja",
            }
        ]


@pytest.mark.asyncio
async def test_discover_maps_ddgs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_mod, "DDGS", lambda: StubDDGS())
    out = await WebCollector().discover(SEG, limit=5)
    assert out and out[0].platform == "web"
    assert out[0].title == "Tutorial de velas"
    assert out[0].url == "https://ex.com/vela"
    assert out[0].views is None


def test_register_defaults_registers_web() -> None:
    from content_factory.config import Settings
    from content_factory.infra.collectors import register_defaults, registry

    register_defaults(Settings())
    assert isinstance(registry.get("web"), WebCollector)
