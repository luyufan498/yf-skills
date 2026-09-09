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


# ============ 跳水段（check-plunge，2026-09-09 样本外定稿） ============
from paper_trading_v2.pulse_check import compute_plunge, PLUNGE_WINDOW


def _plunge_bars():
    """峰 100 → 谷 70（20 个交易日）→ 反弹 8 根到 78：depth≈-30%、speed≈-1.5%/日。

    峰必须唯一（横盘段 high 压低到 98），否则 argmax 会取到最早的同高 bar。
    """
    ks = [_k(f'2026-04-{i + 1:02d}', 97, 98.0, 96.0, 97.5) for i in range(40)]
    for i, px in enumerate((99.0, 99.5, 100.0)):        # 冲顶（峰=100，唯一）
        ks.append(_k(f'2026-05-{i + 1:02d}', px - 1, px, px - 1.5, px))
    for i in range(20):                                  # 跳水 20 日到 70
        px = 100 - 30 * (i + 1) / 20
        ks.append(_k(f'2026-06-{i + 1:02d}', px + 0.5, px + 1.0, px, px))
    for i in range(8):                                   # 反弹 8 日到 78
        px = 70 + 8 * (i + 1) / 8
        ks.append(_k(f'2026-07-{i + 1:02d}', px - 0.3, px + 0.5, px - 0.5, px))
    return ks


def test_compute_plunge_metrics():
    p = compute_plunge(_plunge_bars(), window=PLUNGE_WINDOW)
    assert p is not None
    assert abs(p['peak'] - 100.0) < 0.01 and abs(p['trough'] - 70.0) < 0.01
    assert abs(p['depth'] - (-30.0)) < 0.1, f'depth 应≈-30%，实得 {p["depth"]}'
    assert p['pdays'] == 20, f'跳水历时应 20 交易日，实得 {p["pdays"]}'
    assert abs(p['speed'] - (-1.5)) < 0.05, f'speed 应≈-1.5%/日，实得 {p["speed"]}'
    assert p['rdays'] == 8, f'离低点应 8 交易日，实得 {p["rdays"]}'
    assert p['has_plunge'] is True
    assert p['tag'] in ('has', 'mid')


def test_compute_plunge_no_plunge():
    """一路上涨无跳水 → has_plunge False、tag='none'。"""
    ks = [_k(f'2026-0{1 + i // 28}-{i % 28 + 1:02d}', 50 + i, 51 + i, 49.5 + i, 50.5 + i)
          for i in range(70)]
    p = compute_plunge(ks, window=PLUNGE_WINDOW)
    assert p is not None and p['has_plunge'] is False and p['tag'] == 'none'


def test_compute_plunge_freshness():
    """反弹新鲜度（2026-09-09 加）：低点距今 ≤20 交易日 = fresh；>20 → tag='stale'、不作加分。

    实证：甜点区内 低点近(≤20) − 远(>20) fwd20 +3.9pp (t 3.5)，2025/2026 分年皆成立。
    """
    # 新鲜：反弹 8 日 → rdays=8 ≤ 20
    p = compute_plunge(_plunge_bars(), window=PLUNGE_WINDOW)
    assert p['rdays'] == 8 and p['fresh'] is True and p['fresh_limit'] == 20
    assert p['tag'] in ('has', 'mid')

    # 陈旧：谷后横盘 17 日 → rdays=25 > 20（窗口 60 根，谷距末根 25 根）
    ks = _plunge_bars()
    base = ks[-1]['close']
    for i in range(17):
        ks.append(_k(f'2026-08-{i + 1:02d}', base, base + 0.5, base - 0.5, base))
    p2 = compute_plunge(ks, window=PLUNGE_WINDOW)
    assert p2['rdays'] == 25 and p2['fresh'] is False
    assert p2['tag'] == 'stale' and '反弹陈旧' in p2['state']


def test_compute_plunge_steep_flag():
    """极急跌（≤-2.5%/日）→ tag='steep'（样本外最弱档，只影响排序）。"""
    ks = [_k(f'2026-04-{i + 1:02d}', 98, 99.0, 97.0, 98.5) for i in range(45)]
    for i, px in enumerate((99.5, 100.0)):               # 唯一峰 100
        ks.append(_k(f'2026-05-{i + 1:02d}', px - 1, px, px - 1.5, px))
    for i in range(15):                                  # 15 日跌 45% → -3%/日
        px = 100 - 45 * (i + 1) / 15
        ks.append(_k(f'2026-06-{i + 1:02d}', px + 0.5, px + 1.0, px, px))
    for i in range(5):
        ks.append(_k(f'2026-07-{i + 1:02d}', 55 + i, 56 + i, 54 + i, 55.5 + i))
    p = compute_plunge(ks, window=PLUNGE_WINDOW)
    assert p is not None and p['tag'] == 'steep' and p['speed'] <= -2.5


def test_compute_plunge_short_series():
    assert compute_plunge(_plunge_bars()[:20], window=PLUNGE_WINDOW) is None
