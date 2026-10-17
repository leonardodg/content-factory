from typing import Any

import httpx
import pytest

from content_factory.app.call_counters import counting
from content_factory.domain.errors import UpstreamChanged
from content_factory.domain.models import RemediationTarget, SegmentConfig
from content_factory.infra.collectors.youtube import YoutubeCollector

SEG = SegmentConfig(
    slug="candles", name="Velas", platforms=["youtube"], keywords=["candle making", "vela de soja"]
)


@pytest.mark.asyncio
async def test_discover_maps_fixture(httpx_mock_transport: httpx.MockTransport) -> None:
    c = YoutubeCollector(api_key="k", transport=httpx_mock_transport)
    with counting() as counters:
        out = await c.discover(SEG, limit=5)
    assert out and out[0].platform == "youtube"
    assert out[0].views is not None and out[0].url.startswith("https://")
    assert out[0].duration_s == 492  # PT8M12S
    assert out[0].creator_followers == 54000
    assert counters.quota_used == 102  # 100 search + 1 videos + 1 channels (por chamada)


@pytest.mark.asyncio
async def test_discover_by_keywords_builds_query_and_returns_videos() -> None:
    """discover_by_keywords monta query a partir de keywords+hashtags e usa o MESMO fluxo."""
    captured: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        from pathlib import Path

        fixtures = Path(__file__).parents[2] / "fixtures"
        url = str(request.url)
        if "/search" in url:
            captured.append(request.url.params.get("q", ""))
            return httpx.Response(200, content=(fixtures / "youtube_search.json").read_bytes())
        if "/videos" in url:
            return httpx.Response(200, content=(fixtures / "youtube_videos.json").read_bytes())
        if "/channels" in url:
            return httpx.Response(200, content=(fixtures / "youtube_channels.json").read_bytes())
        return httpx.Response(404, json={})

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_route))
    with counting() as counters:
        out = await c.discover_by_keywords(
            keywords=["candle making", "DIY candle"], hashtags=["#velas", "#soy"], depth=1
        )
    assert out and out[0].platform == "youtube"
    assert out[0].duration_s == 492
    assert counters.quota_used == 102
    # query = keywords+hashtags unidos (máx 5 termos)
    assert len(captured) == 1
    assert "candle making" in captured[0]
    assert "DIY candle" in captured[0]
    assert '"velas"' in captured[0]  # `#` sai, frase entre aspas


@pytest.mark.asyncio
async def test_quota_403_becomes_rate_limited() -> None:
    from content_factory.domain.errors import RateLimited

    def _boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "quota"}})

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_boom))
    with pytest.raises(RateLimited):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_missing_api_key_raises_auth_expired_without_http() -> None:
    """Auditoria A-3: chave vazia = credencial, não cota — falha antes da chamada.

    Sem isso a run saía `rate_limited` e abria o breaker por plataforma,
    mascarando a ausência de `YOUTUBE_API_KEY` como problema de quota.
    """
    from content_factory.domain.errors import AuthExpired

    def _nope(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP não pode ser chamado sem YOUTUBE_API_KEY")

    c = YoutubeCollector(api_key="", transport=httpx.MockTransport(_nope))
    with pytest.raises(AuthExpired, match="YOUTUBE_API_KEY"):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403])
async def test_key_invalid_raises_auth_expired(status: int) -> None:
    """Auditoria A-3: 400/403 com reason `keyInvalid` é credencial revogada.

    Só `quotaExceeded`/`rateLimitExceeded` (e 403 sem reason legível) são
    cota; chave inválida não pode abrir o breaker.
    """
    from content_factory.domain.errors import AuthExpired

    def _invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"message": "API key not valid", "errors": [{"reason": "keyInvalid"}]}},
        )

    c = YoutubeCollector(api_key="bad", transport=httpx.MockTransport(_invalid))
    with pytest.raises(AuthExpired, match="keyInvalid"):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_quota_exceeded_reason_stays_rate_limited() -> None:
    """Auditoria A-3: reason `quotaExceeded` continua sendo cota (rate_limited)."""
    from content_factory.domain.errors import RateLimited

    def _quota(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}},
        )

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_quota))
    with pytest.raises(RateLimited):
        await c.discover(SEG, limit=5)


