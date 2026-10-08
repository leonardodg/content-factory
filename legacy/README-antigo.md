# 🤖 Complete Architecture - Instagram Viral Reels Analyzer

## 📋 Project Structure

```
instagram-viral-analyzer/
│
├── config/
│   ├── config.json              # Main configuration
│   ├── credentials.json         # API credentials (DO NOT commit!)
│   └── niches.json             # Niche definitions
│
├── src/
│   ├── __init__.py
│   ├── api_client.py           # Instagram Graph API client
│   ├── analyzer.py             # Viral analysis logic
│   ├── content_analyzer.py     # Content extraction (hashtags, keywords)
│   ├── agent.py                # Main orchestrator agent
│   └── utils.py                # Utilities
│
├── data/
│   ├── raw/                    # Raw collected data
│   ├── processed/              # Processed data
│   └── reports/                # Generated reports
│
├── scheduler/
│   ├── cron_setup.sh           # Cron configuration script
│   ├── run_analysis.py         # Scheduled execution script
│   └── continuous_server.py    # Continuous server
│
├── notebooks/
│   └── analysis.ipynb          # Exploratory analysis
│
├── logs/
│   ├── automation.log          # Automation logs
│   └── error.log               # Error logs
│
├── requirements.txt            # Dependencies
├── .env.example               # Environment variables example
├── .gitignore                 # Git ignore file
└── README.md                  # Documentation
```

---

## 🔧 Initial Setup

### 1. System Requirements

```bash
# Install dependencies
pip install requests pandas python-dotenv schedule apscheduler openpyxl

# Or use requirements.txt
pip install -r requirements.txt
```

**requirements.txt:**
```
requests==2.31.0
pandas==2.1.0
python-dotenv==1.0.0
schedule==1.2.0
apscheduler==3.10.4
openpyxl==3.1.2
plotly==5.17.0
python-dateutil==2.8.2
```

### 2. Getting Instagram Graph API Credentials

#### Step-by-Step Guide:

**Step 1: Create a Facebook App**
1. Go to: https://developers.facebook.com/
2. Create a new App
3. Add "Instagram Graph API" product

**Step 2: Configure Instagram Business Account**
- Your account must be Business or Creator type
- Connect to a Facebook Page
- Ensure you have admin access

**Step 3: Generate Access Token**
```
1. Use Graph API Explorer
2. Select permissions:
   ✓ instagram_basic
   ✓ instagram_manage_insights
   ✓ pages_read_engagement
   ✓ pages_show_list
   ✓ business_management
3. Generate long-lived token (60 days)
4. Consider implementing token refresh automation
```

**Step 4: Get Instagram User ID**
```bash
# Test in Graph API Explorer
GET /{facebook-page-id}?fields=instagram_business_account

# Response:
{
  "instagram_business_account": {
    "id": "17841400001234567"  # This is your IG User ID
  }
}
```

### 3. Environment Configuration

**.env:**
```env
# Instagram API Credentials
INSTAGRAM_ACCESS_TOKEN=EAABwzLixnjYBO...your_token_here
INSTAGRAM_USER_ID=17841400001234567

# Analysis Settings
NICHE=AI_and_Tech
ANALYSIS_DAYS=7
VIRAL_THRESHOLD=60.0
TOP_RESULTS_COUNT=10
MIN_VIEWS=1000
DATA_LIMIT=50

# Automation
RUN_SCHEDULE=daily  # Options: daily, weekly, hourly
SCHEDULE_TIME=09:00
TIMEZONE=UTC

# Storage
DATA_DIR=./data
REPORTS_DIR=./data/reports
EXPORT_FORMAT=csv,json  # Options: csv, json, excel

# Notifications (optional)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK
EMAIL_NOTIFICATIONS=true
EMAIL_RECIPIENTS=your.email@example.com

# Rate Limiting
API_RATE_LIMIT=200  # Requests per hour
REQUEST_DELAY=2  # Seconds between requests
```

### 4. Niche Configuration

**config/niches.json:**
```json
{
  "AI_and_Tech": {
    "keywords": [
      "artificial intelligence",
      "AI",
      "machine learning",
      "deep learning",
      "automation",
      "tech",
      "technology",
      "ChatGPT",
      "GPT",
      "neural networks",
      "data science",
      "innovation",
      "future tech"
    ],
    "hashtags": [
      "AI",
      "artificialintelligence",
      "machinelearning",
      "tech",
      "automation",
      "futuretech",
      "innovation",
      "technology"
    ],
    "target_accounts": [
      "openai",
      "nvidia",
      "huggingface",
      "googleai",
      "deepmind"
    ],
    "min_followers": 10000,
    "viral_threshold": 60.0
  },
  "Fitness": {
    "keywords": [
      "workout",
      "fitness",
      "gym",
      "training",
      "exercise",
      "muscle",
      "cardio",
      "strength",
      "bodybuilding",
      "crossfit",
      "yoga",
      "nutrition"
    ],
    "hashtags": [
      "fitness",
      "workout",
      "gym",
      "fitfam",
      "gymlife",
      "training"
    ],
    "target_accounts": [
      "kayla_itsines",
      "jefenoel",
      "whitneyysimmons"
    ],
    "min_followers": 5000,
    "viral_threshold": 55.0
  },
  "Finance": {
    "keywords": [
      "money",
      "investing",
      "stocks",
      "crypto",
      "bitcoin",
      "financial freedom",
      "passive income",
      "wealth",
      "trading",
      "real estate",
      "business"
    ],
    "hashtags": [
      "finance",
      "investing",
      "moneytips",
      "wealth",
      "passiveincome"
    ],
    "target_accounts": [
      "wallstreetbets",
      "investopedia"
    ],
    "min_followers": 15000,
    "viral_threshold": 65.0
  },
  "Custom_Niche": {
    "keywords": [
      "your_keyword_1",
      "your_keyword_2"
    ],
    "hashtags": [
      "yourhashtag1",
      "yourhashtag2"
    ],
    "target_accounts": [
      "account1",
      "account2"
    ],
    "min_followers": 1000,
    "viral_threshold": 50.0
  }
}
```

