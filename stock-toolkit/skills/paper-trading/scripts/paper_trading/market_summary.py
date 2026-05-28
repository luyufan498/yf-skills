"""Market summary utilities for paper trading."""

from datetime import datetime
from typing import List, Optional

from paper_trading.price_fetcher import StockPriceFetcher
from paper_trading.kline_fetcher import KLineDataFetcher


def _trim_bars(bars: List[dict]) -> List[dict]:
    """Keep only essential OHLCV fields."""
    fields = {"date", "open", "close", "high", "low", "volume"}
    return [{k: v for k, v in bar.items() if k in fields} for bar in bars]


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


def _compute_cross_period(monthly: dict, weekly: dict, daily: dict, current_price: float) -> dict:
    """Compute cross-period trend alignment and signal.

    Args:
        monthly: Trend dict for monthly period (must contain ``direction`` and ``key_levels``).
        weekly: Trend dict for weekly period.
        daily: Trend dict for daily period.
        current_price: Current market price.

    Returns:
        Dict with ``alignment``, ``position_in_monthly_range`` (0-1), and ``signal``.
    """
    # Extract directions with defaults
    m_dir = monthly.get("direction") if monthly else None
    w_dir = weekly.get("direction") if weekly else None
    d_dir = daily.get("direction") if daily else None

    m_dir = m_dir or "横盘"
    w_dir = w_dir or "横盘"
    d_dir = d_dir or "横盘"

    # position_in_monthly_range
    if monthly:
        key_levels = monthly.get("key_levels", {})
        highest = key_levels.get("highest")
        lowest = key_levels.get("lowest")
    else:
        highest = None
        lowest = None

    if highest is None or lowest is None or highest == lowest:
        position = 0.5
    else:
        position = (current_price - lowest) / (highest - lowest)
        if position < 0:
            position = 0.0
        elif position > 1:
            position = 1.0
        position = round(position, 3)

    # alignment logic
    up_count = sum(1 for d in (m_dir, w_dir, d_dir) if d == "上升")
    down_count = sum(1 for d in (m_dir, w_dir, d_dir) if d == "下降")

    if up_count >= 2 and down_count == 0:
        alignment = "多周期共振上升"
    elif down_count >= 2 and up_count == 0:
        alignment = "多周期共振下降"
    elif m_dir == "下降" and d_dir == "上升":
        alignment = "中长期承压，短期反弹"
    elif m_dir == "上升" and d_dir == "下降":
        alignment = "中长期向好，短期回调"
    elif w_dir == "横盘" and d_dir == "上升":
        alignment = "中期震荡，短期反弹"
    elif w_dir == "横盘" and d_dir == "下降":
        alignment = "中期震荡，短期回调"
    else:
        alignment = "趋势分化，需观察"

    # signal logic
    signal_parts = []
    if position < 0.2:
        signal_parts.append("处于长期区间低位")
    elif position > 0.8:
        signal_parts.append("处于长期区间高位")

    if m_dir == "下降" and w_dir == "横盘" and d_dir == "上升":
        signal_parts.append("日线反弹但周线仍处下降通道，谨慎乐观")
    elif m_dir == "上升" and d_dir == "下降":
        signal_parts.append("长期趋势向好，短期回调或为低吸机会")
    elif up_count == 3:
        signal_parts.append("全线上涨，注意追高风险")
    elif down_count == 3:
        signal_parts.append("全线下跌，等待企稳信号")
    else:
        signal_parts.append("趋势不明确，建议观望")

    signal = "。".join(signal_parts)

    return {
        "alignment": alignment,
        "position_in_monthly_range": position,
        "signal": signal,
    }