# Shape REAL da API (2026-10-14, chave falsa): 400 com errors[].reason
# genérico "badRequest" e o motivo verdadeiro só em details[].reason
_REAL_KEY_INVALID = {
    "error": {
        "code": 400,
        "message": "API key not valid. Please pass a valid API key.",
        "errors": [{"message": "API key not valid.", "domain": "global", "reason": "badRequest"}],
        "status": "INVALID_ARGUMENT",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"},
            {"@type": "type.googleapis.com/google.rpc.LocalizedMessage"},
        ],
    }
}


@pytest.mark.asyncio
async def test_real_api_key_invalid_payload_raises_auth_expired() -> None:
    """Revisão N-1: o payload real não traz `keyInvalid` — o motivo está em details.

    Sem ler `details[].reason` a chave revogada virava `upstream_changed`
    (HTTP 400) e abria o breaker em 3 falhas.
    """
    from content_factory.domain.errors import AuthExpired

    def _invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=_REAL_KEY_INVALID)

    c = YoutubeCollector(api_key="bad", transport=httpx.MockTransport(_invalid))
    with pytest.raises(AuthExpired, match="API_KEY_INVALID"):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("errors_reason", "details_reason"),
    [
        ("accessNotConfigured", None),
        ("forbidden", "SERVICE_DISABLED"),
        ("forbidden", "API_KEY_SERVICE_BLOCKED"),
        ("ipRefererBlocked", None),
        ("forbidden", "API_KEY_HTTP_REFERRER_BLOCKED"),
        ("forbidden", "API_KEY_IP_ADDRESS_BLOCKED"),
    ],
)
async def test_key_configuration_403_raises_auth_expired(
    errors_reason: str, details_reason: str | None
) -> None:
    """Revisão N-2: 403 de configuração da chave é ação humana, não cota."""
    from content_factory.domain.errors import AuthExpired

    error: dict[str, object] = {"errors": [{"reason": errors_reason}]}
    if details_reason:
        error["details"] = [{"reason": details_reason}]

    def _blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": error})

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_blocked))
    with pytest.raises(AuthExpired):
        await c.discover(SEG, limit=5)


@pytest.mark.asyncio
async def test_unknown_403_and_400_keep_classification() -> None:
    """403 sem reason legível continua cota; 400 desconhecido continua upstream."""
    from content_factory.domain.errors import RateLimited, UpstreamChanged

    def _bare_403(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html>forbidden</html>")

    def _bad_400(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"errors": [{"reason": "invalidParameter"}]}})

    with pytest.raises(RateLimited):
        await YoutubeCollector(api_key="k", transport=httpx.MockTransport(_bare_403)).discover(
            SEG, limit=5
        )
    with pytest.raises(UpstreamChanged):
        await YoutubeCollector(api_key="k", transport=httpx.MockTransport(_bad_400)).discover(
            SEG, limit=5
        )


def test_register_defaults_registers_youtube() -> None:
    from content_factory.config import Settings
    from content_factory.infra.collectors import register_defaults, registry

    register_defaults(Settings(youtube_api_key="k"))
    assert isinstance(registry.get("youtube"), YoutubeCollector)


@pytest.mark.asyncio
async def test_aclose_closes_http_client_and_is_idempotent() -> None:
    """Auditoria externa 4.1: o AsyncClient do coletor é fechado no shutdown."""
    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    await c.aclose()
    await c.aclose()
    assert c._client.is_closed  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (
            ["vela artesanal", "vela artesanal aromatica"],
            '"vela artesanal" OR "vela artesanal aromatica"',
        ),
        (["#velas", 'vela "soja"'], '"velas" OR "vela soja"'),
        (["  ", "candle"], '"candle"'),
    ],
)
def test_or_query_quotes_each_phrase(terms: list[str], expected: str) -> None:
    """Busca real 2026-10-15: frases sem aspas unidas por OR → 0 resultados;
    com aspas → 10 (mesmos 5 termos do research de velas-artesanais)."""
    from content_factory.infra.collectors.youtube import or_query

    assert or_query(terms) == expected


@pytest.mark.asyncio
async def test_discover_sends_quoted_or_query() -> None:
    captured: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            captured.append(request.url.params.get("q", ""))
        return httpx.Response(200, json={"items": []})

    c = YoutubeCollector(api_key="k", transport=httpx.MockTransport(_route))
    await c.discover(SEG, limit=5)
    assert captured == ['"candle making" OR "vela de soja"']


