"""
tests/unit/test_sentiment.py
──────────────────────────────
Unit tests for discrete sentiment scoring (TextBlob-based).
"""

import pytest
from unittest.mock import patch

from app.services.ai.sentiment import (
    compute_employee_sentiment_score,
    sentiment_score_discrete,
)


class TestSentimentScoreDiscrete:
    """Test 3-tier discrete mapping."""

    def test_positive_polarity_maps_to_100(self):
        # polarity == 1.0 → 100
        with patch("app.services.ai.sentiment.TextBlob") as mock_tb:
            mock_tb.return_value.sentiment.polarity = 1.0
            assert sentiment_score_discrete("excellent work") == 100.0

    def test_neutral_polarity_maps_to_60(self):
        # polarity == 0.0 → 60
        with patch("app.services.ai.sentiment.TextBlob") as mock_tb:
            mock_tb.return_value.sentiment.polarity = 0.0
            assert sentiment_score_discrete("did the task") == 60.0

    def test_negative_polarity_maps_to_40(self):
        # polarity < 0 → 40
        with patch("app.services.ai.sentiment.TextBlob") as mock_tb:
            mock_tb.return_value.sentiment.polarity = -0.5
            assert sentiment_score_discrete("poor quality") == 40.0

    def test_partial_positive_polarity_maps_to_40(self):
        # polarity between 0 and 1 (exclusive) → 40
        with patch("app.services.ai.sentiment.TextBlob") as mock_tb:
            mock_tb.return_value.sentiment.polarity = 0.5
            assert sentiment_score_discrete("okay work") == 40.0


class TestComputeEmployeeSentimentScore:
    """Test aggregation of sentiment scores over descriptions."""

    def test_empty_descriptions_returns_60(self):
        avg_score, avg_polarity = compute_employee_sentiment_score([])
        assert avg_score == 60.0
        assert avg_polarity == 0.0

    def test_single_positive_description(self):
        with patch("app.services.ai.sentiment.TextBlob") as mock_tb:
            mock_tb.return_value.sentiment.polarity = 1.0
            avg_score, avg_polarity = compute_employee_sentiment_score(["great job"])
            assert avg_score == 100.0
            assert avg_polarity == 1.0

    def test_averaging_multiple_descriptions(self):
        """Two descriptions: one polarity=1 (score 100), one polarity=0 (score 60) → avg=80."""
        polarities = [1.0, 0.0]
        call_count = 0

        class FakeBlob:
            def __init__(self, text):
                nonlocal call_count
                self.polarity_val = polarities[call_count % 2]
                call_count += 1

            @property
            def sentiment(self):
                class S:
                    polarity = self.polarity_val

                return S()

        with patch("app.services.ai.sentiment.TextBlob", side_effect=FakeBlob):
            avg_score, avg_polarity = compute_employee_sentiment_score(
                ["excellent", "neutral text"]
            )
            # scores=[100, 60] → avg=80
            assert avg_score == pytest.approx(80.0, rel=1e-3)