class MarketSummaryAnalyzer:
    """Orchestrates data fetching and produces a structured market summary."""

    def __init__(self):
        self.price_fetcher = StockPriceFetcher()
        self.kline_fetcher = KLineDataFetcher()

    def analyze(self, code: str) -> dict:
        stock_info = self.price_fetcher.get_realtime_price(code)

        monthly_bars = self.kline_fetcher.fetch_kline_data(code, "month", 6)
        weekly_bars = self.kline_fetcher.fetch_kline_data(code, "week", 8)
        daily_bars = self.kline_fetcher.fetch_kline_data(code, "day", 5)

        raw_minute = self.kline_fetcher.fetch_minute_data(code)
        if isinstance(raw_minute, dict):
            minute_list = raw_minute.get("data", [])
        else:
            minute_list = raw_minute if isinstance(raw_minute, list) else []

        # Current price fallback
        current_price = None
        pre_close = None
        name = ""
        if stock_info is not None:
            current_price = getattr(stock_info, "current_price", None)
            pre_close = getattr(stock_info, "pre_close", None)
            name = getattr(stock_info, "name", "") or ""

        if current_price is None and daily_bars:
            current_price = daily_bars[-1].get("close")

        # Compute trends
        monthly_trend = _compute_trend(monthly_bars, "month")
        weekly_trend = _compute_trend(weekly_bars, "week")
        daily_trend = _compute_trend(daily_bars, "day")

        # Intraday pattern
        intraday = _detect_intraday_pattern(minute_list)

        # Cross-period
        cross_period = None
        if current_price is not None:
            cross_period = _compute_cross_period(
                monthly_trend, weekly_trend, daily_trend, current_price
            )

        # Today's trend summary
        today_change_pct = 0.0
        if pre_close and pre_close != 0 and current_price is not None:
            today_change_pct = round((current_price - pre_close) / pre_close * 100, 2)

        # Volume vs avg
        volume_vs_avg = None
        minute_total_volume = None
        if minute_list:
            minute_volumes = [m.get("volume") for m in minute_list if m.get("volume") is not None]
            if minute_volumes:
                minute_total_volume = sum(minute_volumes)

        if daily_bars and minute_total_volume is not None:
            daily_volumes = [b.get("volume") for b in daily_bars if b.get("volume") is not None]
            if daily_volumes:
                avg_volume = sum(daily_volumes) / len(daily_volumes)
                if avg_volume:
                    volume_vs_avg = round(minute_total_volume / avg_volume, 2)

        today_trend = {
            "direction": intraday.get("pattern", "横盘震荡") if intraday else "横盘震荡",
            "change_pct": today_change_pct,
            "volume_vs_avg": volume_vs_avg,
            "amplitude": intraday.get("amplitude") if intraday else None,
        }

        trend_summary = {
            "long_term": monthly_trend,
            "medium_term": weekly_trend,
            "short_term": daily_trend,
            "today": today_trend,
        }

        # Build period summaries
        def _period_summary(bars, trend):
            return {
                "count": len(bars),
                "bars": _trim_bars(bars),
                "key_levels": trend.get("key_levels", {}) if trend else {},
            }

        result = {
            "code": code,
            "name": name,
            "current_price": current_price,
            "pre_close": pre_close,
            "current_date": datetime.now().strftime("%Y-%m-%d"),
            "trend_summary": trend_summary,
            "cross_period": cross_period,
            "monthly": _period_summary(monthly_bars, monthly_trend),
            "weekly": _period_summary(weekly_bars, weekly_trend),
            "daily": _period_summary(daily_bars, daily_trend),
            "intraday": intraday,
        }

        return result

    def _fmt_bar(self, bar: dict, prev_close: Optional[float] = None) -> str:
        """Format a single OHLC bar for pretty output, with optional change from previous."""
        parts = [f"  {bar.get('date', '')}:"]
        for key in ["open", "close", "high", "low"]:
            val = bar.get(key)
            if val is not None:
                name_map = {"open": "开", "close": "收", "high": "高", "low": "低"}
                parts.append(f" {name_map[key]}{val}")
        close = bar.get("close")
        if prev_close is not None and close is not None and prev_close != 0:
            change = round((close - prev_close) / prev_close * 100, 2)
            sign = "+" if change > 0 else ""
            parts.append(f" 涨跌:{sign}{change}%")
        return "".join(parts)

    def _fmt_period_block(self, label: str, period_key: str, data: dict) -> list:
        """Format one period block (monthly/weekly/daily)."""
        lines = []
        period = data.get(period_key, {})
        trend = data.get("trend_summary", {}).get(
            {"monthly": "long_term", "weekly": "medium_term", "daily": "short_term"}.get(period_key, "")
        )
        count = period.get("count", 0)
        kl = period.get("key_levels", {})
        bars = period.get("bars", [])

        lines.append(f"{label} ({count}条)")
        lines.append("-" * 40)
        lines.append(f"  最高: {kl.get('highest', 'N/A')}  最低: {kl.get('lowest', 'N/A')}")

        change = trend.get("change_pct") if trend else None
        if change is not None:
            sign = "+" if change > 0 else ""
            lines.append(f"  开盘: {kl.get('period_start', 'N/A')}  收盘: {kl.get('period_end', 'N/A')}  涨跌: {sign}{change}%")
        else:
            lines.append(f"  开盘: {kl.get('period_start', 'N/A')}  收盘: {kl.get('period_end', 'N/A')}")

        prev_close = None
        for bar in bars:
            lines.append(self._fmt_bar(bar, prev_close))
            prev_close = bar.get("close")

        lines.append("")
        return lines

    def format_pretty(self, data: dict) -> str:
        """Human-readable text output with objective data only."""
        lines = []
        header = (
            f"{data.get('name', '')} ({data.get('code', '')})  "
            f"{data.get('current_date', '')}  现价: {data.get('current_price', 'N/A')}"
        )
        lines.append(header)
        lines.append("=" * len(header))
        lines.append("")

        # Monthly block
        lines.extend(self._fmt_period_block("近6月", "monthly", data))

        # Weekly block
        lines.extend(self._fmt_period_block("近8周", "weekly", data))

        # Daily block
        lines.extend(self._fmt_period_block("近5日", "daily", data))

        # Intraday block
        today_trend = data.get("trend_summary", {}).get("today")
        intraday = data.get("intraday")
        lines.append("当日分时")
        lines.append("-" * 40)

        if today_trend:
            change = today_trend.get("change_pct")
            if change is not None:
                sign = "+" if change > 0 else ""
                lines.append(f"  涨跌: {sign}{change}%")
            amplitude = today_trend.get("amplitude")
            if amplitude is not None:
                lines.append(f"  振幅: {amplitude}%")

        if intraday and intraday.get("key_moments"):
            for km in intraday["key_moments"]:
                lines.append(f"  {km.get('time', '')} {km.get('event', '')} {km.get('price', '')}")

        lines.append("")
        return "\n".join(lines)

    def format_markdown(self, data: dict) -> str:
        """Markdown formatted output with objective data only."""
        lines = []
        lines.append(f"# {data.get('name', '')} ({data.get('code', '')}) 市场摘要")
        lines.append("")
        lines.append(f"**日期**: {data.get('current_date', '')}")
        lines.append(f"**现价**: {data.get('current_price', 'N/A')}")
        lines.append(f"**昨收**: {data.get('pre_close', 'N/A')}")
        lines.append("")

        # Objective period summary table
        lines.append("## 周期变化")
        lines.append("")
        lines.append("| 时间区间 | 涨跌幅 | 开盘 | 收盘 | 最高 | 最低 |")
        lines.append("|----------|--------|------|------|------|------|")

        trend_summary = data.get("trend_summary", {})
        monthly = trend_summary.get("long_term")
        weekly = trend_summary.get("medium_term")
        daily = trend_summary.get("short_term")
        today = trend_summary.get("today")

        if monthly:
            change = monthly.get("change_pct", "N/A")
            kl = monthly.get("key_levels", {})
            lines.append(
                f"| 近6个月 | {change}% | {kl.get('period_start', 'N/A')} | "
                f"{kl.get('period_end', 'N/A')} | {kl.get('highest', 'N/A')} | {kl.get('lowest', 'N/A')} |"
            )

        if weekly:
            change = weekly.get("change_pct", "N/A")
            kl = weekly.get("key_levels", {})
            lines.append(
                f"| 近8周 | {change}% | {kl.get('period_start', 'N/A')} | "
                f"{kl.get('period_end', 'N/A')} | {kl.get('highest', 'N/A')} | {kl.get('lowest', 'N/A')} |"
            )

        if daily:
            change = daily.get("change_pct", "N/A")
            kl = daily.get("key_levels", {})
            lines.append(
                f"| 近5日 | {change}% | {kl.get('period_start', 'N/A')} | "
                f"{kl.get('period_end', 'N/A')} | {kl.get('highest', 'N/A')} | {kl.get('lowest', 'N/A')} |"
            )

        if today:
            change = today.get("change_pct", "N/A")
            intraday = data.get("intraday", {})
            lines.append(
                f"| 今日 | {change}% | {data.get('pre_close', 'N/A')} | "
                f"{data.get('current_price', 'N/A')} | {intraday.get('high', 'N/A')} | {intraday.get('low', 'N/A')} |"
            )

        lines.append("")

        # Intraday table
        intraday = data.get("intraday")
        if intraday and intraday.get("key_moments"):
            lines.append("## 分时关键节点")
            lines.append("")
            lines.append("| 时间 | 事件 | 价格 |")
            lines.append("|------|------|------|")
            for km in intraday["key_moments"]:
                lines.append(f"| {km.get('time', '')} | {km.get('event', '')} | {km.get('price', '')} |")
            lines.append("")

        # Daily bars table
        daily_data = data.get("daily", {})
        bars = daily_data.get("bars", [])
        if bars:
            lines.append("## 日线数据")
            lines.append("")
            lines.append("| 日期 | 开盘 | 收盘 | 最高 | 最低 | 成交量 |")
            lines.append("|------|------|------|------|------|--------|")
            for bar in bars:
                lines.append(
                    f"| {bar.get('date', '')} | {bar.get('open', '')} | "
                    f"{bar.get('close', '')} | {bar.get('high', '')} | "
                    f"{bar.get('low', '')} | {bar.get('volume', '')} |"
                )
            lines.append("")

        return "\n".join(lines)

