from collections.abc import Sequence
from datetime import date
from typing import ClassVar, get_type_hints

from content_factory.app import ports
from content_factory.app.ports import (
    KeywordExpander,
    KeywordSignalRepository,
    SocialDiscoverer,
    TrendsProvider,
)
from content_factory.domain.errors import CollectorError
from content_factory.domain.kw_rank import SuggestHit
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

    async def expand(self, seeds: list[str], depth: int) -> list[SuggestHit]:
        return [SuggestHit(term=seeds[0], seed=seeds[0], position=1)]


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

    async def record_days(
        self, segment_id: int, keywords: Sequence[str], source: str, day: date
    ) -> int:
        return len(keywords)

    async def days_seen(
        self, segment_id: int, source: str, days: int, today: date
    ) -> dict[str, int]:
        return {}


def test_new_protocols_accept_structural_fakes() -> None:
    expander: KeywordExpander = _FakeKeywordExpander()
    trends: TrendsProvider = _FakeTrendsProvider()
    social: SocialDiscoverer = _FakeSocialDiscoverer()
    signals: KeywordSignalRepository = _FakeKeywordSignalRepository()
    assert expander.platform == "web_suggest"
    assert social.platform == "instagram"
    assert trends is not None and signals is not None


def test_refresh_result_e_remediation_target() -> None:
    from content_factory.app.ports import RefreshableCollector, RemediationRepository
    from content_factory.domain.models import RefreshResult, RemediationTarget

    assert RefreshableCollector.__name__ == "RefreshableCollector"
    assert RemediationRepository.__name__ == "RemediationRepository"
    t = RemediationTarget(video_id=1, platform="youtube", platform_video_id="abc")
    assert RefreshResult().missing_ids == [] and t.platform == "youtube"
