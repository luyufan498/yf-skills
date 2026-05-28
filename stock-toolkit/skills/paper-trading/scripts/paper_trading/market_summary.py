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


def _detect_intraday_pattern(minute_data: List[dict]) -> Optional[dict]:
    """Detect intraday price pattern from minute-level data.

    Args:
        minute_data: List of dicts with keys ``time`` (HHMM str), ``price`` (float),
            and optional ``volume`` (int).

    Returns:
        Dict with ``open``, ``high``, ``low``, ``close``, ``pattern``,
        ``amplitude``, ``volume_estimate``, and ``key_moments``,
        or ``None`` if input is empty or has no valid prices.
    """
    if not minute_data:
        return None

    valid_data = [d for d in minute_data if d.get("price") is not None]
    if not valid_data:
        return None

    times = [d["time"] for d in valid_data]
    prices = [d["price"] for d in valid_data]

    open_price = prices[0]
    high = max(prices)
    low = min(prices)
    close_price = prices[-1]

    amplitude = round((high - low) / low * 100, 2) if low != 0 else 0.0
    volume_estimate = valid_data[-1].get("volume")

    high_idx = prices.index(high)
    low_idx = prices.index(low)
    high_time = times[high_idx]
    low_time = times[low_idx]

    # Pattern classification (priority order)
    if amplitude < 2.0:
        pattern = "横盘震荡"
    elif _is_v_reversal(prices, times):
        pattern = "V型反转"
    elif (
        close_price < open_price
        and high_time < "1130"
        and ((high - close_price) / high * 100) > 2.0
    ):
        pattern = "冲高回落"
    elif close_price < open_price:
        pattern = "高开低走"
    elif (
        close_price > open_price
        and low_time < "1130"
        and low != open_price
        and ((close_price - low) / low * 100) > 2.0
    ):
        pattern = "低开高走"
    elif close_price > open_price:
        pattern = "单边上涨"
    elif close_price < open_price:
        pattern = "单边下跌"
    else:
        pattern = "横盘震荡"

    key_moments = _build_key_moments(valid_data, high, low)

    return {
        "open": open_price,
        "high": high,
        "low": low,
        "close": close_price,
        "pattern": pattern,
        "amplitude": amplitude,
        "volume_estimate": volume_estimate,
        "key_moments": key_moments,
    }


def _is_v_reversal(prices: List[float], times: List[str]) -> bool:
    """Check for V-shaped reversal: morning drop then afternoon recovery > 50%."""
    morning_indices = [i for i, t in enumerate(times) if t < "1300"]
    if len(morning_indices) < 2:
        return False

    morning_prices = [prices[i] for i in morning_indices]
    morning_high = max(morning_prices)
    morning_low = min(morning_prices)

    drop = morning_high - morning_low
    if drop / morning_low * 100 < 2.0:
        return False

    afternoon_prices = [prices[i] for i, t in enumerate(times) if t >= "1300"]
    if not afternoon_prices:
        return False

    afternoon_high = max(afternoon_prices)
    recovery = afternoon_high - morning_low

    return recovery > 0.5 * drop


def _build_key_moments(data: List[dict], high: float, low: float) -> List[dict]:
    """Build exactly 5 key moments, deduplicated by time."""
    times = [d["time"] for d in data]
    prices = [d["price"] for d in data]

    moments = []

    # 1. Opening
    moments.append({"time": times[0], "price": prices[0], "event": "开盘"})

    # 2. Morning high
    morning_indices = [i for i, t in enumerate(times) if t < "1200"]
    if morning_indices:
        morning_high = max(prices[i] for i in morning_indices)
        morning_high_idx = next(i for i in morning_indices if prices[i] == morning_high)
        event = "早盘高点" if prices[morning_high_idx] == high else "早盘"
        moments.append(
            {
                "time": times[morning_high_idx],
                "price": prices[morning_high_idx],
                "event": event,
            }
        )

    # 3. Midday low
    midday_indices = [i for i, t in enumerate(times) if "1100" <= t <= "1300"]
    if midday_indices:
        midday_low = min(prices[i] for i in midday_indices)
        midday_low_idx = next(i for i in midday_indices if prices[i] == midday_low)
        moments.append(
            {
                "time": times[midday_low_idx],
                "price": prices[midday_low_idx],
                "event": "午盘低点",
            }
        )

    # 4. Afternoon stabilization
    afternoon_indices = [i for i, t in enumerate(times) if "1400" <= t <= "1430"]
    if afternoon_indices:
        mid = len(afternoon_indices) // 2
        idx = afternoon_indices[mid]
        moments.append(
            {
                "time": times[idx],
                "price": prices[idx],
                "event": "午后企稳",
            }
        )

    # 5. Closing
    moments.append({"time": times[-1], "price": prices[-1], "event": "收盘"})

    # Deduplicate by time, keeping earlier entries
    seen = set()
    deduped = []
    for m in moments:
        if m["time"] not in seen:
            seen.add(m["time"])
            deduped.append(m)

    # Pad to 5 if needed
    for i, (t, p) in enumerate(zip(times, prices)):
        if len(deduped) >= 5:
            break
        if t not in seen:
            if len(deduped) > 0:
                deduped.insert(len(deduped) - 1, {"time": t, "price": p, "event": "盘中"})
            else:
                deduped.append({"time": t, "price": p, "event": "盘中"})
            seen.add(t)

    return deduped[:5]