---

## ⚙️ Automation Scripts

### Script 1: Scheduled Execution

**scheduler/run_analysis.py:**
```python
import schedule
import time
import sys
import os
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import AutomatedAgent
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/automation.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class AnalysisScheduler:
    """Scheduler for automated analysis"""
    
    def __init__(self, config_path='config/config.json'):
        self.config_path = config_path
        self.agent = None
    
    def initialize_agent(self):
        """Initialize or reinitialize the agent"""
        try:
            self.agent = AutomatedAgent(self.config_path)
            logger.info("Agent initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize agent: {str(e)}")
            raise
    
    def run_daily_analysis(self, top_n=10):
        """Execute daily analysis"""
        logger.info("=" * 70)
        logger.info("Starting daily analysis...")
        
        try:
            if not self.agent:
                self.initialize_agent()
            
            # Run analysis
            report = self.agent.run_automated_analysis(top_n=top_n)
            
            logger.info(f"Analysis completed successfully")
            logger.info(f"Viral reels found: {report['viral_count']}")
            logger.info(f"Top performers analyzed: {len(report['top_performers'])}")
            
            # Send notifications
            self.send_notifications(report)
            
            return report
            
        except Exception as e:
            logger.error(f"Error during analysis: {str(e)}", exc_info=True)
            self.send_error_notification(str(e))
    
    def run_weekly_report(self, top_n=20):
        """Execute comprehensive weekly report"""
        logger.info("=" * 70)
        logger.info("Starting weekly comprehensive report...")
        
        try:
            if not self.agent:
                self.initialize_agent()
            
            # Update config for weekly analysis
            self.agent.config['analysis_days'] = 7
            
            report = self.agent.run_automated_analysis(top_n=top_n)
            
            # Generate additional insights
            self.generate_weekly_insights(report)
            
            logger.info("Weekly report completed")
            
            return report
            
        except Exception as e:
            logger.error(f"Error during weekly report: {str(e)}", exc_info=True)
    
    def send_notifications(self, report):
        """Send notifications with results"""
        try:
            # Slack notification
            if os.getenv('SLACK_WEBHOOK_URL'):
                self.send_slack_notification(report)
            
            # Email notification
            if os.getenv('EMAIL_NOTIFICATIONS') == 'true':
                self.send_email_notification(report)
            
            logger.info("Notifications sent successfully")
            
        except Exception as e:
            logger.error(f"Failed to send notifications: {str(e)}")
    
    def send_slack_notification(self, report):
        """Send Slack notification"""
        import requests
        
        webhook_url = os.getenv('SLACK_WEBHOOK_URL')
        
        message = {
            "text": f"🤖 Instagram Analysis Complete",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "📊 Viral Reels Analysis Report"
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Total Analyzed:*\n{report['total_analyzed']}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Viral Found:*\n{report['viral_count']}"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Avg Engagement:*\n{report['avg_engagement_rate']}%"
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Avg Viral Score:*\n{report['avg_viral_score']}"
                        }
                    ]
                }
            ]
        }
        
        if report['top_performers']:
            top = report['top_performers'][0]
            message["blocks"].append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🏆 Top Performer:*\n@{top['username']} - {top['views']:,} views\n<{top['permalink']}|View Reel>"
                }
            })
        
        requests.post(webhook_url, json=message)
    
    def send_email_notification(self, report):
        """Send email notification"""
        # Implement email sending logic
        # Using smtplib or SendGrid API
        pass
    
    def send_error_notification(self, error_message):
        """Send error notification"""
        logger.error(f"Sending error notification: {error_message}")
        # Implement error notification logic
    
    def generate_weekly_insights(self, report):
        """Generate additional weekly insights"""
        logger.info("Generating weekly insights...")
        
        # Create visualizations, trend analysis, etc.
        # Export detailed Excel report
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.agent.export_results(
            f"data/reports/weekly_report_{timestamp}.xlsx",
            format='excel',
            top_n=20
        )


def main():
    """Main scheduler execution"""
    
    # Initialize scheduler
    scheduler = AnalysisScheduler('config/config.json')
    
    # Get configuration
    schedule_time = os.getenv('SCHEDULE_TIME', '09:00')
    top_results = int(os.getenv('TOP_RESULTS_COUNT', '10'))
    
    # Schedule daily analysis
    schedule.every().day.at(schedule_time).do(
        scheduler.run_daily_analysis,
        top_n=top_results
    )
    
    # Schedule weekly report (every Monday at 8 AM)
    schedule.every().monday.at("08:00").do(
        scheduler.run_weekly_report,
        top_n=top_results * 2
    )
    
    logger.info("🤖 Scheduler started successfully")
    logger.info(f"Daily analysis scheduled at: {schedule_time}")
    logger.info(f"Weekly report scheduled: Monday at 08:00")
    logger.info(f"Top results to show: {top_results}")
    logger.info(f"Next run: {schedule.next_run()}")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
```

