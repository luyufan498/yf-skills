"""ATR（平均真实波幅）计算与持仓最高价(peak)合并工具。

独立模块，无状态、无存储依赖，可被 `ptrade atr-sync` 命令、回测脚本、
未来仓位模块复用。算法与回测验证的 volatility_strategy.py 一致：简单平均 TR（非 Wilder 平滑）。

回测参数（两个独立样本验证）：ATR 周期 14、cost_protection 用 k=2.0、trailing_stop 用 k=2.5。
"""

from typing import List, Optional

# ATR 计算参数（与回测 volatility_strategy.py 一致）
ATR_PERIOD = 14        # ATR 周期，简单平均 TR
ATR_K_COST = 2.0       # 成本保护：保护价 = 成本 − k×ATR
ATR_K_TRAIL = 2.5      # 移动止损：止损价 = peak − k×ATR
RISK_BUDGET = 0.015    # 风险预算 1.5% 总权益（仓位模块占位，本模块不实现仓位）

# ── 止盈三件套（2026-08-30 双层审计回测定稿，59 笔/20 票/一年）──
# 依据：ultra 仲裁（C方案 20/20 LOO 折全过、P90 右尾无伤；G方案 P90 -4.13pp 破红线出局）
BREAKEVEN_TRIGGER = 0.15   # 保本锁：本轮收盘浮盈 ≥ +15% → cost_protection 上移至成本（只升不降）
TP1_TRIGGER = 0.30         # 分批止盈阶梯1：收盘浮盈 ≥ +30% → 卖 1/3（take_profit_1）
TP2_TRIGGER = 0.50         # 分批止盈阶梯2：收盘浮盈 ≥ +50% → 再卖 1/3（take_profit_2）
                           # 余 1/3 走 trailing_stop 2.5×ATR 跟随（肥尾不动，禁止收紧 2.0）


def compute_true_range(prev_close: float, high: float, low: float) -> float:
    """单根 True Range = max(high−low, |high−prev_close|, |low−prev_close|)。"""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def compute_atr(klines: List[dict], period: int = ATR_PERIOD) -> Optional[float]:
    """
    基于日K列表计算 ATR(period)，简单平均 TR（非 Wilder 平滑）。

    klines: [{"date","open","high","low","close",...}] 按日期升序（fetch_kline_data 已排序）
    返回最近 period 根 TR 的简单平均；K线不足 period+1 根（第一根无 prev_close）返回 None。

    与回测 _atr 一致：第一根无 prev_close 跳过其 TR，故需 period+1 根才稳定。
    """
    if not klines or len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        prev_close = klines[i - 1].get("close")
        high = klines[i].get("high")
        low = klines[i].get("low")
        if prev_close is None or high is None or low is None:
            continue
        trs.append(compute_true_range(prev_close, high, low))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def merge_peak(stored_peak: Optional[float], klines: List[dict],
               realtime_high: Optional[float] = None) -> Optional[float]:
    """
    合并得出新 peak = max(stored_peak, 区间日K最高high, realtime_high)。

    max 语义天然保证只升不降。全为空返回 None。
    - stored_peak：Condition.peak_price（历史最高价留痕）
    - klines：最近 N 根日K（含当日已收盘），取其 high
    - realtime_high：当日实时最高价（盘中运行时补充当日未收盘的 high）
    """
    highs = [k.get("high") for k in (klines or []) if k.get("high") is not None]
    candidates = [stored_peak, realtime_high] + highs
    valid = [c for c in candidates if c is not None]
    return max(valid) if valid else None
