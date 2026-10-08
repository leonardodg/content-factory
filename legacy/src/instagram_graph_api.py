import requests
from typing import List, Dict, Optional

class InstagramGraphAPI:
    """Client for Instagram Graph API"""

    BASE_URL = "https://graph.instagram.com"
    GRAPH_VERSION = "v21.0"

    def __init__(self, access_token: str, ig_user_id: str):
        """
        Initialize API client

        Args:
            access_token: Graph API access token
            ig_user_id: Instagram Business/Creator user ID
        """
        self.access_token = access_token
        self.ig_user_id = ig_user_id
        self.session = requests.Session()

    def _make_request(self, url: str, params: Dict) -> Dict:
        """Make API request with error handling"""
        params['access_token'] = self.access_token

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"⚠️ API Error: {str(e)}")
            return {}

    def get_media_list(self, limit: int = 50) -> List[str]:
        """Fetch list of media IDs from profile"""
        url = f"{self.BASE_URL}/{self.ig_user_id}/media"
        params = {
            'fields': 'id,media_type,media_product_type,timestamp',
            'limit': limit
        }

        data = self._make_request(url, params)

        # Filter only Reels (VIDEO with REELS product type)
        media_ids = [
            item['id'] for item in data.get('data', [])
            if item.get('media_product_type') == 'REELS'
        ]

        return media_ids

    def get_reel_details(self, media_id: str) -> Dict:
        """Fetch detailed information about a specific Reel"""
        # Get media metadata
        media_url = f"{self.BASE_URL}/{media_id}"
        media_params = {
            'fields': (
                'id,caption,timestamp,permalink,media_url,thumbnail_url,'
                'like_count,comments_count,owner,username,media_product_type,'
                'video_title'
            )
        }

        media_data = self._make_request(media_url, media_params)

        # Get insights (views, engagement)
        insights = self.get_media_insights(media_id)

        # Get audio information
        audio_info = self.get_audio_info(media_id)

        # Combine all data
        return {
            **media_data,
            **insights,
            **audio_info
        }

    def get_media_insights(self, media_id: str) -> Dict:
        """Fetch insights for a media item"""
        insights_url = f"{self.BASE_URL}/{media_id}/insights"
        insights_params = {
            'metric': 'plays,reach,total_interactions,likes,comments,shares,saves'
        }

        data = self._make_request(insights_url, insights_params)

        # Process insights
        insights_dict = {
            'views': 0,
            'reach': 0,
            'saves': 0,
            'shares': 0
        }

        for metric in data.get('data', []):
            metric_name = metric['name']
            metric_value = metric['values'][0]['value']

            if metric_name == 'plays':
                insights_dict['views'] = metric_value
            elif metric_name == 'reach':
                insights_dict['reach'] = metric_value
            elif metric_name == 'saves':
                insights_dict['saves'] = metric_value
            elif metric_name == 'shares':
                insights_dict['shares'] = metric_value

        return insights_dict

    def get_audio_info(self, media_id: str) -> Dict:
        """Get audio information from a Reel"""
        # Note: Audio info requires additional API calls
        # This is a simplified version
        url = f"{self.BASE_URL}/{media_id}"
        params = {
            'fields': 'music_metadata'
        }

        data = self._make_request(url, params)

        audio_metadata = data.get('music_metadata', {})

        return {
            'audio_name': audio_metadata.get('audio_name', 'Original Audio'),
            'audio_id': audio_metadata.get('audio_id', 'N/A'),
            'audio_url': audio_metadata.get('audio_url', 'N/A')
        }

    def get_profile_info(self, username: str) -> Dict:
        """Get profile information including follower count"""
        # Using business discovery to get profile info
        url = f"{self.BASE_URL}/{self.ig_user_id}"
        params = {
            'fields': f'business_discovery.username({username}){{followers_count,following_count,media_count,biography,profile_picture_url,username}}'
        }

        data = self._make_request(url, params)

        profile_data = data.get('business_discovery', {})

        return {
            'username': profile_data.get('username', username),
            'followers_count': profile_data.get('followers_count', 0),
            'following_count': profile_data.get('following_count', 0),
            'media_count': profile_data.get('media_count', 0),
            'biography': profile_data.get('biography', ''),
            'profile_picture_url': profile_data.get('profile_picture_url', '')
        }

    def search_hashtag_media(self, hashtag: str, limit: int = 50) -> List[str]:
        """
        Search media by hashtag (requires specific permissions)
        Alternative: use business discovery endpoints
        """
        hashtag_id = self._get_hashtag_id(hashtag)

        if not hashtag_id:
            return []

        url = f"{self.BASE_URL}/{hashtag_id}/recent_media"
        params = {
            'user_id': self.ig_user_id,
            'fields': 'id,media_type,media_product_type,timestamp',
            'limit': limit
        }

        data = self._make_request(url, params)

        return [
            item['id'] for item in data.get('data', [])
            if item.get('media_product_type') == 'REELS'
        ]

    def _get_hashtag_id(self, hashtag: str) -> Optional[str]:
        """Get hashtag ID for search"""
        url = f"{self.BASE_URL}/ig_hashtag_search"
        params = {
            'user_id': self.ig_user_id,
            'q': hashtag.replace('#', '')
        }

        data = self._make_request(url, params)

        hashtag_data = data.get('data', [])
        if hashtag_data:
            return hashtag_data[0]['id']
        return None
