"""发酵段脉冲检查命令（2026-09-08，回踩规律统计落地）。

check-pulse <stock>：检查近 10 交易日是否有"拉升段（trough→peak）"及当前
价格相对段峰的位置，输出指标性提示——买入触发前（sleeve-order-fill /
C2 收编 / watchpoint buy / 晨审）调用，供决策参考（只报不拦）。

指标口径（2026-09-08 与用户定稿，736 段统计校准）：
- 窗口：买入/检查点前 10 交易日（覆盖拉升+回踩完整形态，防头尾被回踩抹平）
- F%  = (W_peak − trough) / trough —— trough = 段峰【左侧】窗口内最低 low
       （= 该拉升段真实涨幅；"最高值左侧的极小"，冲高回踩后仍可测）
- 距峰天数：段峰到检查点的交易日数（0=峰日当天）
- 现价位置 = 现价相对段峰回撤 %
- 连续大阳数：段峰前最近的连阳/连涨结构
- 状态（软保护三级，只报不拦）：
  🟢 贴峰/主升：现价 ≥ 峰×0.97（无回踩迹象——可能一路阳延续，66% 支持）
  🟡 峰后 ≤3 日或回撤 <5%：情绪顶高发区/浅回踩——降档谨慎（消息确认日
     追入=买在情绪顶的经典坑）
  🔴 回踩 ≥5%：接刀区——拉升段回撤 >5% 后 5 日仅 25-31% 收复、52-61% 仍
     低于 -5%（数据：736 拉升段）——禁市价追，挂企稳/二波信号或放弃

数据：market.db raw 日K（fetch-kline-cached 同库；缺数据提示先拉取）。
"""
import os
import sqlite3
from datetime import datetime
from typing import List, Optional

import typer

from paper_trading_v2.helpers import normalize_stock_name
from paper_trading_v2.code_searcher import lookup_code_for_name


WINDOW = 10          # 检查窗口（前 N 交易日）
DIP_RED = 5.0        # 现价低于段峰 ≥5% → 🔴 接刀区
CLOSE_PEAK = 3.0     # 现价高于峰×97% → 🟢 贴峰
EMO_TOP_DAYS = 3     # 峰后 ≤3 交易日 → 🟡 情绪顶高发区


def _load_klines(code: str, limit: int = 40) -> List[dict]:
    from paper_trading_v2.market_cache import market_db_path
    path = market_db_path()
    if not os.path.exists(path):
        raise typer.Exit(f"market.db 不存在：{path}——先跑 fetch-kline-cached")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    ks = [dict(x) for x in conn.execute(
        "SELECT date, open, high, low, close, volume FROM kline_daily "
        "WHERE code=? ORDER BY date DESC LIMIT ?", (code, limit))]
    conn.close()
    return list(reversed(ks))


def compute_pulse(ks: List[dict], window: int = WINDOW) -> Optional[dict]:
    """计算脉冲段指标：F%、段峰、trough、距峰天数、现价位置、连阳结构。"""
    if len(ks) < 5:
        return None
    pre = ks[-window:]
    # 段峰 = 窗口最高 high（检查点在峰后时峰在左侧；峰即窗口内最高）
    pi = max(range(len(pre)), key=lambda i: pre[i]['high'])
    peak = pre[pi]['high']
    peak_date = pre[pi]['date']
    # trough = 峰左侧窗口内最低 low（"最高值左侧的极小"）
    left = pre[:pi + 1]
    trough = min(x['low'] for x in left)
    F = (peak - trough) / trough * 100 if trough else 0.0
    # 距峰天数：从检查点（最后根）往回数到峰
    days_from_peak = len(pre) - 1 - pi
    # 峰前连涨结构：峰前连续 close 递增天数（拉升段斜率参考）
    consec_up = 0
    for k in range(pi - 1, 0, -1):
        if pre[k]['close'] > pre[k - 1]['close']:
            consec_up += 1
        else:
            break
    last = pre[-1]
    px = last['close']
    drawdown = (px - peak) / peak * 100  # 现价 vs 峰（负=峰下）
    # 状态
    if px >= peak * (1 - CLOSE_PEAK / 100):
        state, st_tag = '🟢 贴峰/主升', 'green'
    elif days_from_peak <= EMO_TOP_DAYS and drawdown > -DIP_RED:
        state, st_tag = '🟡 峰后情绪顶区', 'yellow'
    elif drawdown <= -DIP_RED:
        state, st_tag = '🔴 回踩接刀区', 'red'
    else:
        state, st_tag = '🟡 浅回踩', 'yellow'
    return {
        'F': F, 'peak': peak, 'peak_date': peak_date, 'trough': trough,
        'days_from_peak': days_from_peak, 'drawdown': drawdown,
        'consec_up': consec_up, 'px': px, 'state': state, 'tag': st_tag,
        'window_start': pre[0]['date'], 'window_end': pre[-1]['date'],
    }


def run(stock_name: str, window: int = WINDOW, fmt: str = "pretty") -> None:
    name = normalize_stock_name(stock_name)
    code = lookup_code_for_name(name)
    if not code:
        typer.echo(f"❌ 无法解析 {name} 的代码（试 code_searcher 映射/传入代码）", err=True)
        raise typer.Exit(1)
    ks = _load_klines(code, window + 15)
    if len(ks) < window + 2:
        typer.echo(f"⚠️ {name}({code}) K线不足（{len(ks)} 根）——先跑 fetch-kline-cached {code} -n 60", err=True)
        raise typer.Exit(1)
    p = compute_pulse(ks, window)
    if not p:
        typer.echo(f"⚠️ {name}({code}) K线过短，无法计算脉冲指标", err=True)
        raise typer.Exit(1)

    if fmt == "json":
        import json
        typer.echo(json.dumps({**p, 'stock': name, 'code': code},
                              ensure_ascii=False, indent=2, default=str))
        return

    seg_len = f"拉升段 ¥{p['trough']:.2f} → ¥{p['peak']:.2f} ({p['F']:+.1f}%)"
    pos = (f"现价 ¥{p['px']:.2f} = 段峰 {'+' if p['drawdown'] >= 0 else ''}{p['drawdown']:.1f}%"
           if p['drawdown'] >= 0 else f"现价 ¥{p['px']:.2f} = 段峰下 {-p['drawdown']:.1f}%")
    typer.echo(f"📐 发酵段脉冲检查 {name} ({code})")
    typer.echo(f"   窗口: {p['window_start']} ~ {p['window_end']}（前 {window} 交易日）")
    typer.echo(f"   {seg_len} ｜ 峰日 {p['peak_date']}（距今 {p['days_from_peak']} 交易日）｜ 峰前连涨 {p['consec_up']} 日")
    typer.echo(f"   {pos}")
    typer.echo(f"   状态: {p['state']}")
    typer.echo("   ── 只报不拦（软保护参考）：🟢 尊重趋势 / 🟡 降档谨慎或挂点 / 🔴 禁市价追，挂企稳或放弃")


def register(app):
    @app.command("check-pulse")
    def check_pulse(
        stock_name: str = typer.Argument(..., help="股票名称/代码"),
        window: int = typer.Option(WINDOW, "--window", "-w", help="检查窗口（交易日数）"),
        fmt: str = typer.Option("pretty", "--format", "-f", help="pretty/json"),
    ):
        """发酵段脉冲检查：近 N 日拉升段涨幅(F%)+现价相对段峰位置——买入触发前参考（只报不拦）"""
        run(stock_name, window=window, fmt=fmt)
