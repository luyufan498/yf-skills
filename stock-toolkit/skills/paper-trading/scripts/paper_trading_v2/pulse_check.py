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


def _load_klines(code: str, limit: int = 40, adjust: str = 'raw') -> List[dict]:
    """读 market.db 日K（走读时刷新路径，2026-09-09 修正）。

    原实现直连 SQL 只读缓存 → check-pulse/msg-expiry-scan 会看到陈旧 K
    （sh688041 停在 9/4 仍被当"最新"）。改为 fetch_kline_cached：
    库内最新 >= 最近已收盘交易日 → 纯读库 0 网络；有缺口 → 补抓落库；
    TTL 超期 → 全量重建；抓取失败 → 回退旧缓存（不抛错）。

    adjust='qfq'（2026-09-09 加）：**任何算涨跌幅/回撤/峰谷的消费者都必须用 qfq**
    ——raw 序列在除权日会留下假缺口（如新易盛 6/11 10转4 = 单日 -31.9% raw），
    会让跳水段/拉升段误判为「-30% 暴跌」。check-plunge 已用 qfq；
    check-pulse 沿用 raw（736 段统计的历史口径，改口径需重新校准）。
    """
    from paper_trading_v2.market_cache import fetch_kline_cached, market_db_path
    path = market_db_path()
    if not os.path.exists(path):
        raise typer.Exit(f"market.db 不存在：{path}——先跑 fetch-kline-cached")
    return [dict(x) for x in fetch_kline_cached(code, count=limit, adjust=adjust)]


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
# 消息组试探仓：开槽满 5 交易日后若 ①无发酵(买后5日最高<买价×1.03)
# ②回踩(现价≤买价) ③无新 imp≥4 事件(方向待核) → 论点失效候选（晚审核 C 腿后
# 清退关槽）。水位因子：池紧 5 日即清，池松放宽 T+8/T+10。
# 计时口径（2026-09-09 用户裁决）：**开槽日**起数已收盘 K（非事件日/非成交日）
# ——一槽可并多事件（G3 归并）事件日有歧义，开槽时刻无歧义且段行自带
# （position.opened_at == event_slots.opened_at，**UTC 存，需 +8 折本地**）；
# 开槽比成交早约 1 天，多观察一天无成本。A/B 价格腿仍锚**买价**（成交价），
# A 窗口仍取买后前 5 根 K；C 腿仍从成交日起数（"买后新催化→重置"语义不变）。
# 历史校准（18 笔）：×1.03 抓用户点名 5 只全中，真发酵票(新易盛110%/
# 源杰118%/东方盛虹107%)无一误伤；恒瑞(101.8%平盘)靠 B 腿放行。
NO_FERMENT = 1.03   # A：5 日内最高 < 买价×1.03 = 无发酵
TIGHT_FREE = 400000.0      # 池紧：消息池 free < 40 万
TIGHT_SLOT = 0.75          # 池紧：槽占用 ≥75%


def _newsdb_events_since(stock_code: str, since_ts: str, imp_min: int = 4,
                         exclude_ids: tuple = (), db_path: str | None = None):
    """newsdb 该股成交后 imp≥4 事件明细（晚审 C 腿核验用，库挂返回 None）。

    2026-09-09 修（晚审 0c 口径误计）：since 必须传**完整成交时间戳**。旧版传
    `str(timestamp)[:10]`（纯日期），字符串比较等价于"成交日 00:00 起"→ 成交当日
    盘前入库的**开槽论点事件**被误计成"买后新催化"（生益科技 ND#561 富时罗素
    9/3 08:42:59 早于成交 10:10:11 仍被计入）。SQLite 是字符串比较，故把 ISO 的
    'T' 规范化为空格（`substr(...,1,19)` 去毫秒）保证与 started_at 同格式；
    exclude_ids 排除槽自身论据事件（event_key=ND#<id>，防 started_at 事后订正
    把论据事件重新计进窗口）。
    """
    try:
        if db_path is None:
            from paper_trading_v2.market_cache import market_db_path as _mkp
            p = _mkp()
            db_path = os.path.join(os.path.dirname(os.path.dirname(p)), 'data', 'news', 'news.db')
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT e.id, e.title, e.started_at FROM events e "
            "JOIN event_stock es ON es.event_id=e.id "
            "WHERE es.stock_code=? AND e.importance>=? "
            "AND e.started_at > replace(substr(?,1,19),'T',' ') "
            "ORDER BY e.started_at", (stock_code, imp_min, since_ts)).fetchall()
        conn.close()
        return [dict(r) for r in rows if r['id'] not in exclude_ids]
    except Exception:
        return None


