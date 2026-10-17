from content_factory.config import Settings
from content_factory.domain.models import SearchConfig
from content_factory.infra.collectors.instagram import InstagramCollector
from content_factory.infra.collectors.registry import register
from content_factory.infra.collectors.tiktok import TikTokCollector
from content_factory.infra.collectors.web import WebCollector
from content_factory.infra.collectors.web_queries import FileWebQueries
from content_factory.infra.collectors.youtube import YoutubeCollector


def register_defaults(settings: Settings) -> None:
    default_search = SearchConfig(
        relevance_language=settings.youtube_relevance_language or None,
        region_code=settings.youtube_region_code or None,
        order=settings.youtube_search_order or None,  # pyright: ignore[reportArgumentType]
    )
    register(
        YoutubeCollector(
            api_key=settings.youtube_api_key,
            default_search=None if default_search.is_empty() else default_search,
        )
    )
    register(TikTokCollector(ms_token=settings.tiktok_ms_token, proxy=settings.tiktok_proxy))
    register(InstagramCollector(settings.instagram_access_token, settings.ig_user_id))
    # protocolo `q1` por segmento; sem arquivo, o coletor cai no legado — ou
    # falha explicitamente quando WEB_QUERIES_REQUIRED=1
    register(WebCollector(queries=FileWebQueries(), required=settings.web_queries_required))
