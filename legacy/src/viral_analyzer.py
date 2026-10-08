from typing import List, Dict
from datetime import datetime
from content_analyzer import ContentAnalyzer

class ViralAnalyzer:
    """Analyzes viral content patterns"""

    def __init__(self, niche_keywords: List[str], config: Dict = None): # type: ignore
        """
        Args:
            niche_keywords: Keywords to filter content by niche
            config: Configuration for analysis thresholds
        """
        self.niche_keywords = niche_keywords
        self.config = config or {}
        self.content_analyzer = ContentAnalyzer()

    def calculate_engagement_rate(self, likes: int, comments: int,
                                  shares: int, saves: int, views: int) -> float:
        """Calculate engagement rate with weighted metrics"""
        if views == 0:
            return 0.0

        # Weighted engagement
        total_engagement = (
            likes * 1 +      # Standard engagement
            comments * 2 +   # Higher value (conversation)
            shares * 3 +     # Organic reach multiplier
            saves * 4        # Highest intent (value)
        )

        return round((total_engagement / views) * 100, 2)

    def calculate_viral_score(self, reel_data: Dict) -> float:
        """
        Calculate viral score (0-100) based on multiple factors
        """
        views = reel_data.get('views', 0)
        likes = reel_data.get('like_count', 0)
        comments = reel_data.get('comments_count', 0)
        shares = reel_data.get('shares', 0)
        saves = reel_data.get('saves', 0)
        followers = reel_data.get('followers_count', 1)

        # Weights for each component
        weights = {
            'views': 0.20,
            'engagement_rate': 0.25,
            'shares': 0.20,
            'saves': 0.15,
            'velocity': 0.10,
            'reach_ratio': 0.10  # Views relative to followers
        }

        # 1. Views Score (normalized to 1M+ views = 100)
        views_score = min((views / 1_000_000) * 100, 100)

        # 2. Engagement Rate Score
        eng_rate = self.calculate_engagement_rate(likes, comments, shares, saves, views)
        eng_score = min(eng_rate * 10, 100)  # 10%+ is excellent

        # 3. Shares Score (10k+ is viral)
        shares_score = min((shares / 10_000) * 100, 100)

        # 4. Saves Score (5k+ is viral)
        saves_score = min((saves / 5_000) * 100, 100)

        # 5. Velocity Score (engagement in first 24-48h)
        velocity_score = self._calculate_velocity(reel_data)

        # 6. Reach Ratio (views relative to follower count)
        reach_ratio = (views / max(followers, 1)) * 100
        reach_score = min(reach_ratio, 100)

        # Calculate weighted final score
        viral_score = (
            views_score * weights['views'] +
            eng_score * weights['engagement_rate'] +
            shares_score * weights['shares'] +
            saves_score * weights['saves'] +
            velocity_score * weights['velocity'] +
            reach_score * weights['reach_ratio']
        )

        return round(viral_score, 2)

    def _calculate_velocity(self, reel_data: Dict) -> float:
        """Calculate viralization velocity"""
        timestamp_str = reel_data.get('timestamp', '')
        if not timestamp_str:
            return 50.0

        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            hours_since_post = (datetime.now(timestamp.tzinfo) - timestamp).total_seconds() / 3600

            if hours_since_post <= 24:
                return 100  # Very recent, high score
            elif hours_since_post <= 48:
                return 90
            elif hours_since_post <= 168:  # 1 week
                return 70
            elif hours_since_post <= 336:  # 2 weeks
                return 50
            else:
                return 30
        except:
            return 50.0

    def is_viral(self, viral_score: float, threshold: float = 60.0) -> bool:
        """Determine if a reel is viral based on score"""
        return viral_score >= threshold

    def classify_viral_level(self, viral_score: float) -> str:
        """Classify viral level"""
        if viral_score >= 90:
            return "🔥 SUPER VIRAL"
        elif viral_score >= 70:
            return "🚀 VIRAL"
        elif viral_score >= 60:
            return "⭐ HIGH ENGAGEMENT"
        elif viral_score >= 40:
            return "✓ GOOD PERFORMANCE"
        else:
            return "- NORMAL"

    def filter_by_niche(self, caption: str, hashtags: List[str] = None) -> bool: # type: ignore
        """Filter content by niche using keywords and hashtags"""
        if not caption and not hashtags:
            return False

        # Check caption
        if caption:
            caption_lower = caption.lower()
            if any(keyword.lower() in caption_lower for keyword in self.niche_keywords):
                return True

        # Check hashtags
        if hashtags:
            hashtags_lower = [h.lower() for h in hashtags]
            if any(keyword.lower() in hashtags_lower for keyword in self.niche_keywords):
                return True

        return False
