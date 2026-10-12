from collections.abc import Sequence
from typing import ClassVar, get_type_hints

from content_factory.app import ports
from content_factory.app.ports import (
    KeywordExpander,
    KeywordSignalRepository,
    SocialDiscoverer,
    TrendsProvider,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.models import CandidateVideo, KeywordSignal, TrendPoint


def test_collector_protocol_declares_discover() -> None:
    hints = get_type_hints(ports.Collector.discover)
    assert set(hints) >= {"segment", "limit", "return"}


def test_collector_error_carries_code() -> None:
    err = CollectorError("blocked", "tiktok")
    assert err.code == "blocked"
    assert err.platform == "tiktok"


class _FakeKeywordExpander:
    platform: ClassVar[str] = "web_suggest"

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
        return [KeywordSignal(keyword=seeds[0], source=self.platform, depth=depth)]


class _FakeTrendsProvider:
    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[TrendPoint]:
        return []


class _FakeSocialDiscoverer:
    platform: ClassVar[str] = "instagram"

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        return []


class _FakeKeywordSignalRepository:
    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int:
        return len(signals)

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]:
        return []


def test_new_protocols_accept_structural_fakes() -> None:
    expander: KeywordExpander = _FakeKeywordExpander()
    trends: TrendsProvider = _FakeTrendsProvider()
    social: SocialDiscoverer = _FakeSocialDiscoverer()
    signals: KeywordSignalRepository = _FakeKeywordSignalRepository()
    assert expander.platform == "web_suggest"
    assert social.platform == "instagram"
    assert trends is not None and signals is not None
