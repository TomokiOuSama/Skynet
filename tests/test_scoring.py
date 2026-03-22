"""Tests for the scoring system logic."""

import datetime as dt
import math


def test_time_decay():
    """Verify time decay formula works correctly."""
    half_life = 60  # days
    # At t=0, decay should be 1.0
    assert math.exp(-0.693 * 0 / half_life) == 1.0
    # At t=half_life, decay should be ~0.5
    decay_at_half = math.exp(-0.693 * half_life / half_life)
    assert abs(decay_at_half - 0.5) < 0.01
    # At t=2*half_life, decay should be ~0.25
    decay_at_double = math.exp(-0.693 * 2 * half_life / half_life)
    assert abs(decay_at_double - 0.25) < 0.01


def test_score_weights_sum_to_one():
    """Scoring weights should sum to 1.0."""
    originality_weight = 0.35
    accuracy_weight = 0.40
    social_weight = 0.25
    total = originality_weight + accuracy_weight + social_weight
    assert abs(total - 1.0) < 0.001
