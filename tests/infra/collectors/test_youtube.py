import httpx
import pytest

from content_factory.app.call_counters import counting
from content_factory.domain.models import SegmentConfig
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
