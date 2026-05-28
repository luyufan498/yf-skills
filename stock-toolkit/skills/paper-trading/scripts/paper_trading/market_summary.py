"""Market summary utilities for paper trading."""

from typing import List, Optional


def _compute_trend(bars: List[dict], period_type: str) -> Optional[dict]:
    """Compute trend metrics for a list of OHLC bars."""
    if not bars:
        return None

    first = bars[0]
    last = bars[-1]

    # change_pct
    first_close = first.get("close")
    last_close = last.get("close")
    if first_close is None or first_close == 0:
        change_pct = 0.0
    else:
        change_pct = round((last_close - first_close) / first_close * 100, 2)

    # direction
    if change_pct > 1.0:
        direction = "上升"
    elif change_pct < -1.0:
        direction = "下降"
    else:
        direction = "横盘"

    # volatility
    max_volatility = 0.0
    for bar in bars:
        high = bar.get("high")
        low = bar.get("low")
        if high is None or low is None or low == 0:
            continue
        amplitude = (high - low) / low * 100
        if amplitude > max_volatility:
            max_volatility = amplitude
    volatility = round(max_volatility, 2)

    # ma_direction
    closes = [bar.get("close") for bar in bars if bar.get("close") is not None]
    n = len(closes)
    if n == 0:
        ma_direction = "走平"
    elif n == 1:
        ma_direction = "走平"
    elif n == 2:
        if closes[1] > closes[0]:
            ma_direction = "上升"
        elif closes[1] < closes[0]:
            ma_direction = "下降"
        else:
            ma_direction = "走平"
    else:
        # Use last 3 closes for linear regression
        recent_closes = closes[-3:]
        x = list(range(len(recent_closes)))
        x_mean = sum(x) / len(x)
        y_mean = sum(recent_closes) / len(recent_closes)
        numerator = sum((x[i] - x_mean) * (recent_closes[i] - y_mean) for i in range(len(x)))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(len(x)))
        if denominator == 0:
            slope = 0.0
        else:
            slope = numerator / denominator
        if slope > 0.01:
            ma_direction = "上升"
        elif slope < -0.01:
            ma_direction = "下降"
        else:
            ma_direction = "走平"

    # key_levels
    highs = [bar.get("high") for bar in bars if bar.get("high") is not None]
    lows = [bar.get("low") for bar in bars if bar.get("low") is not None]
    highest = max(highs) if highs else None
    lowest = min(lows) if lows else None
    period_start = first.get("open")
    period_end = last.get("close")

    return {
        "period_type": period_type,
        "direction": direction,
        "change_pct": change_pct,
        "volatility": volatility,
        "ma_direction": ma_direction,
        "key_levels": {
            "highest": highest,
            "lowest": lowest,
            "period_start": period_start,
            "period_end": period_end,
        },
    }
