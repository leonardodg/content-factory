from content_factory.config import Settings
from content_factory.infra.collectors.registry import register
from content_factory.infra.collectors.tiktok import TikTokCollector
from content_factory.infra.collectors.youtube import YoutubeCollector


def register_defaults(settings: Settings) -> None:
    register(YoutubeCollector(api_key=settings.youtube_api_key))
    register(TikTokCollector(ms_token=settings.tiktok_ms_token, proxy=settings.tiktok_proxy))
    # Task 14: register(InstagramCollector(...))
    # Task 15: register(WebCollector())
