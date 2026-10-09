from typing import ClassVar

import pytest

from content_factory.domain.errors import DomainError
from content_factory.domain.models import CandidateVideo, SegmentConfig
from content_factory.infra.collectors import registry


def test_register_and_resolve() -> None:
    class Fake:
        platform: ClassVar[str] = "fake"

        async def discover(self, segment: SegmentConfig, limit: int) -> list[CandidateVideo]:
            return []

    registry.register(Fake())
    assert registry.get("fake").platform == "fake"
    assert [c.platform for c in registry.resolve(["fake"])] == ["fake"]


def test_unknown_platform_raises() -> None:
    with pytest.raises(DomainError):
        registry.get("desconhecida")