def _capture_search(captured: list[dict[str, str]]) -> httpx.MockTransport:
    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            captured.append(dict(request.url.params))
        return httpx.Response(200, json={"items": []})

    return httpx.MockTransport(_route)


@pytest.mark.asyncio
async def test_segment_search_is_sent_and_overrides_default() -> None:
    """Locale do segmento (YAML) vai na busca e vence o padrão global."""
    from content_factory.domain.models import SearchConfig

    captured: list[dict[str, str]] = []
    seg = SEG.model_copy(update={"search": SearchConfig(relevance_language="pt", region_code="BR")})
    c = YoutubeCollector(
        api_key="k",
        transport=_capture_search(captured),
        default_search=SearchConfig(relevance_language="en", region_code="US"),
    )
    await c.discover(seg, limit=5)
    assert captured[0]["relevanceLanguage"] == "pt" and captured[0]["regionCode"] == "BR"


@pytest.mark.asyncio
async def test_default_search_applies_and_absent_locale_sends_nothing() -> None:
    from content_factory.domain.models import SearchConfig

    captured: list[dict[str, str]] = []
    c = YoutubeCollector(
        api_key="k",
        transport=_capture_search(captured),
        default_search=SearchConfig(region_code="BR"),
    )
    await c.discover(SEG, limit=5)
    assert captured[0]["regionCode"] == "BR" and "relevanceLanguage" not in captured[0]
    bare: list[dict[str, str]] = []
    await YoutubeCollector(api_key="k", transport=_capture_search(bare)).discover(SEG, limit=5)
    assert "regionCode" not in bare[0] and "relevanceLanguage" not in bare[0]


@pytest.mark.asyncio
async def test_discover_by_keywords_accepts_locale() -> None:
    from content_factory.domain.models import SearchConfig

    captured: list[dict[str, str]] = []
    c = YoutubeCollector(api_key="k", transport=_capture_search(captured))
    await c.discover_by_keywords(
        ["vela"], [], 1, locale=SearchConfig(relevance_language="pt", region_code="BR")
    )
    assert captured[0]["relevanceLanguage"] == "pt" and captured[0]["regionCode"] == "BR"


