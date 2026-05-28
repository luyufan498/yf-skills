"""Tests for market_summary trend computation."""

import pytest
from paper_trading.market_summary import _compute_trend, _detect_intraday_pattern


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


def test_detect_intraday_pattern_fallback():
    """冲高回落/高开低走 pattern: open=9.42, high=9.46 at 09:32, low=9.20 at 14:30, close=9.21."""
    minute_data = []
    # Build data from 0930 to 1500 (excluding noon 1130-1300)
    times = []
    for h, m in [(9, 30), (9, 31), (9, 32)]:
        times.append(f"{h:02d}{m:02d}")
    for h in range(10, 11):
        for m in range(0, 60):
            times.append(f"{h:02d}{m:02d}")
    for m in range(0, 31):
        times.append(f"11{m:02d}")
    for h in range(13, 15):
        for m in range(0, 60):
            times.append(f"{h:02d}{m:02d}")
    for m in range(0, 1):
        times.append(f"15{m:02d}")

    # Create prices: start at 9.42, spike to 9.46 at 0932, then drift down to 9.20 at 1430, close 9.21
    base = 9.42
    for t in times:
        if t == "0930":
            price = base
        elif t == "0932":
            price = 9.46
        elif t == "1430":
            price = 9.20
        elif t == "1500":
            price = 9.21
        else:
            # Interpolate between known points
            minute_idx = times.index(t)
            idx_0932 = times.index("0932")
            idx_1430 = times.index("1430")
            idx_1500 = times.index("1500")
            if minute_idx < idx_0932:
                price = base + (9.46 - base) * (minute_idx / idx_0932)
            elif minute_idx < idx_1430:
                price = 9.46 + (9.20 - 9.46) * ((minute_idx - idx_0932) / (idx_1430 - idx_0932))
            else:
                price = 9.20 + (9.21 - 9.20) * ((minute_idx - idx_1430) / (idx_1500 - idx_1430))
        minute_data.append({"time": t, "price": round(price, 2), "volume": 1000})

    result = _detect_intraday_pattern(minute_data)
    assert result is not None
    assert result["open"] == pytest.approx(9.42)
    assert result["high"] == pytest.approx(9.46)
    assert result["low"] == pytest.approx(9.20)
    assert result["close"] == pytest.approx(9.21)
    assert result["pattern"] in ("冲高回落", "高开低走")
    assert result["amplitude"] > 0
    assert "volume_estimate" in result
    assert result["volume_estimate"] is None or isinstance(result["volume_estimate"], int)
    assert "key_moments" in result
    assert len(result["key_moments"]) == 5
    for km in result["key_moments"]:
        assert "time" in km
        assert "price" in km
        assert "event" in km


def test_detect_intraday_pattern_rising():
    """Prices steadily rising from 9.10 to 9.50: expect 单边上涨 pattern."""
    minute_data = []
    start_price = 9.10
    end_price = 9.50
    n_points = 20
    for i in range(n_points):
        time_str = f"09{30 + i:02d}"
        price = start_price + (end_price - start_price) * (i / (n_points - 1))
        minute_data.append({"time": time_str, "price": round(price, 2), "volume": 500})

    result = _detect_intraday_pattern(minute_data)
    assert result is not None
    assert result["open"] == pytest.approx(start_price)
    assert result["close"] == pytest.approx(end_price)
    assert result["close"] > result["open"]
    assert result["pattern"] == "单边上涨"
    assert result["amplitude"] > 0
    assert "key_moments" in result
    assert len(result["key_moments"]) == 5


def test_detect_intraday_pattern_empty():
    """Empty minute_data list should return None."""
    result = _detect_intraday_pattern([])
    assert result is None
