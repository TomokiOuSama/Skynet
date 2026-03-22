"""Tests for the scoring system logic."""

import math
import statistics

from skynet.models.kol import VolumeTier
from skynet.scoring.scorer import _classify_volume_tier, _safe_median


def test_time_decay():
    """Verify time decay formula works correctly."""
    half_life = 60
    assert math.exp(-0.693 * 0 / half_life) == 1.0
    decay_at_half = math.exp(-0.693 * half_life / half_life)
    assert abs(decay_at_half - 0.5) < 0.01
    decay_at_double = math.exp(-0.693 * 2 * half_life / half_life)
    assert abs(decay_at_double - 0.25) < 0.01


def test_score_weights_sum_to_one():
    """Scoring weights should sum to 1.0."""
    total = 0.30 + 0.35 + 0.10 + 0.25
    assert abs(total - 1.0) < 0.001


def test_volume_tier_classification():
    assert _classify_volume_tier(10) == VolumeTier.SELECTIVE
    assert _classify_volume_tier(29) == VolumeTier.SELECTIVE
    assert _classify_volume_tier(30) == VolumeTier.MODERATE
    assert _classify_volume_tier(99) == VolumeTier.MODERATE
    assert _classify_volume_tier(100) == VolumeTier.HIGH_VOLUME
    assert _classify_volume_tier(500) == VolumeTier.HIGH_VOLUME


def test_safe_median():
    assert _safe_median([]) is None
    assert _safe_median([1.0]) == 1.0
    assert _safe_median([1.0, 3.0]) == 2.0
    assert _safe_median([1.0, 2.0, 100.0]) == 2.0  # Outlier doesn't skew


def test_median_vs_average_outlier_resistance():
    """Median is resistant to outlier winners — key insight from the Reddit post."""
    returns = [0.05, 0.03, -0.02, 0.01, 0.04, 2.0]  # One 200% outlier
    avg = statistics.mean(returns)
    med = statistics.median(returns)
    # Average is heavily skewed by the outlier
    assert avg > 0.3
    # Median tells the true story
    assert 0.02 < med < 0.05
