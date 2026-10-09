from content_factory.config import Settings
from content_factory.infra.collectors.registry import register
from content_factory.infra.collectors.youtube import YoutubeCollector


def register_defaults(settings: Settings) -> None:
    register(YoutubeCollector(api_key=settings.youtube_api_key))
    # Task 13: register(TikTokCollector(...))
    # Task 14: register(InstagramCollector(...))
    # Task 15: register(WebCollector())
