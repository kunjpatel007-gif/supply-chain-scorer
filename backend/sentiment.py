"""
sentiment.py
------------
Review text -> VADER sentiment. Shared by the simulator and the review forms so
both write severity on the same 0-5 scale as the NLP-classified Olist reviews.
"""
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

analyzer = SentimentIntensityAnalyzer()


def compound(text):
    """VADER compound score, -1 (very negative) .. 1 (very positive)."""
    return analyzer.polarity_scores(text)['compound']


def severity(text):
    """Defect severity 0-5 from review text (negative sentiment only). None for empty text."""
    if not text or not text.strip():
        return None
    return round(max(0.0, -compound(text)) * 5.0, 2)