def expiry_scan():
    """扫描消息组 open 槽——论点失效候选 + 水位。"""
    from paper_trading_v2.config import get_workspace_config
    from paper_trading_v2.market_cache import fetch_kline_cached
    db_path = get_workspace_config()['db_path']
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        segs = conn.execute(
            "SELECT id, stock, code, opened_at FROM position "
            "WHERE strategy='NEWS' AND status='open'").fetchall()
        # 槽自身论据事件 id（event_key=ND#<id>）→ C 腿排除用（2026-09-09 口径修）
        slot_ev_ids = {}
        try:
            for sr in conn.execute(
                    "SELECT s.event_key, m.stock FROM event_slots s "
                    "JOIN event_slot_members m ON m.event_key=s.event_key "
                    "WHERE s.status IN ('open','partial')"):
                ek = str(sr['event_key'] or '').strip()
                if not ek.startswith('ND#'):
                    continue
                try:
                    eid = int(ek[3:])
                except ValueError:
                    continue
                for nm in str(sr['stock'] or '').split(','):
                    nm = nm.strip()
                    if nm:
                        slot_ev_ids.setdefault(nm, set()).add(eid)
        except sqlite3.Error:
            pass
        rows = []
        for seg in segs:
            # 开槽日（UTC 存 → +8 折本地，2026-09-09 口径裁决）
            open_date = None
            if seg['opened_at']:
                from datetime import datetime as _dt, timedelta as _td
                try:
                    open_date = (_dt.fromisoformat(str(seg['opened_at'])[:19])
                                 + _td(hours=8)).strftime('%Y-%m-%d')
                except ValueError:
                    open_date = str(seg['opened_at'])[:10]
            # 首 buy（最早时间那笔的价）
            b = conn.execute("SELECT timestamp, price FROM trades WHERE account_id=? "
                             "AND operation='buy' ORDER BY timestamp, id LIMIT 1",
                             (seg['id'],)).fetchone()
            if not b:
                # 2026-09-09：挂单待成交（sleeve-order 已开槽未成交）段无 buy 成交，
                # 原先硬取 b['timestamp'] 会 TypeError 整段崩（ND#743 本川智能 9/8 21:19 触发）
                rows.append({'stock': seg['stock'], 'code': seg['code'],
                             'buy': '—', 'buy_px': '—', 'n': 0,
                             'status': '待成交', 'note': '段已开无buy成交（挂单待成交）'})
                continue
            buy_date = str(b['timestamp'])[:10]
            buy_ts = str(b['timestamp'])          # 完整成交时间戳（C 腿窗口真源）
            buy_px = float(b['price'])
            if not seg['code']:
                rows.append({'stock': seg['stock'], 'status': 'no_code', 'note': '段无代码'})
                continue
            # 读时刷新（2026-09-09）：走 fetch_kline_cached，避免陈旧缓存被当"最新"
            kall = [dict(k) for k in fetch_kline_cached(seg['code'], count=60)]
            # 计时基准=开槽日（口径裁决 2026-09-09）；开槽日缺失回退成交日
            basis = open_date or buy_date
            ks_open = [k for k in kall if k['date'] > basis]
            n_after = len(ks_open)      # 开槽后交易日数（已收盘 K 根数）
            # 价格腿窗口仍从成交日起（买价锚不变）
            ks = [k for k in kall if k['date'] > buy_date]
            if not ks or n_after == 0:
                rows.append({'stock': seg['stock'], 'code': seg['code'],
                             'buy': buy_date, 'buy_px': buy_px, 'n': 0,
                             'status': '观察中', 'note': '买后无K'})
                continue
            win5 = ks[:5]
            max5 = max(k['high'] for k in win5)
            last_c = ks[-1]['close']
            A = max5 < buy_px * NO_FERMENT
            B = last_c <= buy_px
            news_ev = _newsdb_events_since(seg['code'], buy_ts,
                                           exclude_ids=tuple(slot_ev_ids.get(seg['stock'], ())))
            news_n = None if news_ev is None else len(news_ev)
            rows.append({'stock': seg['stock'], 'code': seg['code'],
                         'buy': buy_date, 'buy_px': round(buy_px, 2),
                         'n': n_after, 'max5': round(max5, 2),
                         'max5_r': round(max5 / buy_px * 100, 1),
                         'last': round(last_c, 2), 'last_r': round(last_c / buy_px * 100, 1),
                         'A': A, 'B': B, 'news_imp4': news_n,
                         'news_events': ([f"ND#{e['id']}@{e['started_at']}"
                                          for e in news_ev] if news_ev else []),
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
        conn.close()


def run_expiry_scan(fmt: str = "pretty"):
    rows, wl, _ = expiry_scan()
    if fmt == "json":
        import json
        typer.echo(json.dumps({'rows': rows, 'water': wl}, ensure_ascii=False, indent=2, default=str))
        return
    typer.echo(f"📡 消息槽论点失效扫描（T+5，A=5日最高<买价×{NO_FERMENT} B=现价≤买价 C=无imp4新事件(方向待核)）")
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
        print(f"  {r['stock']:<7} {r['buy']} 买¥{r['buy_px']:<8.2f} T+{r['n']}日 "
              f"至今最高¥{r['max5']:<8.2f} 现¥{r['last']:<8.2f} {r['status']}")
        if r['A'] or r['B'] or r['n'] >= 5:
            detail = []
            if r['A']: detail.append(f"A无发酵(峰{r['max5_r']}%<{NO_FERMENT*100:.0f}%)")
            if r['B']: detail.append(f"B回踩(现{r['last_r']}%≤100%)")
            imp = r['news_imp4']
            if imp is None: detail.append("C新闻库未连")
            elif imp > 0:
                evs = ' '.join(r.get('news_events') or [])
                detail.append(f"C有imp4事件×{imp} {evs}(方向/时序待核)".rstrip())
            else: detail.append("C无新事件")
            print(f"            {' '.join(detail)}")


def _register_expiry(app):
    @app.command("msg-expiry-scan")
    def msg_expiry_scan(fmt: str = typer.Option("pretty", "--format", "-f", help="pretty/json")):
        """消息槽 T+5 论点失效扫描：买入满 5 交易日无发酵+回踩+无新imp4事件 → 清退候选（晚审 0c 步）"""
        run_expiry_scan(fmt=fmt)


# ============================================================
# 跳水段检查（check-plunge，2026-09-09 样本外定稿）
# ============================================================
# 镜像 check-pulse 的拉升段逻辑：窗口内先找最低 low（trough），再向左侧找
# 最高 high（peak）→ 跳水段；输出 depth/pdays/speed/rdays/rb_pct。
# 样本外实测（241 只随机 A 股 / 104,928 快照 / 445 交易日，同日配对消 regime）：
#   「有跳水段(depth≤-15%) − 无跳水段」fwd5 +1.28pp(t 2.62)、fwd10 +1.68pp(t 2.79)、
#   fwd20 +1.21pp(t 1.26，衰减）；「中速最强」「极深必避」两个池内细节参数**未复现**。
#   → 因此只作技术组候选排序参考（软加分，只影响先看谁），不构成门控；
#     消息组禁用（宪法 2.6：技术面入场门禁用于消息组）。
PLUNGE_WINDOW = 60          # 窗口交易日
PLUNGE_MIN = -15.0          # depth ≤ -15% → 有跳水段
PLUNGE_STEEP = -2.5         # ≤ -2.5%/日 → 极急跌（样本外最弱）
PLUNGE_MID = (-1.5, -0.8)   # 中速档（池内最强；样本外未复现，仅排序用）
PLUNGE_FRESH_RDAYS = 20     # 低点距今 ≤20 交易日 = 反弹新鲜（2026-09-09 实证：>20 显著变差）


def compute_plunge(ks: List[dict], window: int = PLUNGE_WINDOW) -> Optional[dict]:
    """窗口内先找最低 low（trough），再向左侧找最高 high（peak）→ 跳水段指标。"""
    if len(ks) < window + 2:
        return None
    win = ks[-window:]
    ti = min(range(len(win)), key=lambda i: win[i]['low'])
    trough = win[ti]['low']
    pi = max(range(ti + 1), key=lambda i: win[i]['high'])
    peak = win[pi]['high']
    if not trough or not peak:
        return None
    depth = (trough / peak - 1) * 100
    pdays = ti - pi
    speed = depth / pdays if pdays > 0 else 0.0
    rdays = len(win) - 1 - ti
    px = win[-1]['close']
    has = depth <= PLUNGE_MIN
    fresh = rdays <= PLUNGE_FRESH_RDAYS
    if not has:
        state, tag = '⬜ 无跳水段（甜点区排序降级）', 'none'
    elif not fresh:
        state, tag = f'🟠 反弹陈旧（低点距今 {rdays} 交易日 > {PLUNGE_FRESH_RDAYS}，不作加分）', 'stale'
    elif speed <= PLUNGE_STEEP:
        state, tag = '🔴 极急跌（样本外最弱档）', 'steep'
    elif PLUNGE_MID[0] <= speed <= PLUNGE_MID[1]:
        state, tag = '🟢 中速跳水段（排序优先）', 'mid'
    else:
        state, tag = '🟡 有跳水段', 'has'
    return {'depth': depth, 'pdays': pdays, 'speed': speed, 'rdays': rdays,
            'fresh': fresh, 'fresh_limit': PLUNGE_FRESH_RDAYS,
            'rb_pct': (px / trough - 1) * 100 if trough else 0.0,
            'px': px, 'trough': trough, 'peak': peak,
            'peak_date': win[pi]['date'], 'trough_date': win[ti]['date'],
            'window_start': win[0]['date'], 'window_end': win[-1]['date'],
            'has_plunge': has, 'state': state, 'tag': tag}


def run_plunge(stock_name: str, window: int = PLUNGE_WINDOW, fmt: str = "pretty") -> None:
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
    ks = _load_klines(code, window + 15, adjust='qfq')
    if len(ks) < window + 2:
        typer.echo(f"⚠️ {name}({code}) K线不足（{len(ks)} 根）——先跑 fetch-kline-cached {code} -n {window + 15}",
                   err=True)
        raise typer.Exit(1)
    p = compute_plunge(ks, window)
    if not p:
        typer.echo(f"⚠️ {name}({code}) K线过短，无法计算跳水段", err=True)
        raise typer.Exit(1)
    if fmt == "json":
        import json
        typer.echo(json.dumps({**p, 'stock': name, 'code': code},
                              ensure_ascii=False, indent=2, default=str))
        return
    typer.echo(f"🕳️ 跳水段检查 {name} ({code})")
    typer.echo(f"   窗口: {p['window_start']} ~ {p['window_end']}（前 {window} 交易日）")
    typer.echo(f"   段: 峰 ¥{p['peak']:.2f}({p['peak_date']}) → 谷 ¥{p['trough']:.2f}"
               f"({p['trough_date']}) = {p['depth']:+.1f}% ｜ 历时 {p['pdays']} 交易日"
               f" ｜ 速度 {p['speed']:+.2f}%/日")
    typer.echo(f"   离低点 {p['rdays']} 交易日，反弹 {p['rb_pct']:+.1f}%（现价 ¥{p['px']:.2f}）")
    typer.echo(f"   反弹新鲜度: " + ("✅ 新鲜（≤%d 交易日）" % p['fresh_limit'] if p['fresh']
               else "⚠️ 陈旧（>%d 交易日）→ 不作加分" % p['fresh_limit'])
               + " ｜ 实证：甜点区内低点近(≤20)−远(>20) fwd20 +3.9pp (t 3.5)")
    typer.echo(f"   状态: {p['state']}")
    typer.echo("   ── 只作技术组候选排序参考（软加分，只影响先看谁）；不构成门控；"
               "消息组禁用（宪法 2.6）")


def _register_plunge(app):
    @app.command("check-plunge")
    def check_plunge(
        stock_name: str = typer.Argument(..., help="股票名称/代码"),
        window: int = typer.Option(PLUNGE_WINDOW, "--window", "-w", help="检查窗口（交易日数）"),
        fmt: str = typer.Option("pretty", "--format", "-f", help="pretty/json"),
    ):
        """跳水段检查：窗口内峰→谷跌幅/速度/离低点天数/反弹幅度——技术组排序参考（只报不拦）"""
        run_plunge(stock_name, window=window, fmt=fmt)
