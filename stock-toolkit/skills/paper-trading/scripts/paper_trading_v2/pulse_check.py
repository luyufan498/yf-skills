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
    # 拉升结构（2026-09-08 修正——原"从峰倒数连涨"被峰前阴线打断失真，
    # 星网案例：三连板但 9/1 阴线夹断 → 报 0 误导）：
    # 窗口内最大连续阳线段 + 涨停计数，捕捉连板脉冲形态
    max_run, run = 0, 0
    run_start = run_end = None
    cur_start = None
    for k in range(len(pre)):
        if pre[k]['close'] >= pre[k]['open']:
            if run == 0:
                cur_start = k
            run += 1
            if run > max_run:
                max_run = run
                run_start, run_end = cur_start, k
        else:
            run = 0
    limit_ups = sum(1 for k in range(1, len(pre))
                    if pre[k]['close'] >= pre[k - 1]['close'] * 1.095)
    big_ups = sum(1 for k in range(1, len(pre))
                  if 0.05 <= pre[k]['close'] / pre[k - 1]['close'] - 1 < 0.095)
    rs, re = run_start, run_end
    if rs is not None and re is not None and max_run >= 2:
        seg0, seg1 = pre[rs], pre[re]
        run_gain = (seg1['close'] - seg0['open']) / seg0['open'] * 100
        struct = (f"连阳{max_run}日({seg0['date'][5:]}~{seg1['date'][5:]},"
                  f" +{run_gain:.0f}%)")
    else:
        struct = "无明显连阳段"
    if limit_ups:
        struct += f" ｜ 涨停×{limit_ups}"
    elif big_ups:
        struct += f" ｜ 大涨×{big_ups}"
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
        'structure': struct, 'limit_ups': limit_ups, 'px': px,
        'state': state, 'tag': st_tag,
        'window_start': pre[0]['date'], 'window_end': pre[-1]['date'],
    }


def run(stock_name: str, window: int = WINDOW, fmt: str = "pretty") -> None:
    from paper_trading_v2.code_searcher import looks_like_stock_code, canonical_stock_code
    if looks_like_stock_code(stock_name):
        code = canonical_stock_code(stock_name)
        name = code
    else:
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
    typer.echo(f"   {seg_len} ｜ 峰日 {p['peak_date']}（距今 {p['days_from_peak']} 交易日）｜ {p['structure']}")
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


# ============================================================
# T+5 论点失效扫描（msg-expiry-scan，2026-09-08 晚审 0c 步）
# ============================================================
# 消息组试探仓：买入满 5 交易日后若 ①无发酵(5日最高<买价×1.03)
# ②回踩(现价≤买价) ③无新 imp≥4 利好 → 论点失效候选（晚审核 C 腿后
# 自动清退关槽）。水位因子：池紧 5 日即清，池松放宽 T+8/T+10。
# 历史校准（18 笔）：×1.03 抓用户点名 5 只全中，真发酵票(新易盛110%/
# 源杰118%/东方盛虹107%)无一误伤；恒瑞(101.8%平盘)靠 B 腿放行。
NO_FERMENT = 1.03   # A：5 日内最高 < 买价×1.03 = 无发酵
TIGHT_FREE = 400000.0      # 池紧：消息池 free < 40 万
TIGHT_SLOT = 0.75          # 池紧：槽占用 ≥75%


def _newsdb_events_since(stock_code: str, since_date: str, imp_min: int = 4):
    """newsdb 该股买入后 imp≥4 事件数 hint（晚审 C 腿核验用，库挂返回 None）。"""
    try:
        from paper_trading_v2.market_cache import market_db_path as _mkp
        p = _mkp()
        db = os.path.join(os.path.dirname(os.path.dirname(p)), 'data', 'news', 'news.db')
        if not os.path.exists(db):
            return None
        conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        n = conn.execute(
            "SELECT COUNT(*) n FROM events e JOIN event_stock es ON es.event_id=e.id "
            "WHERE es.stock_code=? AND e.importance>=? AND e.started_at>?",
            (stock_code, imp_min, since_date)).fetchone()[0]
        conn.close()
        return n
    except Exception:
        return None


