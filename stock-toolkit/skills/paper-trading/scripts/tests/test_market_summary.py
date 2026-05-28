"""Tests for market_summary trend computation."""

import pytest
from paper_trading.market_summary import _compute_trend


def test_compute_trend_rising():
    """3 bars going up: expect 上升 direction, positive change_pct, 上升 ma_direction."""
    bars = [
        {"date": "2026-05-25", "open": 100.0, "close": 101.0, "high": 102.0, "low": 99.0},
        {"date": "2026-05-26", "open": 101.0, "close": 103.0, "high": 104.0, "low": 100.0},
        {"date": "2026-05-27", "open": 103.0, "close": 106.0, "high": 107.0, "low": 102.0},
    ]
    result = _compute_trend(bars, period_type="day")
    assert result is not None
    assert result["period_type"] == "day"
    assert result["direction"] == "上升"
    assert result["change_pct"] > 0
    assert result["ma_direction"] == "上升"
    assert "key_levels" in result
    assert "highest" in result["key_levels"]
    assert "lowest" in result["key_levels"]
    assert "period_start" in result["key_levels"]
    assert "period_end" in result["key_levels"]


def test_compute_trend_falling():
    """3 bars going down: expect 下降 direction, negative change_pct, 下降 ma_direction."""
    bars = [
        {"date": "2026-05-25", "open": 106.0, "close": 105.0, "high": 107.0, "low": 104.0},
        {"date": "2026-05-26", "open": 105.0, "close": 102.0, "high": 106.0, "low": 101.0},
        {"date": "2026-05-27", "open": 102.0, "close": 100.0, "high": 103.0, "low": 99.0},
    ]
    result = _compute_trend(bars, period_type="day")
    assert result is not None
    assert result["direction"] == "下降"
    assert result["change_pct"] < 0
    assert result["ma_direction"] == "下降"


def test_compute_trend_flat():
    """3 bars nearly flat: expect small change_pct, ma_direction in valid set."""
    bars = [
        {"date": "2026-05-25", "open": 100.0, "close": 100.1, "high": 100.5, "low": 99.5},
        {"date": "2026-05-26", "open": 100.1, "close": 100.0, "high": 100.4, "low": 99.6},
        {"date": "2026-05-27", "open": 100.0, "close": 100.05, "high": 100.3, "low": 99.7},
    ]
    result = _compute_trend(bars, period_type="day")
    assert result is not None
    assert abs(result["change_pct"]) < 1.0
    assert result["ma_direction"] in ("走平", "上升", "下降")


def test_compute_trend_empty():
    """Empty bar list should return None."""
    result = _compute_trend([], period_type="week")
    assert result is None


def test_compute_trend_single_bar():
    """Single bar should have change_pct == 0.0."""
    bars = [
        {"date": "2026-05-27", "open": 100.0, "close": 105.0, "high": 107.0, "low": 99.0},
    ]
    result = _compute_trend(bars, period_type="day")
    assert result is not None
    assert result["change_pct"] == pytest.approx(0.0)
