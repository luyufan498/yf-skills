"""check-pulse 脉冲段检查测试（2026-09-08：回踩规律 736 段统计落地）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from paper_trading_v2.pulse_check import compute_pulse


def _k(d, o, h, l, c):
    return {'date': d, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': 1}


def _rising_then_dip():
    """trough 25 → 拉升到 40（峰）→ 回踩到 36（检查点）：F≈60%、峰下回撤>5%。"""
    ks = [
        _k('2026-08-24', 25.0, 25.8, 24.8, 25.0),
        _k('2026-08-25', 25.0, 26.0, 24.9, 25.8),
        _k('2026-08-26', 26.0, 28.0, 25.9, 27.5),
        _k('2026-08-27', 27.5, 30.5, 27.4, 30.0),
        _k('2026-08-28', 30.0, 33.0, 29.8, 32.5),
        _k('2026-08-31', 32.5, 36.0, 32.4, 35.5),
        _k('2026-09-01', 35.5, 40.0, 35.3, 39.5),
        _k('2026-09-02', 39.5, 39.8, 37.0, 37.5),   # 峰日 9/1（40）
        _k('2026-09-03', 37.5, 38.0, 36.0, 36.4),   # 回踩
        _k('2026-09-04', 36.4, 36.8, 35.9, 36.1),   # 检查点（峰下 ~9.8%）
    ]
    return ks


def test_compute_pulse_peak_and_f():
    ks = _rising_then_dip()
    p = compute_pulse(ks, window=10)
    assert p is not None
    assert abs(p['F'] - 61.29) < 0.5, f'F 应≈61.3%（24.8→40），实得 {p["F"]}'
    assert p['peak'] == 40.0
    assert abs(p['trough'] - 24.8) < 0.01
    assert p['days_from_peak'] == 3            # 峰 9/1，检查点 9/4
    assert p['drawdown'] < -5                  # 峰下回撤


def test_state_red_dip():
    ks = _rising_then_dip()
    p = compute_pulse(ks, window=10)
    assert '🔴' in p['state'], f'回踩>5% 应 🔴，实得 {p["state"]}'


def test_state_green_near_peak():
    """贴峰/检查点在峰日 → 🟢（无回踩迹象，可能主升延续）。"""
    ks = [
        _k('2026-08-25', 26.0, 26.5, 25.9, 26.2),
        _k('2026-08-26', 26.2, 28.0, 26.1, 27.6),
        _k('2026-08-27', 27.6, 30.0, 27.5, 29.6),
        _k('2026-08-28', 29.6, 32.5, 29.5, 32.0),
        _k('2026-08-31', 32.0, 35.0, 31.9, 34.6),
        _k('2026-09-01', 34.6, 35.2, 33.5, 33.8),   # 峰 9/1（35.0）
        _k('2026-09-02', 33.8, 34.9, 33.6, 34.7),   # 检查点贴峰（峰下0.9%）
    ]
    p = compute_pulse(ks, window=7)
    assert p is not None
    assert '🟢' in p['state'], f'贴峰应 🟢，实得 {p["state"]}'


def test_state_yellow_emo_top():
    """峰后 2 天浅回撤（<5%）→ 🟡 情绪顶区。"""
    ks = [
        _k('2026-08-24', 25.0, 25.8, 24.8, 25.0),
        _k('2026-08-25', 25.0, 26.0, 24.9, 25.8),
        _k('2026-08-26', 26.0, 28.0, 25.9, 27.5),
        _k('2026-08-27', 27.5, 30.5, 27.4, 30.0),
        _k('2026-08-28', 30.0, 33.0, 29.8, 32.5),
        _k('2026-08-31', 32.5, 36.0, 32.4, 35.5),
        _k('2026-09-01', 35.5, 40.0, 35.3, 39.5),
        _k('2026-09-02', 39.5, 39.8, 38.0, 38.5),   # 峰后 1 天（峰下3.75%）
    ]
    p = compute_pulse(ks, window=8)
    assert p is not None
    assert '🟡' in p['state'], f'峰后浅回撤应 🟡，实得 {p["state"]}'


def test_insufficient_klines():
    assert compute_pulse([_k('2026-09-01', 1, 1, 1, 1)], window=10) is None
