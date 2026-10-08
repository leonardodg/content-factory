from typing import List
import re

class ContentAnalyzer:
    """Analyzes content for keywords, hashtags, and patterns"""

    @staticmethod
    def extract_hashtags(text: str) -> List[str]:
        """Extract hashtags from text"""
        if not text:
            return []
        return re.findall(r'#(\w+)', text)

    @staticmethod
    def extract_mentions(text: str) -> List[str]:
        """Extract mentions from text"""
        if not text:
            return []
        return re.findall(r'@(\w+)', text)

    @staticmethod
    def find_keywords(text: str, keywords: List[str]) -> List[str]:
        """Find matching keywords in text"""
        if not text:
            return []

        text_lower = text.lower()
        found = []

        for keyword in keywords:
            if keyword.lower() in text_lower:
                found.append(keyword)

        return found

    @staticmethod
    def clean_caption(caption: str, max_length: int = 100) -> str:
        """Clean and truncate caption for preview"""
        if not caption:
            return ""

        # Remove extra whitespace
        caption = ' '.join(caption.split())

        # Truncate if too long
        if len(caption) > max_length:
            return caption[:max_length] + "..."

        return caption
