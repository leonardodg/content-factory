"""
Instagram Viral Reels Analyzer - Automated Agent
=================================================
Analyzes Reels from a specific niche and identifies viral content
"""
import json
from automated_agent import AutomatedAgent

# ============================================================================
# EXAMPLE USAGE
# ============================================================================

def main() -> None:
    """Main Function - Example usage of the agent"""

    # 1. Create configuration file
    config = {
        'access_token': 'YOUR_ACCESS_TOKEN_HERE',
        'ig_user_id': 'YOUR_IG_USER_ID_HERE',
        'niche_keywords': [
            'AI',
            'artificial intelligence',
            'automation',
            'tech',
            'ChatGPT',
            'machine learning'
        ],
        'analysis_days': 7,
        'viral_threshold': 60.0,
        'top_results_count': 10,  # Configurable number of top results
        'min_views': 1000,
        'data_limit': 50
    }

    with open('config.json', 'w') as f:
        json.dump(config, f, indent=2)

    print("Configuration file created: config.json")
    print("\n" + "=" * 70)
    print("CONFIGURATION GUIDE")
    print("=" * 70)
    print("\nParameters you can configure:")
    print("• access_token: Your Instagram Graph API token")
    print("• ig_user_id: Your Instagram Business Account ID")
    print("• niche_keywords: Keywords to filter your niche")
    print("• analysis_days: Number of days to analyze (default: 7)")
    print("• viral_threshold: Minimum score to consider viral (0-100)")
    print("• top_results_count: Number of top results to show (default: 10)")
    print("• min_views: Minimum views to consider (default: 1000)")
    print("• data_limit: Maximum reels to fetch (default: 50)")

    # 2. Initialize agent
    print("\n" + "=" * 70)
    print("INITIALIZING AGENT")
    print("=" * 70)
    agent = AutomatedAgent('config.json')

    # 3. Run automated analysis with custom top_n
    print("\n" + "=" * 70)
    print("RUNNING ANALYSIS")
    print("=" * 70)

    # Example: Get top 15 results
    report = agent.run_automated_analysis(top_n=15)

    # 4. Additional analysis options
    print("\n" + "=" * 70)
    print("ADDITIONAL ANALYSIS OPTIONS")
    print("=" * 70)

    print("\n1. Get specific number of top performers:")
    print("   top_10 = agent.identify_viral_reels(top_n=10)")
    print("   top_20 = agent.identify_viral_reels(top_n=20)")

    print("\n2. Export with specific format:")
    print("   agent.export_results('results.csv', format='csv', top_n=15)")
    print("   agent.export_results('report.json', format='json', top_n=20)")
    print("   agent.export_results('data.xlsx', format='excel', top_n=25)")

    print("\n3. Get detailed report for specific count:")
    print("   report = agent.generate_insights_report(top_n=30)")

    return report # type: ignore


if __name__ == "__main__":
    print("=" * 70)
    print("INSTAGRAM VIRAL REELS ANALYZER - AUTOMATED AGENT")
    print("=" * 70)
    print("\nThis script analyzes Instagram Reels to identify viral content.")
    print("\nFeatures:")
    print("✓ Fetches Reels from Instagram Graph API")
    print("✓ Extracts comprehensive metrics (views, likes, comments, shares, saves)")
    print("✓ Analyzes creator profiles (followers, bio, profile picture)")
    print("✓ Identifies audio trends and hashtags")
    print("✓ Calculates viral scores with advanced algorithms")
    print("✓ Generates detailed reports with configurable top N results")
    print("✓ Exports to CSV, JSON, and Excel formats")

    print("\n" + "=" * 70)
    print("QUICK START")
    print("=" * 70)
    print("\n1. Configure your credentials in config.json")
    print("2. Set your niche keywords")
    print("3. Run: agent = AutomatedAgent('config.json')")
    print("4. Run: agent.run_automated_analysis(top_n=10)")
    print("\nYou can change top_n to any number to get more/fewer results!")

    # Uncomment to run
    # main()