### Script 2: Cron Job Setup (Linux/Mac)

**scheduler/cron_setup.sh:**
```bash
#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Instagram Viral Analyzer - Cron Setup${NC}"
echo "========================================"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_PATH=$(which python3)

echo -e "\n${YELLOW}Configuration:${NC}"
echo "Project Directory: $PROJECT_DIR"
echo "Python Path: $PYTHON_PATH"

# Create logs directory
mkdir -p "$PROJECT_DIR/logs"

# Backup existing crontab
echo -e "\n${YELLOW}Backing up existing crontab...${NC}"
crontab -l > "$PROJECT_DIR/crontab_backup_$(date +%Y%m%d_%H%M%S).txt" 2>/dev/null

# Daily analysis at 9 AM
DAILY_CRON="0 9 * * * cd $PROJECT_DIR && $PYTHON_PATH scheduler/run_analysis.py >> logs/cron.log 2>&1"

# Weekly report on Monday at 8 AM
WEEKLY_CRON="0 8 * * 1 cd $PROJECT_DIR && $PYTHON_PATH scheduler/weekly_report.py >> logs/weekly_cron.log 2>&1"

# Add to crontab
echo -e "\n${YELLOW}Adding cron jobs...${NC}"
(crontab -l 2>/dev/null | grep -v "run_analysis.py" | grep -v "weekly_report.py"; echo "$DAILY_CRON") | crontab -
(crontab -l 2>/dev/null; echo "$WEEKLY_CRON") | crontab -

echo -e "\n${GREEN}✓ Cron jobs configured successfully!${NC}"
echo -e "\nScheduled jobs:"
echo "  • Daily Analysis: Every day at 9:00 AM"
echo "  • Weekly Report: Every Monday at 8:00 AM"

echo -e "\n${YELLOW}Current crontab:${NC}"
crontab -l

echo -e "\n${YELLOW}Useful commands:${NC}"
echo "  • View cron jobs: crontab -l"
echo "  • Edit cron jobs: crontab -e"
echo "  • Remove all cron jobs: crontab -r"
echo "  • View logs: tail -f $PROJECT_DIR/logs/cron.log"

echo -e "\n${GREEN}Setup complete!${NC}"
```

### Script 3: Continuous Server (APScheduler)

**scheduler/continuous_server.py:**
```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import AutomatedAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/server.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Initialize scheduler
scheduler = BlockingScheduler()

# Initialize agent
agent = None

def initialize_agent():
    """Initialize the agent"""
    global agent
    try:
        agent = AutomatedAgent('config/config.json')
        logger.info("Agent initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize agent: {str(e)}")
        raise

@scheduler.scheduled_job(CronTrigger(hour=9, minute=0))
def daily_analysis():
    """Daily analysis at 9 AM"""
    logger.info("Starting daily analysis job...")
    try:
        if not agent:
            initialize_agent()
        
        report = agent.run_automated_analysis(top_n=10)
        logger.info(f"Daily analysis completed. Viral reels: {report['viral_count']}")
    except Exception as e:
        logger.error(f"Daily analysis failed: {str(e)}", exc_info=True)

@scheduler.scheduled_job(CronTrigger(day_of_week='mon', hour=8, minute=0))
def weekly_report():
    """Weekly comprehensive report on Monday at 8 AM"""
    logger.info("Starting weekly comprehensive report...")
    try:
        if not agent:
            initialize_agent()
        
        agent.config['analysis_days'] = 7
        report = agent.run_automated_analysis(top_n=20)
        logger.info(f"Weekly report completed. Total analyzed: {report['total_analyzed']}")
    except Exception as e:
        logger.error(f"Weekly report failed: {str(e)}", exc_info=True)

@scheduler.scheduled_job(IntervalTrigger(hours=12))
def health_check():
    """Health check every 12 hours"""
    logger.info("Performing health check...")
    try:
        if not agent:
            initialize_agent()
        logger.info("Health check passed ✓")
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")

def main():
    """Start the continuous server"""
    logger.info("=" * 70)
    logger.info("Instagram Viral Analyzer - Continuous Server")
    logger.info("=" * 70)
    
    # Initialize agent
    try:
        initialize_agent()
    except Exception as e:
        logger.error("Failed to start server. Exiting...")
        sys.exit(1)
    
    # Display schedule
    logger.info("\nScheduled Jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  • {job.name}: {job.trigger}")
    
    logger.info("\n🚀 Server started successfully!")
    logger.info("Press Ctrl+C to stop\n")
    
    # Start scheduler
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\nShutting down server...")
        scheduler.shutdown()
        logger.info("Server stopped")

if __name__ == "__main__":
    main()
```

