"""
app/services/ai/sentiment.py
─────────────────────────────
Sentiment analysis for work-log descriptions.

Implements the EXACT discrete scoring logic from perform_crm_df.py (§1.1):

    def sentiment_score_discrete(text):
        polarity = TextBlob(str(text)).sentiment.polarity
        if polarity == 1:   return 100
        elif polarity == 0: return 60
        else:               return 40

This three-tier mapping is intentional per the documentation:
    Perfect positive sentiment (polarity = 1.0) → 100
    Completely neutral (polarity = 0.0)          → 60
    Any other value (partial positive/negative)  → 40

The employee-level sentiment score is the AVERAGE of all log descriptions
for the given month.
"""

from textblob import TextBlob

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def sentiment_score_discrete(text: str) -> float:
    """
    Compute a discrete 3-tier sentiment score from a single log description.

    Reference: perform_crm_df.py sentiment_score_discrete()

    Args:
        text: Log description string.

    Returns:
        100 if polarity == 1.0 (perfect positive)
         60 if polarity == 0.0 (neutral)
         40 otherwise (partial positive or any negative)
    """
    try:
        polarity: float = TextBlob(str(text)).sentiment.polarity
    except Exception:
        return 60.0  # treat failures as neutral

    if polarity == 1.0:
        return 100.0
    elif polarity == 0.0:
        return 60.0
    else:
        return 40.0


def compute_employee_sentiment_score(descriptions: list[str]) -> tuple[float, float]:
    """
    Compute the aggregate sentiment score for an employee's work-log descriptions.

    Args:
        descriptions: List of log description strings for the month.

    Returns:
        Tuple of (average_sentiment_score, average_polarity).
        Returns (60.0, 0.0) if no descriptions are provided.
    """
    if not descriptions:
        logger.warning("sentiment_no_descriptions")
        return 60.0, 0.0

    scores: list[float] = []
    polarities: list[float] = []

    for desc in descriptions:
        if not desc or not str(desc).strip():
            # Empty description → neutral
            scores.append(60.0)
            polarities.append(0.0)
            continue

        try:
            polarity = TextBlob(str(desc)).sentiment.polarity
            polarities.append(polarity)
            scores.append(sentiment_score_discrete(desc))
        except Exception as exc:
            logger.warning("sentiment_parse_error", error=str(exc))
            scores.append(60.0)
            polarities.append(0.0)

    avg_score = sum(scores) / len(scores) if scores else 60.0
    avg_polarity = sum(polarities) / len(polarities) if polarities else 0.0

    return round(avg_score, 2), round(avg_polarity, 4)
