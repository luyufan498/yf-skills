"""ATR 计算与 peak 合并的单元测试。"""

from paper_trading.atr import compute_true_range, compute_atr, merge_peak, ATR_PERIOD, ATR_K_COST, ATR_K_TRAIL


def _kline(o, h, l, c, date="2026-01-01"):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1000}


def test_compute_true_range():
    """TR = max(H−L, |H−前C|, |L−前C|)"""
    assert compute_true_range(prev_close=100, high=105, low=95) == 10  # H−L=10, |H−C|=5, |L−C|=5
    assert compute_true_range(prev_close=90, high=105, low=95) == 15  # |H−前C|=15 最大
    assert compute_true_range(prev_close=110, high=105, low=95) == 15  # |L−前C|=15 最大


def test_compute_atr_simple_average():
    """构造 15 根K线，验证 ATR = 最近14根TR简单平均（非 Wilder）"""
    # 构造每根 TR=2 的K线（前收=100, H=102, L=100 → TR=2）
    klines = [_kline(100, 102, 100, 100, f"2026-01-{i:02d}") for i in range(1, 16)]
    atr = compute_atr(klines, period=14)
    assert atr == 2.0  # 14根TR都=2，平均=2


def test_compute_atr_insufficient_klines():
    """K线不足 period+1 根返回 None"""
    klines = [_kline(100, 102, 100, 100) for _ in range(10)]
    assert compute_atr(klines, period=14) is None


def test_compute_atr_skip_first():
    """第一根无 prev_close，其 TR 跳过"""
    # 15根，第一根跳过，后14根TR=2
    klines = [_kline(100, 102, 100, 100, f"2026-01-{i:02d}") for i in range(1, 16)]
    atr = compute_atr(klines, period=14)
    assert atr == 2.0
    # 16根时第一根仍跳过，取最后14根
    klines.append(_kline(100, 104, 100, 100, "2026-01-16"))
    atr2 = compute_atr(klines, period=14)
    # 最后14根：13根TR=2 + 1根TR=4 = (13*2+4)/14
    assert abs(atr2 - (13 * 2 + 4) / 14) < 0.001


def test_merge_peak_only_ascending():
    """peak 只升不降（max 语义）"""
    klines_high_95 = [_kline(100, 95, 90, 92)]
    assert merge_peak(stored_peak=100, klines=klines_high_95) == 100  # 旧peak更高
    klines_high_105 = [_kline(100, 105, 100, 102)]
    assert merge_peak(stored_peak=100, klines=klines_high_105) == 105  # 新高
    assert merge_peak(stored_peak=None, klines=[], realtime_high=None) is None  # 全空


def test_merge_peak_with_realtime_high():
    """realtime_high > 日K high 时取 realtime"""
    klines = [_kline(100, 95, 90, 92)]
    assert merge_peak(stored_peak=90, klines=klines, realtime_high=98) == 98
    assert merge_peak(stored_peak=None, klines=klines, realtime_high=98) == 98


def test_atr_constants_match_backtest():
    """ATR 常量与回测验证参数一致"""
    assert ATR_PERIOD == 14
    assert ATR_K_COST == 2.0
    assert ATR_K_TRAIL == 2.5