---

## 📊 Workflow Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    AUTOMATED AGENT WORKFLOW                   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  1. DATA COLLECTION (Instagram Graph API)                    │
│     ✓ Fetch last N Reels                                    │
│     ✓ Extract metrics (views, likes, comments, shares,saves) │
│     ✓ Get creator profile (followers, bio, profile pic)     │
│     ✓ Extract audio information (name, URL)                 │
│     ✓ Parse hashtags and mentions                           │
│     ✓ Filter by niche keywords                              │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  2. CONTENT ANALYSIS                                          │
│     ✓ Extract hashtags from captions                        │
│     ✓ Extract @mentions                                      │
│     ✓ Identify niche keywords                               │
│     ✓ Clean and format captions                             │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  3. ENGAGEMENT ANALYSIS                                       │
│     ✓ Calculate weighted engagement rate                    │
│     ✓ Compute viral score (0-100)                           │
│     ✓ Classify viral level                                  │
│     ✓ Calculate reach ratio (views/followers)               │
│     ✓ Measure velocity (time-based performance)             │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  4. VIRAL IDENTIFICATION                                      │
│     ✓ Filter by viral threshold (configurable)              │
│     ✓ Sort by performance metrics                           │
│     ✓ Select top N performers (configurable)                │
│     ✓ Extract patterns from top content                     │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  5. INSIGHTS GENERATION                                       │
│     ✓ Generate comprehensive report                         │
│     ✓ Identify audio trends                                 │
│     ✓ Analyze hashtag patterns                              │
│     ✓ Rank top creators                                     │
│     ✓ Extract common success patterns                       │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  6. EXPORT & NOTIFICATION                                     │
│     ✓ Save to CSV/JSON/Excel                                │
│     ✓ Send Slack notifications                              │
│     ✓ Send email reports                                    │
│     ✓ Update dashboards                                     │
│     ✓ Log results                                           │
└──────────────────────────────────────────────────────────────┘
```

---

## 🎯 Enhanced Metrics & Formulas

### 1. Weighted Engagement Rate
```
ER = [(Likes × 1) + (Comments × 2) + (Shares × 3) + (Saves × 4)] / Views × 100

Weights Rationale:
• Likes (1x): Basic engagement
• Comments (2x): Active conversation
• Shares (3x): Organic reach multiplier
• Saves (4x): Highest value intent
```

### 2. Viral Score (0-100)
```
VS = Σ (Component_Score × Weight)

Components:
• Views Score (20%): Views / 1M × 100
• Engagement Rate (25%): ER × 10
• Shares Score (20%): Shares / 10K × 100
• Saves Score (15%): Saves / 5K × 100
• Velocity (10%): Time-based performance
• Reach Ratio (10%): (Views / Followers) × 100
```

### 3. Viral Classification
```
90-100: 🔥 SUPER VIRAL    (Exceptional performance)
70-89:  🚀 VIRAL          (High viral potential)
60-69:  ⭐ HIGH ENGAGEMENT (Strong performance)
40-59:  ✓ GOOD            (Above average)
0-39:   - NORMAL          (Standard performance)
```

### 4. Reach Ratio
```
Reach Ratio = (Views / Followers) × 100

Interpretation:
• >300%: Excellent viral reach
• 100-300%: Good organic spread
• 50-100%: Average reach
• <50%: Limited reach beyond followers
```

---

## 📈 Enhanced Report Example

```
╔═════════════════════════════════════════════════════════════╗
║          INSTAGRAM VIRAL REELS ANALYSIS REPORT               ║
╚═════════════════════════════════════════════════════════════╝

📅 Analysis Period: Last 7 days
🕐 Generated: 2024-11-06 14:30:00
🎯 Niche: AI & Technology
🔝 Top Results Shown: 15

═════════════════════════════════════════════════════════════

📊 SUMMARY STATISTICS

Total Reels Analyzed: 50
Viral Reels Found: 12 (24%)
Average Engagement Rate: 7.8%
Average Viral Score: 52.3
Highest Viral Score: 96.8

═════════════════════════════════════════════════════════════

🏆 TOP 15 PERFORMERS

1️⃣ 🔥 SUPER VIRAL (Score: 96.8)
────────────────────────────────────────────────────────────
👤 Creator Information:
   • Username: @ai_tech_daily
   • Profile: https://instagram.com/ai_tech_daily
   • Followers: 245,000
   • Bio: "AI & Tech trends explained simply"
   • Profile Picture: [URL]

📝 Content:
   • Caption: "5 AI tools that will change your life in 2024..."
   • Full Caption: [Available in export]
   • Posted: 2024-11-03 08:15:00

📊 Performance Metrics:
   • Views: 1,850,000
   • Likes: 125,000
   • Comments: 3,200
   • Shares: 8,500
   • Saves: 12,300
   • Reach: 1,650,000
   • Engagement Rate: 13.2%
   • Reach Ratio: 755% (7.5x followers!)

