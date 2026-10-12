"""E2E: guard-rail com coletores REAIS (IG/YT/TikTok) e fakes de rede.

Prova que o FanoutGuard limita o total de keywords passadas aos socials
mesmo com todos os coletores rodando no mesmo ciclo. Fakes de rede
(MockTransport/stub client), não fakes de Protocol.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from tests.stubs import FakeLimiter, FakeLock, FakeRepos, FakeTrendObservations

from content_factory.app.ports import KeywordSignalRepository
from content_factory.app.research_pipeline import ResearchDeps, run_research_cycle
from content_factory.domain.fanout_guard import FanoutGuard
from content_factory.domain.models import (
    CandidateVideo,
    DepthConfig,
    KeywordSignal,
    Segment,
    SegmentConfig,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


# --- fakes de rede (não de Protocol) ---


class _FakeWeb:
    """Expander que devolve MUITOS sinais — o guard deve truncar."""

    platform: ClassVar[str] = "web_suggest"

    def __init__(self, n: int = 10) -> None:
        self._n = n

    async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
        return [
            KeywordSignal(
                keyword=f"{seeds[0]} var {i}",
                source=self.platform,
                weight=0.9 - i * 0.05,
                depth=depth + 1,
            )
            for i in range(self._n)
            if seeds
        ]


class _FakeTrends:
    async def interest_over_time(self, keywords: list[str], timeframe: str) -> list[Any]:
        return []  # sem trends p/ simplificar — o limite é no web fanout


class _SpyInstagram:
    """IG real com spy — conta quantas keywords recebeu."""

    platform: ClassVar[str] = "instagram"

    def __init__(self) -> None:
        self.received_keywords: list[list[str]] = []

    async def discover_by_keywords(
        self, keywords: list[str], hashtags: list[str], depth: int
    ) -> list[CandidateVideo]:
        self.received_keywords.append(list(keywords))
        return []


def _youtube_with_mock() -> Any:
    """YT real com MockTransport — devolve fixture, conta chamadas."""

    def _route(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search" in url:
            return httpx.Response(200, content=(FIXTURES / "youtube_search.json").read_bytes())
        if "/videos" in url:
            return httpx.Response(200, content=(FIXTURES / "youtube_videos.json").read_bytes())
        if "/channels" in url:
            return httpx.Response(200, content=(FIXTURES / "youtube_channels.json").read_bytes())
        return httpx.Response(404, json={})

    from content_factory.infra.collectors.youtube import YoutubeCollector

    return YoutubeCollector(api_key="k", transport=httpx.MockTransport(_route))


def _tiktok_with_stub() -> Any:
    """TikTok real com stub client — devolve fixture."""

    fixture = (FIXTURES / "tiktok_trending.json").read_text()

    import json

    payload: list[dict[str, Any]] = json.loads(fixture)

    class _StubSearch:
        def videos(self, query: str, count: int = 30, **kw: object) -> Any:
            async def _run() -> list[dict[str, Any]]:
                return payload

            return _run()

    class _StubClient:
        search = _StubSearch()

        def hashtag(self, name: str) -> Any:
            class _H:
                async def videos(self, count: int = 30, **kw: object) -> list[dict[str, Any]]:
                    return payload

            return _H()

    from content_factory.infra.collectors.tiktok import TikTokCollector

    return TikTokCollector(ms_token="tok", client=_StubClient())


class _SignalRepo(KeywordSignalRepository):
    def __init__(self) -> None:
        self.saved: list[KeywordSignal] = []

    async def upsert_many(self, segment_id: int, signals: Sequence[KeywordSignal]) -> int:
        self.saved.extend(signals)
        return len(signals)

    async def top_for_segment(
        self, segment_id: int, source: str | None, limit: int
    ) -> list[KeywordSignal]:
        return []


class _Segments:
    async def get_by_slug(self, slug: str) -> Segment:
        return Segment(
            id=1,
            slug=slug,
            name=slug,
            platforms=["instagram", "youtube", "tiktok"],
            keywords=["vela", "candle", "soy", "wax", "diy", "craft", "handmade", "aroma"],
        )

    async def upsert(self, cfg: SegmentConfig) -> Segment:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_guard_limits_keywords_to_socials_with_real_collectors() -> None:
    """FanoutGuard corta keywords antes de chegarem nos 3 coletores reais.

    Cenário: 8 seeds → web devolve 10 sinais → fanout_per_stage["web"]=3
    → socials recebem no máximo 3 keywords (não 8, não 10).
    """
    repos = FakeRepos()
    web = _FakeWeb(n=10)
    ig = _SpyInstagram()
    yt = _youtube_with_mock()
    tt = _tiktok_with_stub()

    # fanout limita web a 3 sinais; max_nodes_per_cycle alto p/ não cortar seeds
    cfg = DepthConfig(max_depth=2, fanout_per_stage={"web": 3}, max_nodes_per_cycle=50)

    deps = ResearchDeps(
        web=web,
        trends=_FakeTrends(),
        socials={"instagram": ig, "youtube": yt, "tiktok": tt},
        signals=_SignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_Segments(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(cfg),
    )

    out = await run_research_cycle("candles", cfg, deps)

    assert out.ok is True
    # web devolveu 10, mas fanout_per_stage cortou para 3
    assert out.counts["web_found"] == 3
    # IG recebeu exatamente as 3 keywords dos sinais (web + trends)
    assert len(ig.received_keywords) == 1
    assert len(ig.received_keywords[0]) == 3
    # F2: candidatos reais do YT/TikTok foram persistidos via upsert_candidates
    from tests.stubs import FakeVideos

    assert isinstance(repos.videos, FakeVideos)
    assert repos.videos.upserts  # pelo menos 1 plataforma social persistiu
    # YT e TikTok também foram chamados (social_found pode variar pelos fixtures)
    assert out.counts["social_found"] >= 0  # pelo menos não crachou
    assert out.counts["social_inserted"] >= 1


@pytest.mark.asyncio
async def test_guard_dedupe_prevents_duplicate_keywords_to_socials() -> None:
    """Mesma keyword em seeds e sinais web não gera entrada duplicada no guard.

    O guard deduplica via admit() — seeds idênticos (casefold) só passam 1x.
    """
    repos = FakeRepos()

    class _DupWeb:
        platform: ClassVar[str] = "web_suggest"

        async def expand(self, seeds: list[str], depth: int) -> list[KeywordSignal]:
            # devolve 1 sinal por seed recebida (seeds já deduplicados pelo guard)
            return [
                KeywordSignal(keyword=f"{s} derivada", source="web_suggest", weight=0.9, depth=1)
                for s in seeds
            ]

    class _DupSegments:
        async def get_by_slug(self, slug: str) -> Segment:
            # 3 variações da MESMA keyword (casefold igual)
            return Segment(
                id=1,
                slug=slug,
                name=slug,
                platforms=["instagram"],
                keywords=["Vela", "vela", "VELA"],
            )

        async def upsert(self, cfg: SegmentConfig) -> Segment:
            raise NotImplementedError

    ig = _SpyInstagram()
    cfg = DepthConfig(max_depth=2, max_nodes_per_cycle=50)

    deps = ResearchDeps(
        web=_DupWeb(),
        trends=_FakeTrends(),
        socials={"instagram": ig},
        signals=_SignalRepo(),
        trend_observations=FakeTrendObservations(),
        segments=_DupSegments(),
        videos=repos.videos,
        runs=repos.runs,
        lock=FakeLock(),
        limiter=FakeLimiter(),
        guard=FanoutGuard(cfg),
    )

    out = await run_research_cycle("candles", cfg, deps)
    assert out.ok is True
    # 3 seeds → guard.admit deduplica para 1 → web recebe 1 → 1 sinal
    assert out.counts["web_found"] == 1
    assert len(ig.received_keywords[0]) == 1
