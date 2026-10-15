from content_factory.config import Settings
from content_factory.domain.models import SearchLocale
from content_factory.infra.collectors.instagram import InstagramCollector
from content_factory.infra.collectors.registry import register
from content_factory.infra.collectors.tiktok import TikTokCollector
from content_factory.infra.collectors.web import WebCollector
from content_factory.infra.collectors.youtube import YoutubeCollector


def register_defaults(settings: Settings) -> None:
    default_locale = SearchLocale(
        relevance_language=settings.youtube_relevance_language or None,
        region_code=settings.youtube_region_code or None,
    )
    register(
        YoutubeCollector(
            api_key=settings.youtube_api_key,
            default_locale=None if default_locale.is_empty() else default_locale,
        )
    )
    register(TikTokCollector(ms_token=settings.tiktok_ms_token, proxy=settings.tiktok_proxy))
    register(InstagramCollector(settings.instagram_access_token, settings.ig_user_id))
    register(WebCollector())