🎵 Audio:
   • Name: "Epic Tech Music - Trending"
   • Audio ID: 12345678
   • URL: https://instagram.com/audio/12345678

🔗 Links:
   • Reel: https://instagram.com/reel/ABC123
   • Video File: https://scontent.cdninstagram.com/...
   • Thumbnail: https://scontent.cdninstagram.com/...

#️⃣  Hashtags (5 used):
   #AI #ArtificialIntelligence #TechTools #Innovation #FutureTech

🔑 Keywords Found:
   AI, artificial intelligence, automation, tech

───────────────────────────────────────────────────────────

2️⃣ 🔥 SUPER VIRAL (Score: 94.2)
────────────────────────────────────────────────────────────
[Similar detailed information for rank 2]

───────────────────────────────────────────────────────────

... [Continues for all 15 results] ...

═════════════════════════════════════════════════════════════

🔍 COMMON PATTERNS IN VIRAL CONTENT

Performance Benchmarks:
• Average Views: 985,000
• Average Likes: 68,500
• Average Comments: 1,850
• Average Shares: 4,200
• Average Saves: 6,100
• Average Engagement Rate: 11.3%
• Average Creator Followers: 185,000

Content Characteristics:
• Optimal Duration: 15-25 seconds
• Best Posting Time: 08:00-10:00 (35% of virals)
• Common Format: Tutorial/Listicle
• Hook Strategy: Question in first 3 seconds

═════════════════════════════════════════════════════════════

🎵 TRENDING AUDIO (Top 5)

1. Epic Tech Music - Trending (8 viral reels)
2. Original Audio - @tech_expert (5 viral reels)
3. Motivational Beat #42 (4 viral reels)
4. Future Bass Drop (3 viral reels)
5. Tech Vlog Music (3 viral reels)

Insight: 67% of viral content uses trending audio

═════════════════════════════════════════════════════════════

#️⃣  TRENDING HASHTAGS (Top 10)

1. #AI (15 times)
2. #ArtificialIntelligence (12 times)
3. #TechTools (10 times)
4. #Innovation (9 times)
5. #FutureTech (8 times)
6. #Automation (7 times)
7. #MachineLearning (6 times)
8. #TechTrends (5 times)
9. #DigitalTransformation (5 times)
10. #TechTips (4 times)

═════════════════════════════════════════════════════════════

👥 TOP CREATORS (By Viral Performance)

1. @ai_tech_daily
   • Profile: https://instagram.com/ai_tech_daily
   • Followers: 245,000
   • Avg Viral Score: 92.5
   • Total Views: 4.2M
   • Viral Reels: 4

2. @future_tech_hub
   • Profile: https://instagram.com/future_tech_hub
   • Followers: 180,000
   • Avg Viral Score: 88.3
   • Total Views: 3.1M
   • Viral Reels: 3

3. @tech_simplified
   • Profile: https://instagram.com/tech_simplified
   • Followers: 150,000
   • Avg Viral Score: 85.7
   • Total Views: 2.8M
   • Viral Reels: 3

4. @ai_revolution
   • Profile: https://instagram.com/ai_revolution
   • Followers: 120,000
   • Avg Viral Score: 81.2
   • Total Views: 1.9M
   • Viral Reels: 2

5. @innovation_daily
   • Profile: https://instagram.com/innovation_daily
   • Followers: 95,000
   • Avg Viral Score: 78.5
   • Total Views: 1.5M
   • Viral Reels: 2

═════════════════════════════════════════════════════════════

💡 ACTIONABLE RECOMMENDATIONS

Content Strategy:
✓ Focus on tutorial/educational format (70% of virals)
✓ Use trending audio tracks (increases viral chance by 67%)
✓ Include numbers in titles ("5 ways", "3 tips")
✓ Create strong hook in first 3 seconds
✓ Optimal video length: 15-25 seconds
✓ End with clear call-to-action

Posting Strategy:
✓ Best times: 08:00-10:00 or 18:00-20:00
✓ Post consistently (3-5x per week)
✓ Use 5-8 relevant hashtags
✓ Engage with comments within first hour

Growth Opportunities:
✓ Collaborate with top performers in your niche
✓ Study audio trends weekly
✓ A/B test different hooks
✓ Analyze competitor strategies

═════════════════════════════════════════════════════════════

📁 EXPORTED FILES

✓ viral_analysis.csv - Detailed metrics spreadsheet
✓ viral_analysis.json - Complete report data
✓ viral_analysis.xlsx - Excel report with charts

═════════════════════════════════════════════════════════════
```

---

## 🚀 Quick Start Guide

### Basic Usage

```python
from src.agent import AutomatedAgent

# 1. Initialize agent
agent = AutomatedAgent('config/config.json')

# 2. Run complete analysis (default top 10)
report = agent.run_automated_analysis()

# 3. Get custom number of results
report = agent.run_automated_analysis(top_n=25)

# 4. View viral reels dataframe
viral_reels = agent.identify_viral_reels(top_n=15)
print(viral_reels[['username', 'views', 'viral_score', 'permalink']])

# 5. Generate detailed report
report = agent.generate_insights_report(top_n=20)