def expiry_scan():
    """扫描消息组 open 槽——论点失效候选 + 水位。"""
    from paper_trading_v2.config import get_workspace_config
    from paper_trading_v2.market_cache import market_db_path
    db_path = get_workspace_config()['db_path']
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    mkt = sqlite3.connect(f'file:{market_db_path()}?mode=ro', uri=True)
    mkt.row_factory = sqlite3.Row
    try:
        segs = conn.execute(
            "SELECT id, stock, code, opened_at FROM position "
            "WHERE strategy='NEWS' AND status='open'").fetchall()
        rows = []
        for seg in segs:
            buy = conn.execute(
                "SELECT MIN(timestamp) bt, price FROM trades WHERE account_id=? "
                "AND operation='buy' GROUP BY price ORDER BY bt LIMIT 1",
                (seg['id'],)).fetchone()
            if not buy:
                continue
            # 首 buy（最早时间那笔的价）
            b = conn.execute("SELECT timestamp, price FROM trades WHERE account_id=? "
                             "AND operation='buy' ORDER BY timestamp, id LIMIT 1",
                             (seg['id'],)).fetchone()
            buy_date = str(b['timestamp'])[:10]
            buy_px = float(b['price'])
            if not seg['code']:
                rows.append({'stock': seg['stock'], 'status': 'no_code', 'note': '段无代码'})
                continue
            ks = [dict(k) for k in mkt.execute(
                "SELECT date,high,low,close FROM kline_daily WHERE code=? AND date>? "
                "ORDER BY date", (seg['code'], buy_date))]
            n_after = len(ks)          # 买入后交易日数（K 根数）
            if n_after == 0:
                rows.append({'stock': seg['stock'], 'code': seg['code'],
                             'buy': buy_date, 'buy_px': buy_px, 'n': 0,
                             'status': '观察中', 'note': '买后无K'})
                continue
            win5 = ks[:5]
            max5 = max(k['high'] for k in win5)
            last_c = ks[-1]['close']
            A = max5 < buy_px * NO_FERMENT
            B = last_c <= buy_px
            news_n = _newsdb_events_since(seg['code'], buy_date)
            rows.append({'stock': seg['stock'], 'code': seg['code'],
                         'buy': buy_date, 'buy_px': round(buy_px, 2),
                         'n': n_after, 'max5': round(max5, 2),
                         'max5_r': round(max5 / buy_px * 100, 1),
                         'last': round(last_c, 2), 'last_r': round(last_c / buy_px * 100, 1),
                         'A': A, 'B': B, 'news_imp4': news_n,
                         'status': ('🔴论点失效候选' if (A and B and n_after >= 5)
                                    else '🟢观察中')})
        # 水位
        sleeve = conn.execute("SELECT free,total FROM sleeve_ledger WHERE id=1").fetchone()
        slot_n = conn.execute("SELECT COUNT(*) n FROM event_slots WHERE status IN ('open','partial')").fetchone()['n']
        free, total = sleeve['free'], sleeve['total']
        tight = free < TIGHT_FREE or slot_n / 20.0 >= TIGHT_SLOT
        return rows, {'free': free, 'total': total, 'slots': slot_n,
                      'tight': tight}, db_path
    finally:
        conn.close(); mkt.close()


def run_expiry_scan(fmt: str = "pretty"):
    rows, wl, _ = expiry_scan()
    if fmt == "json":
        import json
        typer.echo(json.dumps({'rows': rows, 'water': wl}, ensure_ascii=False, indent=2, default=str))
        return
    typer.echo(f"📡 消息槽论点失效扫描（T+5，A=5日最高<买价×{NO_FERMENT} B=现价≤买价 C=无imp4利好）")
    typer.echo(f"   水位: 消息池 free ¥{wl['free']:,.0f}/{wl['total']:,.0f} ｜ 槽占用 {wl['slots']}/20 "
               f"｜ {'🔴 池紧——5 日即清（腾坑）' if wl['tight'] else '🟢 池松——可放宽 T+8/10'}")
    typer.echo("")
    for r in rows:
        if r['status'] == 'no_code':
            print(f"  ⚠️ {r['stock']}: {r['note']}")
            continue
        if r['n'] == 0:
            print(f"  {r['stock']:<7} {r['buy']} 买¥{r['buy_px']:<8} {r['status']}（{r['note']}）")
            continue
        tag = f"{r['max5_r']:>5.1f}%/{r['last_r']:>5.1f}%" if r['n'] >= 1 else "  n/a"
        print(f"  {r['stock']:<7} {r['buy']} 买¥{r['buy_px']:<8.2f} T+{r['n']}日 "
              f"至今最高¥{r['max5']:<8.2f} 现¥{r['last']:<8.2f} {r['status']}")
        if r['A'] or r['B'] or r['n'] >= 5:
            detail = []
            if r['A']: detail.append(f"A无发酵(峰{r['max5_r']}%<{NO_FERMENT*100:.0f}%)")
            if r['B']: detail.append(f"B回踩(现{r['last_r']}%≤100%)")
            imp = r['news_imp4']
            if imp is None: detail.append("C新闻库未连")
            elif imp > 0: detail.append(f"C有imp4利好×{imp}(可重置/agent核)")
            else: detail.append("C无新利好")
            print(f"            {' '.join(detail)}")


def _register_expiry(app):
    @app.command("msg-expiry-scan")
    def msg_expiry_scan(fmt: str = typer.Option("pretty", "--format", "-f", help="pretty/json")):
        """消息槽 T+5 论点失效扫描：买入满 5 交易日无发酵+回踩+无新利好 → 清退候选（晚审 0c 步）"""
        run_expiry_scan(fmt=fmt)
