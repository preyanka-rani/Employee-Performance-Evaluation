"""
tests/unit/test_developer_scoring.py
──────────────────────────────────────
Unit tests for all Developer scoring formula functions.
Tests verify exact formulas from the documentation.
"""

import pytest

from app.services.scoring.developer import (
    compute_final_score,
    compute_reward,
    compute_segment_a,
    compute_segment_b,
    normalise_work_hours,
)


class TestNormaliseWorkHours:
    """Test the step-function for work-log hour normalisation."""

    @pytest.mark.parametrize(
        "hours, expected",
        [
            (160, 100.0),
            (175, 100.0),
            (200, 100.0),
            (159.9, 90.0),
            (140, 90.0),
            (139.9, 80.0),
            (120, 80.0),
            (119.9, 70.0),
            (100, 70.0),
            (99.9, 60.0),
            (80, 60.0),
            (79.9, 50.0),
            (60, 50.0),
            (59.9, 40.0),
            (0, 40.0),
        ],
    )
    def test_step_boundaries(self, hours: float, expected: float):
        assert normalise_work_hours(hours) == expected


class TestSegmentA:
    """Test Segment A computation: segment_a_marks = (C1 + C2) / 2 / 2."""

    def test_perfect_scores(self):
        # C1=100, C2=100 → segment_a=100 → marks=50
        _, marks = compute_segment_a(100.0, 100.0, 100.0)
        assert marks == 50.0

    def test_zero_scores(self):
        _, marks = compute_segment_a(0.0, 0.0, 0.0)
        assert marks == 0.0

    def test_component2_weighting(self):
        # C2 = work_log*0.9 + sentiment*0.1
        # work_log=80, sentiment=60 → C2 = 80*0.9+60*0.1 = 72+6 = 78
        # segment_a = (90 + 78) / 2 = 84
        # marks = 84 / 2 = 42
        segment_a_raw, marks = compute_segment_a(90.0, 80.0, 60.0)
        expected_c2 = 80.0 * 0.9 + 60.0 * 0.1  # = 78.0
        expected_seg_a = (90.0 + expected_c2) / 2  # = 84.0
        expected_marks = expected_seg_a / 2  # = 42.0
        assert abs(marks - expected_marks) < 0.01

    def test_marks_bounded_by_50(self):
        _, marks = compute_segment_a(100.0, 100.0, 100.0)
        assert marks <= 50.0


class TestSegmentB:
    """Test Segment B: attendance_marks + TL scores (max ~50)."""

    def test_maximum_segment_b(self):
        # attendance_score=100 → marks=10; TL max=10+15+15=40 → total=50
        result = compute_segment_b(100.0, 10.0, 15.0, 15.0)
        assert result == 50.0

    def test_zero_segment_b(self):
        result = compute_segment_b(0.0, 0.0, 0.0, 0.0)
        assert result == 0.0

    def test_attendance_conversion(self):
        # attendance_score=70 → attendance_marks = 7.0
        result = compute_segment_b(70.0, 5.0, 8.0, 7.0)
        assert result == pytest.approx(7.0 + 5.0 + 8.0 + 7.0, rel=1e-3)


class TestReward:
    """Test reward formula: (MIN(sum, 140) * 5) / 140."""

    def test_reward_at_maximum(self):
        # All perfect → sum=400 → capped at 140 → reward=5.0
        reward = compute_reward(100.0, 100.0, 40.0, 100.0)
        assert reward == 5.0

    def test_reward_proportional(self):
        # sum = 70 → reward = (70*5)/140 = 2.5
        reward = compute_reward(35.0, 0.0, 0.0, 35.0)
        assert reward == pytest.approx(2.5, rel=1e-3)

    def test_reward_cap(self):
        reward = compute_reward(100.0, 100.0, 100.0, 100.0)
        assert reward == 5.0

    def test_reward_zero(self):
        reward = compute_reward(0.0, 0.0, 0.0, 0.0)
        assert reward == 0.0


class TestFinalScore:
    """Test final_score = ((base_total + reward) / 105) * 100."""

    def test_perfect_final_score(self):
        # base_total=100 (theoretical max), reward=5 → final=(105/105)*100=100
        score = compute_final_score(100.0, 5.0)
        assert score == pytest.approx(100.0, rel=1e-3)

    def test_zero_final_score(self):
        score = compute_final_score(0.0, 0.0)
        assert score == 0.0

    def test_midrange_score(self):
        # base_total=52.5, reward=2.5 → (55/105)*100 ≈ 52.38
        score = compute_final_score(52.5, 2.5)
        expected = ((52.5 + 2.5) / 105) * 100
        assert score == pytest.approx(expected, rel=1e-3)

    def test_realistic_scenario(self):
        """
        Realistic scenario from documentation:
            quality_check=80, work_log=90, sentiment=60
            C2 = 90*0.9 + 60*0.1 = 81+6 = 87
            segment_a = (80+87)/2 = 83.5 → marks = 83.5/2 = 41.75
            attendance=75 → att_marks=7.5
            problem_solving=7, kpi=10, general=8 → seg_b = 7.5+7+10+8 = 32.5
            base_total = 41.75 + 32.5 = 74.25
            reward = MIN(75+90+25+80, 140) = MIN(270,140)*5/140 = 5.0
            final = (74.25+5)/105*100 ≈ 75.48
        """
        _, seg_a_marks = compute_segment_a(80.0, 90.0, 60.0)
        seg_b = compute_segment_b(75.0, 7.0, 10.0, 8.0)
        base = round(seg_a_marks + seg_b, 4)
        reward = compute_reward(75.0, 90.0, 25.0, 80.0)
        final = compute_final_score(base, reward)

        assert final > 0
        assert final <= 100
        assert final == pytest.approx(((base + reward) / 105) * 100, rel=1e-3)