# 6. Export results in different formats
agent.export_results('results.csv', format='csv', top_n=10)
agent.export_results('report.json', format='json', top_n=15)
agent.export_results('analysis.xlsx', format='excel', top_n=20)
```

### Advanced Configuration

```python
# Custom configuration
config = {
    'access_token': 'YOUR_TOKEN',
    'ig_user_id': 'YOUR_ID',
    'niche_keywords': ['AI', 'machine learning', 'tech'],
    'analysis_days': 14,          # Analyze last 14 days
    'viral_threshold': 70.0,      # Higher threshold
    'top_results_count': 25,      # Show top 25
    'min_views': 5000,            # Minimum 5k views
    'data_limit': 100             # Fetch 100 reels
}

# Save and use
import json
with open('custom_config.json', 'w') as f:
    json.dump(config, f, indent=2)

agent = AutomatedAgent('custom_config.json')
report = agent.run_automated_analysis(top_n=25)
```

### CLI Usage

**Create a CLI script (cli.py):**

```python
#!/usr/bin/env python3
import argparse
import sys
from src.agent import AutomatedAgent

def main():
    parser = argparse.ArgumentParser(
        description='Instagram Viral Reels Analyzer'
    )
    
    parser.add_argument(
        '--config',
        default='config/config.json',
        help='Path to configuration file'
    )
    
    parser.add_argument(
        '--top',
        type=int,
        default=10,
        help='Number of top results to show'
    )
    
    parser.add_argument(
        '--niche',
        help='Override niche from config'
    )
    
    parser.add_argument(
        '--days',
        type=int,
        help='Number of days to analyze'
    )
    
    parser.add_argument(
        '--threshold',
        type=float,
        help='Viral score threshold (0-100)'
    )
    
    parser.add_argument(
        '--export',
        choices=['csv', 'json', 'excel', 'all'],
        default='all',
        help='Export format'
    )
    
    parser.add_argument(
        '--output',
        default='viral_analysis',
        help='Output filename (without extension)'
    )
    
    args = parser.parse_args()
    
    # Initialize agent
    try:
        agent = AutomatedAgent(args.config)
        
        # Override config if specified
        if args.niche:
            agent.config['niche'] = args.niche
        if args.days:
            agent.config['analysis_days'] = args.days
        if args.threshold:
            agent.config['viral_threshold'] = args.threshold
        
        # Run analysis
        print(f"\n🤖 Running analysis for top {args.top} results...")
        report = agent.run_automated_analysis(top_n=args.top)
        
        # Export results
        if args.export in ['csv', 'all']:
            agent.export_results(f"{args.output}.csv", 'csv', args.top)
        if args.export in ['json', 'all']:
            agent.export_results(f"{args.output}.json", 'json', args.top)
        if args.export in ['excel', 'all']:
            agent.export_results(f"{args.output}.xlsx", 'excel', args.top)
        
        print(f"\n✅ Analysis complete! Found {report['viral_count']} viral reels")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
```

**Usage examples:**

```bash
# Basic usage
python cli.py

# Custom top results
python cli.py --top 25

# Analyze specific period
python cli.py --days 14 --threshold 75

# Export only JSON
python cli.py --export json --output my_report

# Full customization
python cli.py --config custom.json --top 30 --days 7 --threshold 65 --export all
```

---

## 🐳 Docker Deployment

**Dockerfile:**

```dockerfile
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p logs data/reports

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30m --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from src.agent import AutomatedAgent; agent = AutomatedAgent('config/config.json')" || exit 1

# Run the scheduler
CMD ["python", "scheduler/continuous_server.py"]
```

**docker-compose.yml:**

```yaml
version: '3.8'

services:
  analyzer:
    build: .
    container_name: instagram_analyzer
    restart: unless-stopped
    volumes:
      - ./config:/app/config:ro
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - TZ=UTC
      - PYTHONUNBUFFERED=1
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
      interval: 30m
      timeout: 10s
      retries: 3
      start_period: 5s
```

**Build and run:**

```bash
# Build image
docker build -t instagram-analyzer .

# Run with docker-compose
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Restart
docker-compose restart
```

---

## 📊 Advanced Analytics Dashboard

**Optional: Create a Streamlit dashboard**

**dashboard.py:**

```python
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from src.agent import AutomatedAgent
import json
from datetime import datetime

st.set_page_config(
    page_title="Instagram Viral Analyzer",
    page_icon="🔥",
    layout="wide"
)

# Title
st.title("🔥 Instagram Viral Reels Analyzer")
st.markdown("Real-time analytics for your niche")

# Sidebar configuration
st.sidebar.header("Configuration")
top_n = st.sidebar.slider("Top Results", 5, 50, 10)
days = st.sidebar.slider("Analysis Period (days)", 1, 30, 7)
threshold = st.sidebar.slider("Viral Threshold", 0, 100, 60)

if st.sidebar.button("🚀 Run Analysis", type="primary"):
    with st.spinner("Analyzing reels..."):
        # Initialize agent
        agent = AutomatedAgent('config/config.json')
        agent.config['analysis_days'] = days
        agent.config['viral_threshold'] = threshold
        
        # Run analysis
        report = agent.run_automated_analysis(top_n=top_n)
        viral_reels = agent.identify_viral_reels(top_n=top_n)
        
        # Store in session state
        st.session_state['report'] = report
        st.session_state['viral_reels'] = viral_reels

