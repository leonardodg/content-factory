from typing import List, Dict, Optional
import json
from datetime import datetime, timedelta

from content_analyzer import ContentAnalyzer
from reel_metrics import ReelMetrics
from viral_analyzer import ViralAnalyzer
from instagram_graph_api import InstagramGraphAPI

import pandas as pd
from dataclasses import asdict
import os

class AutomatedAgent:
    """Main agent that orchestrates the analysis"""

    def __init__(self, config_path: str = "config.json"):
        """Load agent configuration"""
        self.config = self._load_config(config_path)
        self.api = InstagramGraphAPI(
            self.config['access_token'],
            self.config['ig_user_id']
        )
        self.analyzer = ViralAnalyzer(
            self.config['niche_keywords'],
            self.config
        )
        self.content_analyzer = ContentAnalyzer()
        self.data_store = []

    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from JSON file"""
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)

        # Default configuration
        return {
            'access_token': 'YOUR_ACCESS_TOKEN',
            'ig_user_id': 'YOUR_IG_USER_ID',
            'niche_keywords': ['AI', 'artificial intelligence', 'tech', 'automation'],
            'analysis_days': 7,
            'viral_threshold': 60.0,
            'top_results_count': 10,
            'min_views': 1000
        }

    def collect_reels_data(self, limit: int = 50) -> List[ReelMetrics]:
        """Collect Reels data with enhanced information"""
        print(f"🔍 Fetching last {limit} Reels...")

        media_ids = self.api.get_media_list(limit)
        reels_data = []

        for i, media_id in enumerate(media_ids, 1):
            try:
                print(f"📊 Analyzing Reel {i}/{len(media_ids)}...", end='\r')

                # Get reel details
                reel_data = self.api.get_reel_details(media_id)

                if not reel_data:
                    continue

                # Extract caption and content
                caption = reel_data.get('caption', '')
                hashtags = self.content_analyzer.extract_hashtags(caption)
                mentions = self.content_analyzer.extract_mentions(caption)

                # Filter by niche
                keywords_found = self.content_analyzer.find_keywords(
                    caption,
                    self.config.get('niche_keywords', [])
                )

                if not self.analyzer.filter_by_niche(caption, hashtags):
                    continue

                # Get profile information
                username = reel_data.get('username', 'unknown')
                profile_info = self.api.get_profile_info(username)

                # Calculate metrics
                views = reel_data.get('views', 0)
                likes = reel_data.get('like_count', 0)
                comments = reel_data.get('comments_count', 0)
                shares = reel_data.get('shares', 0)
                saves = reel_data.get('saves', 0)

                # Skip if below minimum views threshold
                if views < self.config.get('min_views', 1000):
                    continue

                engagement_rate = self.analyzer.calculate_engagement_rate(
                    likes, comments, shares, saves, views
                )

                # Add profile info to reel data for viral score calculation
                reel_data_with_profile = {
                    **reel_data,
                    **profile_info
                }

                viral_score = self.analyzer.calculate_viral_score(reel_data_with_profile)

                # Create metrics object
                metrics = ReelMetrics(
                    reel_id=media_id,
                    timestamp=reel_data.get('timestamp', ''),
                    caption=self.content_analyzer.clean_caption(caption, 100),
                    full_caption=caption,
                    views=views,
                    likes=likes,
                    comments=comments,
                    shares=shares,
                    saves=saves,
                    reach=reel_data.get('reach', 0),
                    audio_name=reel_data.get('audio_name', 'Original Audio'),
                    audio_id=reel_data.get('audio_id', 'N/A'),
                    audio_url=reel_data.get('audio_url', 'N/A'),
                    engagement_rate=engagement_rate,
                    viral_score=viral_score,
                    permalink=reel_data.get('permalink', ''),
                    media_url=reel_data.get('media_url', ''),
                    thumbnail_url=reel_data.get('thumbnail_url', ''),
                    username=username,
                    profile_picture_url=profile_info.get('profile_picture_url', ''),
                    followers_count=profile_info.get('followers_count', 0),
                    following_count=profile_info.get('following_count', 0),
                    media_count=profile_info.get('media_count', 0),
                    biography=profile_info.get('biography', ''),
                    hashtags=hashtags,
                    mentions=mentions,
                    keywords_found=keywords_found,
                    video_duration=0  # Would require additional API call
                )

                reels_data.append(metrics)

            except Exception as e:
                print(f"\n⚠️ Error processing {media_id}: {str(e)}")
                continue

        print(f"\n✅ Collected {len(reels_data)} reels matching criteria")
        self.data_store = reels_data
        return reels_data

    def identify_viral_reels(self, top_n: Optional[int] = None) -> pd.DataFrame:
        """Identify viral Reels from the collected data"""
        if not self.data_store:
            raise ValueError("No data collected. Run collect_reels_data() first.")

        # Convert to DataFrame
        df = pd.DataFrame([asdict(reel) for reel in self.data_store])

        # Filter by date (last N days)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        cutoff_date = datetime.now() - timedelta(days=self.config['analysis_days'])
        df_recent = df[df['timestamp'] >= cutoff_date]

        # Filter viral content
        df_viral = df_recent[
            df_recent['viral_score'] >= self.config['viral_threshold']
        ]

        # Sort by viral score
        df_viral = df_viral.sort_values('viral_score', ascending=False)

        # Limit to top N results if specified
        if top_n is None:
            top_n = self.config.get('top_results_count', 10)

        return df_viral.head(top_n)

    def generate_insights_report(self, top_n: Optional[int] = None) -> Dict:
        """Generate comprehensive insights report"""
        if not self.data_store:
            raise ValueError("No data collected.")

        df = pd.DataFrame([asdict(reel) for reel in self.data_store])
        viral_reels = self.identify_viral_reels(top_n)

        if top_n is None:
            top_n = self.config.get('top_results_count', 10)

        # Ensure top_n is an int for DataFrame.head()
        n_top = int(top_n) # type: ignore

        report = {
            'period': f"Last {self.config['analysis_days']} days",
            'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_analyzed': len(df),
            'viral_count': len(viral_reels),
            'avg_engagement_rate': round(df['engagement_rate'].mean(), 2),
            'avg_viral_score': round(df['viral_score'].mean(), 2),
            'top_performers': [],
            'common_patterns': {},
            'audio_trends': {},
            'hashtag_trends': {},
            'top_creators': []
        }

        if len(viral_reels) > 0:
            # Top performers
            for idx, row in viral_reels.head(n_top).iterrows():
                report['top_performers'].append({
                    'rank': len(report['top_performers']) + 1,
                    'reel_id': row['reel_id'],
                    'caption': row['caption'],
                    'full_caption': row['full_caption'],
                    'views': int(row['views']),
                    'likes': int(row['likes']),
                    'comments': int(row['comments']),
                    'shares': int(row['shares']),
                    'saves': int(row['saves']),
                    'engagement_rate': round(row['engagement_rate'], 2),
                    'viral_score': round(row['viral_score'], 2),
                    'viral_level': self.analyzer.classify_viral_level(row['viral_score']),
                    'permalink': row['permalink'],
                    'media_url': row['media_url'],
                    'thumbnail_url': row['thumbnail_url'],
                    'username': row['username'],
                    'profile_url': f"https://instagram.com/{row['username']}",
                    'followers_count': int(row['followers_count']),
                    'audio_name': row['audio_name'],
                    'audio_url': row['audio_url'],
                    'hashtags': row['hashtags'],
                    'keywords': row['keywords_found'],
                    'posted_date': row['timestamp'].strftime('%Y-%m-%d %H:%M')
                })

            # Common patterns in viral content
            report['common_patterns'] = {
                'avg_views': int(viral_reels['views'].mean()),
                'avg_likes': int(viral_reels['likes'].mean()),
                'avg_comments': int(viral_reels['comments'].mean()),
                'avg_shares': int(viral_reels['shares'].mean()),
                'avg_saves': int(viral_reels['saves'].mean()),
                'avg_engagement_rate': round(viral_reels['engagement_rate'].mean(), 2),
                'avg_follower_count': int(viral_reels['followers_count'].mean())
            }

            # Audio trends
            audio_counts = {}
            for audio in viral_reels['audio_name']:
                if audio and audio != 'N/A':
                    audio_counts[audio] = audio_counts.get(audio, 0) + 1

            report['audio_trends'] = dict(
                sorted(audio_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            )

            # Hashtag trends
            hashtag_counts = {}
            for hashtags in viral_reels['hashtags']:
                for tag in hashtags:
                    hashtag_counts[tag] = hashtag_counts.get(tag, 0) + 1

            report['hashtag_trends'] = dict(
                sorted(hashtag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            )

            # Top creators (by viral score)
            creator_stats = viral_reels.groupby('username').agg({
                'viral_score': 'mean',
                'followers_count': 'first',
                'views': 'sum',
                'reel_id': 'count'
            }).round(2)

            creator_stats = creator_stats.sort_values('viral_score', ascending=False).head(5)

            report['top_creators'] = [
                {
                    'username': username,
                    'profile_url': f"https://instagram.com/{username}",
                    'avg_viral_score': float(row['viral_score']),
                    'followers_count': int(row['followers_count']),
                    'total_views': int(row['views']),
                    'viral_reels_count': int(row['reel_id'])
                }
                for username, row in creator_stats.iterrows()
            ]

        return report

    def export_results(self, filename: str = "viral_analysis.csv",
                      format: str = "csv", top_n: Optional[int] = None):
        """Export results to file"""
        viral_reels = self.identify_viral_reels(top_n)

        if format == "csv":
            viral_reels.to_csv(filename, index=False)
            print(f"✅ Results exported to {filename}")

        elif format == "json":
            report = self.generate_insights_report(top_n)
            with open(filename.replace('.csv', '.json'), 'w') as f:
                json.dump(report, f, indent=2, default=str)
            print(f"✅ Report exported to {filename.replace('.csv', '.json')}")

        elif format == "excel":
            with pd.ExcelWriter(filename.replace('.csv', '.xlsx'), engine='openpyxl') as writer:
                viral_reels.to_excel(writer, sheet_name='Viral Reels', index=False)
            print(f"✅ Results exported to {filename.replace('.csv', '.xlsx')}")

    def run_automated_analysis(self, top_n: Optional[int] = None):
        """Run complete automated analysis"""
        print("🤖 Starting automated analysis...")
        print("=" * 70)

        # 1. Collect data
        self.collect_reels_data(limit=self.config.get('data_limit', 50))

        # 2. Identify viral content
        if top_n is None:
            top_n = self.config.get('top_results_count', 10)

        viral_reels = self.identify_viral_reels(top_n)

        # 3. Generate report
        report = self.generate_insights_report(top_n)

        # 4. Display results
        self._print_report(report, top_n) # type: ignore

        # 5. Export
        self.export_results(format="csv", top_n=top_n)
        self.export_results(format="json", top_n=top_n)

        print("\n✅ Analysis completed!")

        return report

    def _print_report(self, report: Dict, top_n: int):
        """Print formatted report to console"""
        print("\n" + "=" * 70)
        print("📈 VIRAL ANALYSIS REPORT")
        print("=" * 70)
        print(f"📅 Period: {report['period']}")
        print(f"🕐 Analysis Date: {report['analysis_date']}")
        print(f"📊 Total Analyzed: {report['total_analyzed']} Reels")
        print(f"🔥 Viral Reels Found: {report['viral_count']}")
        print(f"💹 Avg Engagement Rate: {report['avg_engagement_rate']}%")
        print(f"⭐ Avg Viral Score: {report['avg_viral_score']}")

        if report['top_performers']:
            print("\n" + "=" * 70)
            print(f"🏆 TOP {min(top_n, len(report['top_performers']))} PERFORMERS")
            print("=" * 70)

            for performer in report['top_performers'][:top_n]:
                print(f"\n#{performer['rank']} {performer['viral_level']}")
                print("-" * 70)
                print(f"👤 Creator: @{performer['username']}")
                print(f"   Profile: {performer['profile_url']}")
                print(f"   Followers: {performer['followers_count']:,}")
                print(f"\n📝 Caption: {performer['caption']}")
                print(f"\n📊 Metrics:")
                print(f"   Views: {performer['views']:,}")
                print(f"   Likes: {performer['likes']:,}")
                print(f"   Comments: {performer['comments']:,}")
                print(f"   Shares: {performer['shares']:,}")
                print(f"   Saves: {performer['saves']:,}")
                print(f"   Engagement Rate: {performer['engagement_rate']}%")
                print(f"   Viral Score: {performer['viral_score']}")
                print(f"\n🎵 Audio: {performer['audio_name']}")
                if performer['audio_url'] != 'N/A':
                    print(f"   Audio URL: {performer['audio_url']}")
                print(f"\n🔗 Links:")
                print(f"   Reel: {performer['permalink']}")
                print(f"   Video: {performer['media_url']}")
                if performer['hashtags']:
                    print(f"\n#️⃣  Hashtags: {', '.join(['#' + h for h in performer['hashtags'][:5]])}")
                if performer['keywords']:
                    print(f"🔑 Keywords: {', '.join(performer['keywords'])}")
                print(f"📅 Posted: {performer['posted_date']}")

            # Common patterns
            print("\n" + "=" * 70)
            print("🔍 COMMON PATTERNS IN VIRAL CONTENT")
            print("=" * 70)
            patterns = report['common_patterns']
            print(f"Average Views: {patterns['avg_views']:,}")
            print(f"Average Likes: {patterns['avg_likes']:,}")
            print(f"Average Comments: {patterns['avg_comments']:,}")
            print(f"Average Shares: {patterns['avg_shares']:,}")
            print(f"Average Saves: {patterns['avg_saves']:,}")
            print(f"Average Engagement Rate: {patterns['avg_engagement_rate']}%")
            print(f"Average Creator Followers: {patterns['avg_follower_count']:,}")

            # Audio trends
            if report['audio_trends']:
                print("\n" + "=" * 70)
                print("🎵 TRENDING AUDIO")
                print("=" * 70)
                for i, (audio, count) in enumerate(report['audio_trends'].items(), 1):
                    print(f"{i}. {audio} ({count} viral reels)")

            # Hashtag trends
            if report['hashtag_trends']:
                print("\n" + "=" * 70)
                print("#️⃣  TRENDING HASHTAGS")
                print("=" * 70)
                for i, (hashtag, count) in enumerate(report['hashtag_trends'].items(), 1):
                    print(f"{i}. #{hashtag} ({count} times)")

            # Top creators
            if report['top_creators']:
                print("\n" + "=" * 70)
                print("👥 TOP CREATORS")
                print("=" * 70)
                for i, creator in enumerate(report['top_creators'], 1):
                    print(f"\n{i}. @{creator['username']}")
                    print(f"   Profile: {creator['profile_url']}")
                    print(f"   Followers: {creator['followers_count']:,}")
                    print(f"   Avg Viral Score: {creator['avg_viral_score']}")
                    print(f"   Total Views: {creator['total_views']:,}")
                    print(f"   Viral Reels: {creator['viral_reels_count']}")
