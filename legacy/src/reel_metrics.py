from dataclasses import dataclass
from typing import List

@dataclass
class ReelMetrics:
    """Data structure for Reel metrics"""
    reel_id: str
    timestamp: str
    caption: str
    full_caption: str
    views: int
    likes: int
    comments: int
    shares: int
    saves: int
    reach: int
    audio_name: str
    audio_id: str
    audio_url: str
    engagement_rate: float
    viral_score: float
    permalink: str
    media_url: str
    thumbnail_url: str
    # Profile information
    username: str
    profile_picture_url: str
    followers_count: int
    following_count: int
    media_count: int
    biography: str
    # Content analysis
    hashtags: List[str]
    mentions: List[str]
    keywords_found: List[str]
    video_duration: int