# Display results if available
if 'report' in st.session_state:
    report = st.session_state['report']
    viral_reels = st.session_state['viral_reels']
    
    # Metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Analyzed", report['total_analyzed'])
    with col2:
        st.metric("Viral Found", report['viral_count'])
    with col3:
        st.metric("Avg Engagement", f"{report['avg_engagement_rate']}%")
    with col4:
        st.metric("Avg Viral Score", report['avg_viral_score'])
    
    # Top performers
    st.header("🏆 Top Performers")
    
    for performer in report['top_performers'][:5]:
        with st.expander(f"#{performer['rank']} @{performer['username']} - {performer['viral_level']}"):
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.markdown(f"**Caption:** {performer['caption']}")
                st.markdown(f"**Posted:** {performer['posted_date']}")
                st.markdown(f"**Hashtags:** {', '.join(['#'+h for h in performer['hashtags'][:5]])}")
                st.markdown(f"[View Reel]({performer['permalink']}) | [Profile](https://instagram.com/{performer['username']})")
            
            with col2:
                st.metric("Views", f"{performer['views']:,}")
                st.metric("Engagement Rate", f"{performer['engagement_rate']}%")
                st.metric("Viral Score", performer['viral_score'])
    
    # Charts
    st.header("📊 Analytics")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Views distribution
        fig = px.bar(
            viral_reels.head(10),
            x='username',
            y='views',
            title='Top 10 by Views',
            labels={'views': 'Views', 'username': 'Creator'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        # Viral score distribution
        fig = px.scatter(
            viral_reels,
            x='engagement_rate',
            y='viral_score',
            size='views',
            hover_data=['username'],
            title='Engagement vs Viral Score',
            labels={'engagement_rate': 'Engagement Rate (%)', 'viral_score': 'Viral Score'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # Hashtag analysis
    if report['hashtag_trends']:
        st.header("#️⃣ Hashtag Trends")
        hashtag_df = pd.DataFrame(
            list(report['hashtag_trends'].items()),
            columns=['Hashtag', 'Count']
        )
        fig = px.bar(
            hashtag_df.head(10),
            x='Hashtag',
            y='Count',
            title='Top 10 Hashtags'
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # Download data
    st.header("📥 Export Data")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        csv = viral_reels.to_csv(index=False)
        st.download_button(
            "Download CSV",
            csv,
            "viral_reels.csv",
            "text/csv"
        )
    
    with col2:
        json_str = json.dumps(report, indent=2, default=str)
        st.download_button(
            "Download JSON",
            json_str,
            "report.json",
            "application/json"
        )
    
    with col3:
        excel_buffer = viral_reels.to_excel(index=False)
        st.download_button(
            "Download Excel",
            excel_buffer,
            "viral_reels.xlsx",
            "application/vnd.ms-excel"
        )

else:
    st.info("👈 Configure settings and click 'Run Analysis' to start")
```

**Run dashboard:**

```bash
pip install streamlit plotly
streamlit run dashboard.py
```

---

## 🔐 Security Best Practices

### 1. Credentials Management

```bash
# Never commit credentials
echo "config/credentials.json" >> .gitignore
echo ".env" >> .gitignore
echo "*.log" >> .gitignore
echo "data/" >> .gitignore
```

### 2. Environment Variables

```python
# Use environment variables instead of hardcoding
import os
from dotenv import load_dotenv

load_dotenv()

config = {
    'access_token': os.getenv('INSTAGRAM_ACCESS_TOKEN'),
    'ig_user_id': os.getenv('INSTAGRAM_USER_ID'),
    # ... other config
}
```

### 3. Token Rotation

```python
# Implement automatic token refresh
def refresh_access_token(app_id, app_secret, current_token):
    """Refresh long-lived access token"""
    url = "https://graph.instagram.com/refresh_access_token"
    params = {
        'grant_type': 'ig_refresh_token',
        'access_token': current_token
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    return data.get('access_token')
```

### 4. Rate Limiting

```python
import time
from functools import wraps

def rate_limit(max_calls=200, period=3600):
    """Rate limiter decorator"""
    calls = []
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            now = time.time()
            # Remove old calls
            calls[:] = [c for c in calls if c > now - period]
            
            if len(calls) >= max_calls:
                sleep_time = period - (now - calls[0])
                print(f"Rate limit reached. Sleeping for {sleep_time:.0f}s")
                time.sleep(sleep_time)
            
            calls.append(time.time())
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

---

## 🛠️ Troubleshooting

### Common Issues

**1. API Token Expired**
```
Error: OAuthException - Error validating access token
Solution: Regenerate access token from Facebook Developer Portal
```

**2. Permission Errors**
```
Error: Insufficient permissions
Solution: Ensure all required permissions are granted:
  - instagram_basic
  - instagram_manage_insights
  - pages_read_engagement
```

**3. Rate Limiting**
```
Error: Rate limit exceeded
Solution: Implement request delays and use rate limiting
```

**4. No Data Returned**
```
Issue: 0 reels found
Solution: 
  - Check niche keywords are relevant
  - Verify account is Business/Creator type
  - Ensure Reels have sufficient age (not too recent)
```

---

## 📚 API Reference

### AutomatedAgent Methods

```python
class AutomatedAgent:
    
    def __init__(self, config_path: str)
        """Initialize agent with configuration"""
    
    def collect_reels_data(self, limit: int = 50) -> List[ReelMetrics]
        """Collect reels data from API"""
    
    def identify_viral_reels(self, top_n: Optional[int] = None) -> pd.DataFrame
        """Identify viral reels and return top N"""
    
    def generate_insights_report(self, top_n: Optional[int] = None) -> Dict
        """Generate comprehensive insights report"""
    
    def export_results(self, filename: str, format: str = "csv", top_n: Optional[int] = None)
        """Export results to file
        
        Args:
            filename: Output filename
            format: 'csv', 'json', or 'excel'
            top_n: Number of top results to export
        """
    
    def run_automated_analysis(self, top_n: Optional[int] = None) -> Dict
        """Run complete automated analysis
        
        Args:
            top_n: Number of top results to analyze and show
            
        Returns:
            Complete analysis report dictionary
        """
```

### Configuration Parameters

```python
{
    # Required
    "access_token": str,              # Instagram API token
    "ig_user_id": str,                # Instagram Business Account ID
    
    # Analysis Settings
    "niche_keywords": List[str],      # Keywords for filtering
    "analysis_days": int,             # Days to analyze (default: 7)
    "viral_threshold": float,         # Min score for viral (default: 60.0)
    "top_results_count": int,         # Default top results (default: 10)
    "min_views": int,                 # Minimum views threshold (default: 1000)
    "data_limit": int                 # Max reels to fetch (default: 50)
}
```

---

## 🎓 Advanced Usage Examples

### Example 1: Multi-Niche Analysis

```python
niches = {
    'AI_Tech': ['AI', 'machine learning', 'automation'],
    'Fitness': ['workout', 'gym', 'fitness'],
    'Finance': ['investing', 'crypto', 'money']
}

results = {}

for niche_name, keywords in niches.items():
    print(f"\n🔍 Analyzing {niche_name}...")
    
    # Update config
    agent.config['niche_keywords'] = keywords
    
    # Run analysis
    report = agent.run_automated_analysis(top_n=5)
    results[niche_name] = report
    
    print(f"✓ Found {report['viral_count']} viral reels")

# Compare results
for niche, report in results.items():
    print(f"\n{niche}:")
    print(f"  Avg Viral Score: {report['avg_viral_score']}")
    print(f"  Avg Engagement: {report['avg_engagement_rate']}%")
```

### Example 2: Trend Tracking Over Time

```python
from datetime import datetime, timedelta

# Analyze different time periods
periods = [7, 14, 30]
trend_data = []

for days in periods:
    agent.config['analysis_days'] = days
    report = agent.run_automated_analysis(top_n=10)
    
    trend_data.append({
        'period': f'{days} days',
        'viral_count': report['viral_count'],
        'avg_score': report['avg_viral_score'],
        'avg_engagement': report['avg_engagement_rate']
    })

# Visualize trends
import pandas as pd
df_trends = pd.DataFrame(trend_data)
print(df_trends)
```

### Example 3: Competitor Analysis

```python
competitors = [
    '@competitor1',
    '@competitor2',
    '@competitor3'
]

competitor_data = []

for username in competitors:
    # Get profile info
    profile = agent.api.get_profile_info(username)
    
    # Would need to implement fetching their reels
    # This is a simplified example
    
    competitor_data.append({
        'username': username,
        'followers': profile['followers_count'],
        'media_count': profile['media_count']
    })

df_competitors = pd.DataFrame(competitor_data)
print(df_competitors)
```

---

## 🔄 Continuous Improvement

### Metrics to Track

1. **API Usage**
   - Requests per day
   - Token refresh frequency
   - Error rates

2. **Analysis Performance**
   - Processing time
   - Viral detection accuracy
   - False positive rate

3. **Business Metrics**
   - Reels analyzed per week
   - Trends identified
   - Recommendations implemented

### Logging Enhancement

```python
import logging
from logging.handlers import RotatingFileHandler

# Advanced logging setup
def setup_logging():
    logger = logging.getLogger('instagram_analyzer')
    logger.setLevel(logging.DEBUG)
    
    # Rotating file handler
    file_handler = RotatingFileHandler(
        'logs/app.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger
```

---

## 🎉 Complete Setup Checklist

- [ ] Install Python 3.9+
- [ ] Install dependencies (`pip install -r requirements.txt`)
- [ ] Create Facebook/Instagram Business account
- [ ] Generate API access token
- [ ] Configure `config/config.json`
- [ ] Set up `.env` file
- [ ] Define niche keywords
- [ ] Test API connection
- [ ] Run first analysis manually
- [ ] Set up automation (cron/scheduler)
- [ ] Configure notifications (optional)
- [ ] Set up monitoring/logging
- [ ] Document your workflow

**You're now ready to automate viral content discovery! 🚀**