def test_settings_default_search_wires_into_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Padrão global por env; vazio = sem restrição."""
    from content_factory.config import Settings
    from content_factory.infra.collectors import register_defaults, registry

    monkeypatch.setenv("YOUTUBE_RELEVANCE_LANGUAGE", "pt")
    monkeypatch.setenv("YOUTUBE_REGION_CODE", "BR")
    register_defaults(Settings())
    yt = registry.get("youtube")
    assert isinstance(yt, YoutubeCollector)
    assert yt.default_search is not None
    assert (yt.default_search.relevance_language, yt.default_search.region_code) == ("pt", "BR")
    monkeypatch.setenv("YOUTUBE_RELEVANCE_LANGUAGE", "")
    monkeypatch.setenv("YOUTUBE_REGION_CODE", "")
    register_defaults(Settings())
    assert registry.get("youtube").default_search is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_bind_segment_search_wraps_only_youtube_with_segment_locale() -> None:
    """T3: o research recebe o YouTube amarrado ao locale do segmento.

    O protocolo `SocialDiscoverer` não muda; sem locale no segmento, o mapa
    original volta intacto (mesmos objetos).
    """
    from content_factory.domain.models import SearchConfig
    from content_factory.infra.queue.composition import bind_segment_search

    captured: list[dict[str, str]] = []
    yt = YoutubeCollector(api_key="k", transport=_capture_search(captured))
    other = object()
    socials = {"youtube": yt, "instagram": other}
    assert bind_segment_search(socials, SEG) is socials  # type: ignore[arg-type]

    seg = SEG.model_copy(update={"search": SearchConfig(relevance_language="pt", region_code="BR")})
    bound = bind_segment_search(socials, seg)  # type: ignore[arg-type]
    assert bound["instagram"] is other
    assert bound["youtube"].platform == "youtube"
    await bound["youtube"].discover_by_keywords(["vela"], [], 1)
    assert captured[0]["relevanceLanguage"] == "pt" and captured[0]["regionCode"] == "BR"


@pytest.mark.asyncio
async def test_search_order_is_configurable_and_defaults_to_view_count() -> None:
    """Busca real 2026-10-15 (resina): com `order=viewCount` o YouTube ignora o
    `relevanceLanguage` (1/10 vídeos em PT); com `relevance`, 9/10. A ordem
    vira configurável; sem configuração continua `viewCount`."""
    from content_factory.domain.models import SearchConfig

    captured: list[dict[str, str]] = []
    c = YoutubeCollector(api_key="k", transport=_capture_search(captured))
    await c.discover(SEG, limit=5)
    seg = SEG.model_copy(
        update={
            "search": SearchConfig(relevance_language="pt", region_code="BR", order="relevance")
        }
    )
    await c.discover(seg, limit=5)
    assert captured[0]["order"] == "viewCount"
    assert captured[1]["order"] == "relevance"


def test_search_order_rejects_unknown_value() -> None:
    import pydantic

    from content_factory.domain.models import SearchConfig

    with pytest.raises(pydantic.ValidationError):
        SearchConfig(order="date-desc")  # type: ignore[arg-type]


def _lang_transport(langs: list[tuple[str, dict[str, str]]]) -> httpx.MockTransport:
    """search devolve os ids; videos devolve snippet com os campos de idioma dados."""

    def _route(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": v}} for v, _ in langs]})
        if path.endswith("/videos"):
            items = [
                {
                    "id": vid,
                    "snippet": {
                        "title": vid,
                        "channelId": "c1",
                        "channelTitle": "canal",
                        "publishedAt": "2026-10-15T10:00:00Z",
                        **extra,
                    },
                    "statistics": {"viewCount": "10"},
                    "contentDetails": {"duration": "PT1M"},
                }
                for vid, extra in langs
            ]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(200, json={"items": [{"id": "c1", "statistics": {}}]})

    return httpx.MockTransport(_route)


_MIXED: list[tuple[str, dict[str, str]]] = [
    ("pt", {"defaultAudioLanguage": "pt-BR"}),
    ("en", {"defaultAudioLanguage": "en-US"}),
    ("es", {"defaultLanguage": "es"}),
    ("pt_default_only", {"defaultLanguage": "pt"}),
    ("none", {}),
]


@pytest.mark.asyncio
async def test_language_mapped_from_audio_then_default_language() -> None:
    c = YoutubeCollector(api_key="k", transport=_lang_transport(_MIXED))
    out = {v.platform_video_id: v.language for v in await c.discover(SEG, limit=5)}
    assert out == {
        "pt": "pt-BR",
        "en": "en-US",
        "es": "es",
        "pt_default_only": "pt",
        "none": None,
    }


@pytest.mark.asyncio
async def test_relevance_language_drops_videos_declared_in_other_language() -> None:
    """T3: `relevanceLanguage` é só preferência na API; o declarado diferente sai.
    Sem idioma declarado o vídeo fica (não há evidência para descartar)."""
    from content_factory.domain.models import SearchConfig

    seg = SEG.model_copy(update={"search": SearchConfig(relevance_language="pt")})
    c = YoutubeCollector(api_key="k", transport=_lang_transport(_MIXED))
    kept = {v.platform_video_id for v in await c.discover(seg, limit=5)}
    assert kept == {"pt", "pt_default_only", "none"}
    by_kw = await c.discover_by_keywords(
        ["vela"], [], 1, locale=SearchConfig(relevance_language="pt-BR")
    )
    assert {v.platform_video_id for v in by_kw} == {"pt", "pt_default_only", "none"}


@pytest.mark.asyncio
async def test_without_relevance_language_nothing_is_dropped() -> None:
    c = YoutubeCollector(api_key="k", transport=_lang_transport(_MIXED))
    assert len(await c.discover(SEG, limit=5)) == 5


def _ranked_transport(
    ids: list[str], langs: dict[str, str], log: list[httpx.Request]
) -> httpx.MockTransport:
    """search devolve `ids` na ordem; videos declara idioma por vídeo (`langs`)."""

    def _route(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path.endswith("/search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": v}} for v in ids]})
        if path.endswith("/videos"):
            items = [
                {
                    "id": vid,
                    "snippet": {
                        "title": vid,
                        "channelId": "UC1",
                        "channelTitle": "canal",
                        "publishedAt": "2026-10-15T10:00:00Z",
                        **({"defaultAudioLanguage": langs[vid]} if vid in langs else {}),
                    },
                    "statistics": {"viewCount": "10"},
                    "contentDetails": {"duration": "PT1M"},
                }
                for vid in ids
            ]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(
            200, json={"items": [{"id": "UC1", "statistics": {"subscriberCount": "100"}}]}
        )

    return httpx.MockTransport(_route)


@pytest.mark.asyncio
async def test_search_term_atribui_termo_posicao_e_locale() -> None:
    """R6: 1 termo por busca; posição = ordem do search.list ANTES do filtro."""
    from content_factory.domain.models import SearchConfig, SearchQuery

    seen: list[httpx.Request] = []
    col = YoutubeCollector(
        api_key="k", transport=_ranked_transport(["v1", "v2", "v3"], {"v2": "en"}, seen)
    )
    seg = SEG.model_copy(
        update={
            "search": SearchConfig(relevance_language="pt", region_code="BR", order="relevance")
        }
    )
    q = SearchQuery(term="vela artesanal", origin="seed", query_id="p:0:0:seed")
    out = await col.search_term(seg, q, limit=25)
    # v2 é en → cai; v3 mantém a posição 3 (o filtro não renumera)
    assert [(d.candidate.platform_video_id, d.result_position) for d in out] == [
        ("v1", 1),
        ("v3", 3),
    ]
    assert all(
        d.term == "vela artesanal" and d.origin == "seed" and d.query_id == q.query_id for d in out
    )
    assert all(d.run_kind == "collect" for d in out)
    search_req = next(r for r in seen if r.url.path.endswith("/search"))
    assert search_req.url.params["relevanceLanguage"] == "pt"
    assert search_req.url.params["regionCode"] == "BR"
    assert search_req.url.params["order"] == "relevance"
    assert search_req.url.params["q"] == "vela artesanal"  # sem aspas de or_query


@pytest.mark.asyncio
async def test_search_term_preserva_hashtag() -> None:
    from content_factory.domain.models import SearchQuery

    seen: list[httpx.Request] = []
    col = YoutubeCollector(api_key="k", transport=_ranked_transport(["v1"], {}, seen))
    q = SearchQuery(term="#velaaromatica", origin="hashtag:youtube", query_id="p:0:3:h")
    await col.search_term(SEG, q, limit=25)
    assert (
        next(r for r in seen if r.url.path.endswith("/search")).url.params["q"] == "#velaaromatica"
    )


@pytest.mark.asyncio
async def test_channel_id_do_snippet_chega_no_candidato() -> None:
    """R8: o `channelId` que o YouTube já recebe e descartava vira vínculo."""
    seen: list[httpx.Request] = []
    col = YoutubeCollector(api_key="k", transport=_ranked_transport(["v1"], {}, seen))
    out = await col.discover(SEG, limit=5)
    assert [c.creator_platform_id for c in out] == ["UC1"]
    assert out[0].creator_followers == 100


def _video_item(vid: str, channel: str = "UC1") -> dict[str, Any]:
    return {
        "id": vid,
        "snippet": {
            "channelId": channel,
            "channelTitle": "canal",
            "title": f"vídeo {vid}",
            "description": "",
            "publishedAt": "2026-10-15T10:00:00Z",
            "thumbnails": {"default": {"url": "https://img/x.jpg"}},
        },
        "statistics": {"viewCount": "10", "likeCount": "1", "commentCount": "0"},
        "contentDetails": {"duration": "PT1M"},
    }


def _youtube_refresh_transport(
    returned: list[str], calls: list[str] | None = None
) -> httpx.MockTransport:
    """`videos.list` devolve só os ids de `returned` que foram PEDIDOS (`id=`)."""

    def _route(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        if request.url.path.endswith("/videos"):
            asked = (request.url.params.get("id") or "").split(",")
            return httpx.Response(
                200, json={"items": [_video_item(v) for v in asked if v in returned]}
            )
        return httpx.Response(
            200, json={"items": [{"id": "UC1", "statistics": {"subscriberCount": "100"}}]}
        )

    return httpx.MockTransport(_route)


def _collector_with_transport(transport: httpx.MockTransport) -> YoutubeCollector:
    return YoutubeCollector(api_key="k", transport=transport)


@pytest.mark.asyncio
async def test_refresh_lotes_de_50_e_detecta_removido() -> None:
    # transport devolve só "v2" no videos.list (v1 sumiu) e um channel
    col = _collector_with_transport(_youtube_refresh_transport(returned=["v2"]))
    out = await col.refresh(
        [
            RemediationTarget(video_id=1, platform="youtube", platform_video_id="v1"),
            RemediationTarget(video_id=2, platform="youtube", platform_video_id="v2"),
        ]
    )
    assert [c.platform_video_id for c in out.candidates] == ["v2"]
    assert out.missing_ids == ["v1"]


@pytest.mark.asyncio
async def test_refresh_quebra_em_lotes_de_50_e_conta_cota() -> None:
    calls: list[str] = []
    ids = [f"v{i}" for i in range(51)]
    col = _collector_with_transport(_youtube_refresh_transport(returned=ids, calls=calls))
    targets = [
        RemediationTarget(video_id=i, platform="youtube", platform_video_id=v)
        for i, v in enumerate(ids)
    ]
    with counting() as counters:
        out = await col.refresh(targets)
    assert [c.platform_video_id for c in out.candidates] == ids
    assert out.missing_ids == []
    assert sum(1 for p in calls if p.endswith("/videos")) == 2  # 50 + 1
    assert counters.quota_used == 4  # 2 lotes x (videos + channels)


@pytest.mark.asyncio
async def test_refresh_sem_items_levanta_upstream_changed() -> None:
    """Revisão pre-merge: `{"items": []}` NÃO é evidência de remoção.

    Sem isso o lote inteiro viraria `missing_ids` → `videos.removed_at` (o
    vídeo sai dos alvos por toda a janela) num sintoma clássico de contrato
    quebrado/quota estourada. Mesma regra do `_search_ranked` ("search sem
    items"): resposta sem items é anomalia de upstream, não remoção.
    """
    col = _collector_with_transport(_youtube_refresh_transport(returned=[]))
    with pytest.raises(UpstreamChanged) as exc:
        await col.refresh(
            [
                RemediationTarget(video_id=1, platform="youtube", platform_video_id="v1"),
                RemediationTarget(video_id=2, platform="youtube", platform_video_id="v2"),
            ]
        )
    assert exc.value.code == "upstream_changed"


@pytest.mark.asyncio
async def test_refresh_com_algum_item_marca_o_ausente_como_removido() -> None:
    """A detecção de removido continua: id ausente entre itens que voltaram.

    O lote com resposta VÁLIDA (≥ 1 item) é evidência: o id que não voltou
    saiu (removido/privado).
    """
    col = _collector_with_transport(_youtube_refresh_transport(returned=["v1", "v3"]))
    out = await col.refresh(
        [
            RemediationTarget(video_id=1, platform="youtube", platform_video_id="v1"),
            RemediationTarget(video_id=2, platform="youtube", platform_video_id="v2"),
            RemediationTarget(video_id=3, platform="youtube", platform_video_id="v3"),
        ]
    )
    assert [c.platform_video_id for c in out.candidates] == ["v1", "v3"]
    assert out.missing_ids == ["v2"]


@pytest.mark.asyncio
async def test_accepted_language_pt_br_descarta_pt_pt() -> None:
    """Público do teste é o Brasil: `accepted_language="pt-BR"` descarta o
    vídeo declarado `pt-PT` (12 de 27 na coleta real de 16/10). `pt` sem
    região e sem idioma declarado ficam: não há evidência de que não é BR."""
    from content_factory.domain.models import SearchConfig

    langs = [*_MIXED, ("pt_pt", {"defaultAudioLanguage": "pt-PT"})]
    seg = SEG.model_copy(
        update={"search": SearchConfig(relevance_language="pt", accepted_language="pt-BR")}
    )
    c = YoutubeCollector(api_key="k", transport=_lang_transport(langs))
    kept = {v.platform_video_id for v in await c.discover(seg, limit=6)}
    assert kept == {"pt", "pt_default_only", "none"}


@pytest.mark.asyncio
async def test_sem_accepted_language_pt_pt_continua_pelo_prefixo() -> None:
    """Sem `accepted_language`, vale o prefixo do `relevance_language` (T3)."""
    from content_factory.domain.models import SearchConfig

    langs = [*_MIXED, ("pt_pt", {"defaultAudioLanguage": "pt-PT"})]
    seg = SEG.model_copy(update={"search": SearchConfig(relevance_language="pt")})
    c = YoutubeCollector(api_key="k", transport=_lang_transport(langs))
    kept = {v.platform_video_id for v in await c.discover(seg, limit=6)}
    assert kept == {"pt", "pt_default_only", "none", "pt_pt"}


def test_accepted_language_conta_como_config_de_busca() -> None:
    from content_factory.domain.models import SearchConfig

    assert not SearchConfig(accepted_language="pt-BR").is_empty()
