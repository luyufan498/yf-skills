"""ptrade2 CLI — master-pool / watchlist / sleeve / 交易命令组"""
import sys

import typer
from typing import Optional

from paper_trading_v2.helpers import normalize_stock_name, get_stock_name_suggestions, auto_exright_check

app = typer.Typer(help="ptrade2 — SQLite 深迁移 + 弹性组合总池 + 消息组事件槽",
                  add_completion=False, no_args_is_help=True)


def _cli_gate_precheck(argv: list):
    """能力矩阵前置闸（sleeve-m1，方案 2.5/3.2）：typer callback 里对 conditions 写操作
    与 buy 做 grp 路由检查；master-pool-allocate/topup 的拦截在 MasterPoolManager 库层。"""
    import os
    from paper_trading_v2 import gate as gate_mod
    from paper_trading_v2.gate import enforce, conditions_write_actions

    args = [a for a in argv[1:] if a != '--']
    if not args:
        return
    cmd = args[0]
    if cmd == 'conditions':
        action = None
        for i, t in enumerate(args[1:], 1):
            if t in ('--action', '-a') and i + 1 < len(args):
                action = args[i + 1]
            elif t.startswith('--action='):
                action = t.split('=', 1)[1]
        if action in conditions_write_actions():
            stock = next((t for t in args[1:] if not t.startswith('-')), None)
            if stock:
                enforce(normalize_stock_name(stock), 'conditions_write')
    elif cmd == 'buy':
        stock = next((t for t in args[1:] if not t.startswith('-')), None)
        if stock:
            enforce(normalize_stock_name(stock), 'buy')


@app.callback()
def _gate_callback(ctx: typer.Context):
    """全局前置闸：消息组（grp=news）账户能力矩阵（违例报错+shadow_log gate_violation）。"""
    try:
        args = list(ctx.args or []) or sys.argv[1:]
        _cli_gate_precheck(args)
    except ValueError as e:          # GateViolation
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command()
def version():
    """显示版本信息"""
    from paper_trading_v2 import __version__
    typer.echo(f"paper-trading v{__version__}")


# ============ master-pool 命令组 ============

@app.command("master-pool-init")
def master_pool_init(
    amount: float = typer.Option(..., "--amount", help="总池初始资金"),
):
    """初始化总池（一次性）"""
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    try:
        mpm.init_pool(amount)
        typer.echo(f"✅ 总池初始化成功：¥{amount:,.0f}")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("master-pool-show")
def master_pool_show(
    pool: str = typer.Option("main", "--pool", help="main=趋势池 / sleeve=消息池"),
):
    """总池状态"""
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager(pool=pool)
    d = mpm.show()
    if 'error' in d:
        typer.echo(d['error'], err=True)
        raise typer.Exit(1)
    label = '总池' if pool == 'main' else '消息池'
    typer.echo(f"💰 {label}：¥{d['total']:,.0f} ｜ 空闲：¥{d['free']:,.0f}")
    typer.echo(f"   已分配（open段）：¥{d['occupied']:,.0f} ({d['usage_rate']*100:.0f}%)")
    typer.echo(f"   已实现盈亏（进池）：¥{d['realized_pnl']:,.0f} ｜ 活跃段：{d['open_segments']}")
    if pool == 'sleeve':
        typer.echo(f"   活跃事件槽：{d['active_slots']}/20 ｜ 待成交单：{d['pending_slots']}")


@app.command("master-pool-allocate")
def master_pool_allocate(
    stock: str = typer.Argument(...),
    amount: float = typer.Option(..., "--amount", help="分配金额"),
    reason: str = typer.Option("", "--reason", help="原因（审计）"),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
    code: Optional[str] = typer.Option(None, "--code", help="股票代码（可选）"),
):
    """开持仓段（从 free 拨 budget；--code 缺失时自动查码补全）"""
    stock = normalize_stock_name(stock)
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    try:
        code = _ensure_code(stock, code)
        mpm.allocate(stock, amount, reason, source=source, code=code)
        typer.echo(f"✅ 分配 {stock} ¥{amount:,.0f} ({code})")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("master-pool-topup")
def master_pool_topup(
    stock: str = typer.Argument(...),
    amount: float = typer.Option(..., "--amount"),
    reason: str = typer.Option("", "--reason"),
    source: str = typer.Option("agent", "--source", help="agent/manual；migrate=迁移票承接注资"
                                                              "（豁免甜点动量检查，资金帽/段位帽/冷却不豁免）"),
):
    """段内注资（迁移票承接须 --source migrate，宪法 2.6）"""
    stock = normalize_stock_name(stock)
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    try:
        mpm.topup(stock, amount, reason, source=source)
        typer.echo(f"✅ 注资 {stock} ¥{amount:,.0f}"
                   + ("（迁移承接：甜点动量检查豁免，资金帽/段位帽/冷却不豁免）"
                      if source == 'migrate' else ""))
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("master-pool-release")
def master_pool_release(
    stock: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason"),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
):
    """关持仓段（空仓回池）"""
    stock = normalize_stock_name(stock)
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    try:
        mpm.release(stock, reason, source=source)
        typer.echo(f"✅ 释放 {stock} 回池")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("master-pool-records")
def master_pool_records(
    days: Optional[int] = typer.Option(None, "--days"),
):
    """资金流水审计"""
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    for r in mpm.records(days):
        typer.echo(f"{r['timestamp'][:10]} {r['action']:8} {str(r['stock'] or ''):8} "
                   f"¥{r['amount'] or 0:,.0f}  free {r['free_before'] or 0:,.0f}→{r['free_after'] or 0:,.0f}  "
                   f"{r['reason']}")


@app.command("reconcile")
def reconcile_cmd(
    detail: bool = typer.Option(False, "--detail", help="逐段列出段现金恒等式核对"),
):
    """U7.5 资金恒等式对账（只报不拦）：主池free+消息池free+Σopen段budget vs 总资金。

    容差 = |Σ closed 段 cash|（滞留在死壳段、未回池的资金，如沃森生物费用损失）
        + Σ 非 buy/sell/init/exright 类型 operations 金额（费税项）。
    超差 → stdout ⚠️ 告警（不拦截任何操作）；接心跳尾步与晨审（cron prompt 文案属 M2 窗口）。
    同时逐段核对段现金恒等式：cash + FIFO成本 − realized == budget（±0.01）。
    M1.8/R2 总量守恒门：双池 Σtotal vs 注入基准 W_BASE——init 配对划拨洞/越权直写
    （凭空印钱路径，W 恒等式抓不到：W 与 Σtotal 同步膨胀 drift 恒=0）在此现形。
    """
    from paper_trading_v2.db import get_connection, migrate_db, grp_of_strategy
    from paper_trading_v2.master_pool import W_BASE
    from paper_trading_v2.config import get_workspace_config
    conn = get_connection(get_workspace_config()['db_path'])
    migrate_db(conn)
    try:
        ledger = lambda t: (conn.execute(f"SELECT total, free FROM {t} WHERE id=1").fetchone()
                            or {'total': 0.0, 'free': 0.0})
        main = ledger('pool_ledger')
        sleeve = ledger('sleeve_ledger')
        open_budget = conn.execute(
            "SELECT COALESCE(SUM(budget),0) FROM position WHERE status='open'").fetchone()[0]
        closed_cash = conn.execute(
            "SELECT COALESCE(SUM(cash),0) FROM position WHERE status='closed'").fetchone()[0]
        odd_ops = conn.execute(
            "SELECT COALESCE(SUM(ABS(COALESCE(amount,0))),0) FROM operations WHERE type NOT IN "
            "('buy','sell','init','exright_bonus','exright_dividend')").fetchone()[0]
        total = main['total'] + sleeve['total']
        w = main['free'] + sleeve['free'] + open_budget
        drift = w - total
        tolerance = abs(closed_cash) + (odd_ops or 0.0)
        typer.echo("🔍 资金恒等式对账（U7.5，只报不拦）")
        typer.echo(f"   主池 total/free: ¥{main['total']:,.2f} / ¥{main['free']:,.2f} ｜ "
                   f"消息池 total/free: ¥{sleeve['total']:,.2f} / ¥{sleeve['free']:,.2f}")
        typer.echo(f"   Σ open 段 budget: ¥{open_budget:,.2f}")
        typer.echo(f"   W = free(双池) + Σopen段budget = ¥{w:,.2f} ｜ 总资金 = ¥{total:,.2f}")
        typer.echo(f"   偏差 drift = ¥{drift:,.2f} ｜ 容差 = ¥{tolerance:,.2f} "
                   f"（closed 段滞留现金 ¥{closed_cash:,.2f} + 费税项 ¥{odd_ops or 0:,.2f}）")
        if abs(drift) > tolerance + 0.005:
            typer.echo(f"⚠️ 超差告警：|drift| {abs(drift):,.2f} > 容差 {tolerance:,.2f}"
                       f"——账本/段现金存在未入账资金流，需人工核查（本命令只报不拦）")
        else:
            typer.echo("✅ 恒等式在容差内")
        # M1.8/R2 总量守恒门（抓一切印钱路径，含历史）：双池 Σtotal 必须恒=注入基准 W_BASE。
        # 旧 init 洞只 INSERT sleeve_ledger 不扣主池 → Σtotal 膨胀，但 W 同步膨胀 drift 恒=0，
        # 上面那条 drift 门抓不到——守恒门补位（只报不拦，与 drift 告警同形式）。
        conservation = total - W_BASE
        typer.echo(f"   总量守恒：Σtotal(双池) ¥{total:,.2f} ｜ 注入基准 W_BASE ¥{W_BASE:,.2f}")
        if abs(conservation) > 0.005:
            typer.echo(f"⚠️ 超差告警：总量守恒破坏——Σtotal(双池) ¥{total:,.2f} ≠ "
                       f"注入基准 ¥{W_BASE:,.2f}（Δ {conservation:+,.2f}）——存在未配对划拨/"
                       f"越权直写（消息池 init 未经主池扣减或手工改账），需人工核查"
                       f"（本命令只报不拦）")
        else:
            typer.echo("✅ 总量守恒（Σtotal = 注入基准 W_BASE）")
        # 段现金恒等式（U5 不变量）：cash + FIFO成本 − realized == budget
        from paper_trading_v2.sleeve_slots import account_remaining
        bad = []
        for seg in conn.execute("SELECT id, stock, strategy, status, budget, cash, "
                                "realized_pnl FROM position ORDER BY id"):
            qty, fifo_cost = account_remaining(conn, seg['id'])
            realized = seg['realized_pnl'] or 0.0
            ident = (seg['cash'] or 0.0) + fifo_cost - realized
            base = seg['budget'] or 0.0
            if seg['status'] == 'open' and abs(ident - base) > 0.01:
                bad.append((seg['id'], seg['stock'], ident, base, seg['cash'], fifo_cost))
        typer.echo(f"   段现金恒等式（open 段 cash+FIFO−realized==budget）："
                   f"{'全部成立 ✅' if not bad else f'{len(bad)} 段破恒等 ❌'}")
        for seg_id, stock, ident, base, cash, fifo in bad:
            typer.echo(f"     ❌ 段#{seg_id} {stock}: cash ¥{cash:,.2f} + FIFO ¥{fifo:,.2f} "
                       f"− realized = ¥{ident:,.2f} ≠ budget ¥{base:,.2f}")
        if detail:
            typer.echo("   —— 逐段明细 ——")
            for seg in conn.execute("SELECT id, stock, strategy, status, budget, cash, "
                                    "realized_pnl FROM position ORDER BY id"):
                qty, fifo_cost = account_remaining(conn, seg['id'])
                typer.echo(f"     段#{seg['id']:<3} {seg['stock']:<6} {grp_of_strategy(seg['strategy']):<4} "
                           f"{seg['status']:<6} budget ¥{seg['budget'] or 0:,.0f} ｜ cash ¥{seg['cash'] or 0:,.2f} "
                           f"｜ FIFO {qty}股/¥{fifo_cost:,.2f} ｜ realized ¥{seg['realized_pnl'] or 0:,.2f}")
    finally:
        conn.close()


# ============ watchlist 命令组 ============

@app.command("watchlist-list")
def watchlist_list():
    """池名单"""
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist()
    for s in w.list():
        typer.echo(f"  {s['strategy']}  {s['stock']}  {str(s['code'] or '')}")


def _ensure_code(stock: str, code: Optional[str]) -> str:
    """自动查码兜底：--code 缺失时先查本地 pool/position，再触网查码补全；都查不到才拒绝。

    2026-08-25 加入：堵住"入池/建段不带 code"导致网站代码列空白 + 无法取价的坑。
    v9：本地兜底源=pool 表 + position 段表（accounts 已退役，段即账户）。
    """
    if code and code.strip():
        return code.strip()
    # 1) 本地兜底：pool 表 / position 段表已有该股 code（离线也可靠）
    try:
        from paper_trading_v2.db import get_connection
        from paper_trading_v2.watchlist import Watchlist
        w = Watchlist()
        conn = get_connection(w.db_path)
        row = conn.execute(
            "SELECT COALESCE((SELECT code FROM pool WHERE stock=? LIMIT 1), "
            "(SELECT code FROM position WHERE stock=? AND code IS NOT NULL LIMIT 1)) AS c",
            (stock, stock),
        ).fetchone()
        conn.close()
        if row and row["c"]:
            return row["c"]
    except Exception:
        pass
    # 2) 触网查码（code_searcher 腾讯 sug）
    try:
        from paper_trading_v2.code_searcher import validate_stock_name
        ok, found = validate_stock_name(stock)
        if ok and found:
            typer.echo(f"ℹ️ 自动补全代码：{stock} → {found}")
            return found
    except Exception:
        pass
    raise ValueError(
        f"无法自动获取 {stock} 的股票代码（未传 --code 且本地/网络查码均失败）。"
        f"请手动指定 --code <代码>（如 sh600118 / sz300308）"
    )


@app.command("watchlist-add")
def watchlist_add(
    stock: str = typer.Argument(...),
    code: Optional[str] = typer.Option(None, "--code"),
    strategy: str = typer.Option("L2", "--strategy", help="L1/L2/L3/NEWS"),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
    reason: str = typer.Option("", "--reason"),
    pin: Optional[bool] = typer.Option(None, "--pin/--no-pin", help="设置/清除名单保护（pin=1 禁删除可降级）"),
    event_key: Optional[str] = typer.Option(None, "--event-key", help="消息组 G3 事件键（NEWS 收编必带，如 ND#293）"),
    news_kind: Optional[str] = typer.Option(None, "--news-kind", help="六类词表：price_cycle/policy/earnings/company_event/tech_catalyst/sentiment/other"),
):
    """入池/调整档位（--pin 名单保护；--code 自动查码；NEWS 收编带 --event-key/--news-kind
    并过 G2 次新否决：上市 <40 交易日硬拒绝）"""
    stock = normalize_stock_name(stock)
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist()
    try:
        code = _ensure_code(stock, code)
        # G2 次新否决（消息组硬闸）：NEWS 收编不足 40 交易日直接拒绝；
        # 技术组（L1/L2/L3）仅提示不改行为（G2 属消息组清单闸，方案 2.2）
        from paper_trading_v2.helpers import check_listing_age
        ok, detail = check_listing_age(stock, code)
        if not ok:
            if strategy == 'NEWS':
                typer.echo(f"❌ {detail}", err=True)
                raise typer.Exit(1)
            typer.echo(f"ℹ️ {detail}（技术组仅提示）")
        w.add(stock, code, strategy, source, reason, pin,
              event_key=event_key, news_kind=news_kind)
        typer.echo(f"✅ 入池 {stock} ({strategy})" + (" 🔒pin" if pin else ""))
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("watchlist-log")
def watchlist_log(
    stock: Optional[str] = typer.Option(None, "--stock", help="只看某只股票"),
    days: int = typer.Option(30, "--days", help="最近 N 天"),
    limit: int = typer.Option(50, "--limit"),
):
    """名单变更审计日志（入池/出池/升降级历史，含原因与来源）。"""
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist()
    rows = w.log(stock=stock, days=days, limit=limit)
    if not rows:
        typer.echo("(无记录)")
        return
    for r in rows:
        flow = f"{r['strategy_from'] or ''}→{r['strategy_to'] or ''}"
        typer.echo(f"{r['timestamp'][:16]}  {r['action']:<12} {r['stock']:<8} {flow:<6} "
                   f"[{r['source']}] {r['reason'] or ''}")


@app.command("watchlist-remove")
def watchlist_remove(
    stock: str = typer.Argument(...),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
    reason: str = typer.Option("", "--reason"),
    archive: bool = typer.Option(False, "--archive/--removed", help="archive=档案化终态"
                                 "（清仓不回池，方案 2.6b）；removed=旧语义（默认）"),
):
    """移出池（默认旧 removed 语义；--archive 走档案化终态）"""
    stock = normalize_stock_name(stock)
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist()
    try:
        w.remove(stock, source, reason, archive=archive)
        typer.echo(f"✅ {'档案化' if archive else '移出'} {stock}"
                   + ("（archived 终态）" if archive else ""))
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


# ============ sleeve 命令组（消息组事件槽，sleeve-m1） ============

@app.command("sleeve-pool-init")
def sleeve_pool_init(
    amount: float = typer.Option(..., "--amount", help="消息池初始资金（≤主池 20%，从主池划拨）"),
):
    """初始化消息池（一次性；资金=从主池配对划拨，主池同事务扣减，M1.8/R1）"""
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager(pool='sleeve')
    try:
        mpm.init_pool(amount)
        typer.echo(f"✅ 消息池初始化成功：¥{amount:,.0f}（从主池划拨，上限 20% 主池）")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("sleeve-show")
def sleeve_show():
    """消息池状态 + 事件槽清单"""
    from paper_trading_v2.db import get_connection, migrate_db
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager(pool='sleeve')
    d = mpm.show()
    if 'error' in d:
        typer.echo(d['error'], err=True)
        raise typer.Exit(1)
    typer.echo(f"💰 消息池：¥{d['total']:,.0f} ｜ 空闲：¥{d['free']:,.0f}")
    typer.echo(f"   槽占用：¥{d['occupied']:,.0f} ({d['usage_rate']*100:.0f}%) ｜ "
               f"活跃槽 {d['active_slots']}/20 ｜ 待成交 {d['pending_slots']} ｜ "
               f"成员段 {d['open_segments']}")
    typer.echo(f"   已实现盈亏：¥{d['realized_pnl']:,.0f}")
    conn = get_connection(mpm.db_path)
    migrate_db(conn)
    try:
        rows = conn.execute(
            "SELECT event_key,status,fill_status,budget,realized,news_kind,members_json,"
            "invalidation,topup_locked,opened_at FROM event_slots "
            "ORDER BY (status IN ('open','partial')) DESC, opened_at DESC").fetchall()
        if not rows:
            typer.echo("📭 无事件槽")
            return
        typer.echo("📋 事件槽：")
        for r in rows:
            lock = " 🔒" if r['topup_locked'] else ""
            flag = f" ⚑失效:{r['invalidation']}" if r['invalidation'] else ""
            typer.echo(f"  [{r['status']:>8}/{r['fill_status'] or '-':>8}] {r['event_key']}  "
                       f"¥{(r['budget'] or 0):,.0f}  已实现¥{(r['realized'] or 0):,.0f}  "
                       f"{r['news_kind'] or '-'}  成员 {r['members_json'] or '[]'}{lock}{flag}")
    finally:
        conn.close()


@app.command("sleeve-open")
def sleeve_open(
    stocks: list[str] = typer.Argument(..., help="成员股票（可多只，等权）"),
    budget: float = typer.Option(..., "--budget", help="本事件槽预算（从消息池拨付）"),
    event_key: Optional[str] = typer.Option(None, "--event-key", help="G3 事件键（如 ND#293；缺省 fail-open 生成兜底键）"),
    news_kind: Optional[str] = typer.Option(None, "--news-kind", help="六类词表打标"),
    title: Optional[str] = typer.Option(None, "--title", help="事件标题"),
    reason: str = typer.Option("", "--reason", help="判定依据（审计）"),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
    code: list[str] = typer.Option(None, "--code", help="成员代码，顺序对应成员（可多次）"),
):
    """消息组 L1 建仓事务：sleeve_ledger 扣款→成员账户(grp=news)→pending 待成交单→开槽
    （G3 归并：活跃槽并入等权不加坑；关闭槽二波=新键开新槽）"""
    stocks = [normalize_stock_name(s) for s in stocks]
    from paper_trading_v2.sleeve_open import SleeveOpener
    code_map = {}
    if code:
        for i, c in enumerate(code):
            if i < len(stocks):
                code_map[stocks[i]] = c.strip()
    try:
        op = SleeveOpener()
        if not code_map:
            for s in stocks:
                try:
                    code_map[s] = _ensure_code(s, None)
                except ValueError:
                    pass
        r = op.open_slot(stocks, budget, event_key=event_key, news_kind=news_kind,
                         source=source, reason=reason, title=title, code_map=code_map)
        typer.echo(f"✅ sleeve-open [{r['mode']}] {r['event_key']}  成员 {r['members']}  "
                   f"等权份额 ¥{r['share']:,.0f}")
        if r.get('derived_wave'):
            typer.echo(f"   ↳ 二波新槽（原键已关闭，永不并回）")
        if r.get('key_missing'):
            typer.echo(f"   ⚠️ 事件键缺失 fail-open：兜底键 {r['event_key']}（影子账#9 已记）")
        for c in r.get('conflicts', []):
            typer.echo(f"   ⚠️ {c} 主池另有 open 段（同票双组）——晨审人工裁决")
        typer.echo(f"   待成交单 pending → 心跳开盘后首扫 sleeve-fill 按开盘价成交")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("sleeve-fill")
def sleeve_fill(
    event_key: Optional[str] = typer.Option(None, "--event-key", help="只成交该槽（缺省=全部 pending）"),
    price: list[str] = typer.Option(None, "--price", help="开盘价注入 股票=价格（测试/停牌顺延场景）"),
    atr: Optional[str] = typer.Option(None, "--atr", help="ATR 注入 股票=ATR 或单一标量（缺省触网算）"),
    prev_close: list[str] = typer.Option(None, "--prev-close", help="昨收注入 股票=价格（R7 价差防线参照+影子账#7）"),
    skip_conditions: bool = typer.Option(False, "--skip-conditions", help="跳过挂三件套（不推荐）"),
):
    """开盘成交分支（心跳 ≥9:30 首扫调用）：pending → 按当日开盘价成交 + 挂三件套
    （R7 防线：价≤0/偏离昨收>30%/ATR 解析失败 → 拒绝成交留痕 fill_blocked）"""
    from paper_trading_v2.sleeve_open import SleeveOpener

    def _kv(pairs):
        out = {}
        for p in pairs or []:
            if '=' in p:
                k, v = p.split('=', 1)
                out[normalize_stock_name(k.strip())] = float(v)
        return out

    atr_arg = _kv([atr]) if (atr and '=' in atr) else \
        (float(atr) if atr else None)
    try:
        res = SleeveOpener().fill_pending(event_key=event_key, open_prices=_kv(price),
                                          atr=atr_arg, prev_close_map=_kv(prev_close),
                                          skip_conditions=skip_conditions)
        if not res:
            typer.echo("IDLE（无 pending 待成交单）")
            return
        for s in res:
            for f in s['filled']:
                if 'qty' in f:
                    typer.echo(f"✅ {s['event_key']} {f['stock']} 买 {f['qty']} 股 @ ¥{f['price']} "
                               f"= ¥{f['amount']:,.0f}")
                else:
                    typer.echo(f"⏭ {s['event_key']} {f['stock']} {f.get('note','')}")
            for st, why in s['skipped']:
                typer.echo(f"⏭ {s['event_key']} {st} 未成交：{why}")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("sleeve-cancel")
def sleeve_cancel(
    event_key: str = typer.Argument(...),
    reason: str = typer.Option("", "--reason", help="弃单原因（TTL 过期/停牌超期）"),
    source: str = typer.Option("agent", "--source"),
):
    """弃单：资金回消息池 + 槽 archived（坑释放）+ 影子账#1"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    try:
        r = SleeveOpener().cancel_pending(event_key, reason=reason, source=source)
        typer.echo(f"✅ 弃单 {event_key}（回款 ¥{r['refund']:,.0f}，影子账#1 已记）")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("sleeve-migrate")
def sleeve_migrate(
    stock: str = typer.Argument(...),
    reason: str = typer.Option("", "--reason", help="V11 资格判定依据（晨审产出）"),
    source: str = typer.Option("agent", "--source"),
    code: Optional[str] = typer.Option(None, "--code"),
):
    """移交桥＝段转策略：NEWS 段原地转 L1（段行 id 不动、成本/FIFO 连续，方案 2.3 v4.2）
    + 资金对转 + 槽 migrated 加仓锁。承接后注资走 master-pool-topup --source migrate"""
    stock = normalize_stock_name(stock)
    from paper_trading_v2.sleeve_migrate import SleeveMigrator
    try:
        code = _ensure_code(stock, code)
        r = SleeveMigrator().migrate(stock, reason=reason, source=source, code=code)
        typer.echo(f"✅ 段转 {stock}：{r['qty']} 股 @ ¥{r['avg_cost']:.2f}（成本 ¥{r['cost']:,.0f}）"
                   f"——NEWS 段原地转 L1（id 不动，成本连续）")
        typer.echo(f"   槽 {r['event_key']} → {r['slot_status']}（加仓锁已落）")
        typer.echo(f"   双 ledger 对转：主池承接 ¥{r['cost']:,.0f}，消息池回款 ¥{r['refund_to_sleeve']:,.0f}")
        typer.echo(f"   承接注资：master-pool-topup {stock} --amount X --source migrate"
                   f"（豁免甜点动量检查；资金帽/段位帽/冷却不豁免）")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("sleeve-close-slot")
def sleeve_close_slot(
    event_key: str = typer.Argument(...),
    reason: str = typer.Option("", "--reason"),
    source: str = typer.Option("agent", "--source"),
):
    """槽对账归档：全成员清零后槽 closed 释放（残余资金回消息池）"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    try:
        r = SleeveOpener().close_slot(event_key, reason=reason, source=source)
        typer.echo(f"✅ 槽 {event_key} closed（预算 ¥{r['budget']:,.0f}，已实现 ¥{r['realized']:,.0f}，"
                   f"残余回款 ¥{r['residual_refund']:,.0f}）")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


# ============ 交易命令组（minimal，委托 PaperTrader v2） ============

@app.command("init")
def init(
    stock_name: str = typer.Argument(...),
    capital: float = typer.Option(..., "--capital", "-c"),
    code: Optional[str] = typer.Option(None, "--code"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """初始化资金池（v9=manual L1 段直建；一般用 master-pool-allocate 替代）"""
    stock_name = normalize_stock_name(stock_name)
    # 有 open 段的股票，资金由段管理，禁止 init --force（防幻影资金）
    from paper_trading_v2.db import get_connection, migrate_db
    from paper_trading_v2.config import get_workspace_config
    _conn = get_connection(get_workspace_config()['db_path'])
    try:
        migrate_db(_conn)
        seg = _conn.execute("SELECT id FROM position WHERE stock=? AND status='open'",
                            (stock_name,)).fetchone()
    finally:
        _conn.close()
    if seg:
        typer.echo(f"❌ {stock_name} 有 open 段，资金由段管理，禁止 init --force", err=True)
        raise typer.Exit(1)
    from paper_trading_v2.trading import PaperTrader
    try:
        # v9 段即账户：init_account 落为 manual L1 open 段（budget=cash=capital）
        account = PaperTrader().init_account(stock_name, capital, stock_code=code, force=force)
        typer.echo(f"✅ 段直建成功（段视角）：{account.stock_name} ¥{capital:,.0f} "
                   f"→ manual L1 open 段（budget=cash=¥{capital:,.0f}）")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("buy")
def buy(
    stock_name: str = typer.Argument(...),
    qty: Optional[int] = typer.Option(None, "--qty", "-q"),
    amount: Optional[float] = typer.Option(None, "--amount", "-a"),
    note: str = typer.Option("", "--note", "-n"),
):
    """买入股票"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.gate import enforce
    from paper_trading_v2.trading import PaperTrader
    try:
        enforce(stock_name, 'buy')      # 能力矩阵：消息组禁直接 buy（只走 sleeve-fill）
        account = PaperTrader().buy_stock(stock_name, quantity=qty, amount=amount, note=note)
        typer.echo(f"✅ 买入成功：{stock_name} ｜ 剩余可用 ¥{account.capital_pool.available:,.0f}")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("sell")
def sell(
    stock_name: str = typer.Argument(...),
    qty: Optional[int] = typer.Option(None, "--qty", "-q"),
    all: bool = typer.Option(False, "--all", help="全部卖出"),
    note: str = typer.Option("", "--note", "-n"),
):
    """卖出股票"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.trading import PaperTrader
    try:
        account = PaperTrader().sell_stock(stock_name, quantity=qty, sell_all=all, note=note)
        typer.echo(f"✅ 卖出成功：{stock_name} ｜ 可用 ¥{account.capital_pool.available:,.0f}")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("list")
def list_cmd():
    """列出所有段（段视角，v9：段即账户）"""
    from paper_trading_v2.storage import SqlStorage
    s = SqlStorage()
    names = s.list_accounts()
    if not names:
        typer.echo("📭 暂无 open 段（段视角）")
        return
    closed = s.list_accounts(include_closed=True)
    typer.echo(f"📊 共有 {len(names)} 个 open 段（段视角）：")
    for name in names:
        acct = s.load_account(name)
        if acct:
            typer.echo(f"  • {name}: ¥{acct.capital_pool.available:,.0f} 段现金 / "
                       f"¥{acct.capital_pool.current_total:,.0f} 段内总资产"
                       f"（budget ¥{acct.capital_pool.total:,.0f}）")
    n_closed = len(closed) - len(names)
    if n_closed > 0:
        typer.echo(f"（另有 {n_closed} 个已关闭段为历史壳：ptrade2 info <股> 按名查询）")


# ============ 查询命令组（v1 对齐，驱动 SQLite） ============

@app.command()
def info(
    stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/markdown)"),
):
    """查看账户详情（合并显示资金池、持仓、收益）"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    from paper_trading_v2.reporting import ReportGenerator
    from paper_trading_v2.trading import PaperTrader
    manager = PortfolioManager()

    if stock_name:
        # 自动除权检查
        try:
            trader = PaperTrader()
            auto_exright_check(trader, stock_name)
        except Exception:
            pass
        # 查询单个股票的完整信息
        summary = manager.get_account_summary(stock_name)

        if not summary:
            suggestions = get_stock_name_suggestions(stock_name, manager)
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的账户记录{suggestions}")
            raise typer.Exit(1)

        if format == "markdown":
            # Markdown 表格模式：直接输出分析报告所需的表格
            generator = ReportGenerator()
            output = generator.generate_info_markdown_table(stock_name)
            typer.echo(output)
            return

        # Pretty 模式（默认）
        typer.echo(f"📊 {stock_name} 账户详情\n")

        # 资金池信息
        pool = summary["capital_pool"]
        typer.echo("💰 资金池状态")
        typer.echo(f"   初始资金：¥{pool['total']:,.2f}")
        typer.echo(f"   当前总资产：¥{pool['current_total']:,.2f}")
        typer.echo(f"   可用资金：¥{pool['available']:,.2f}")
        typer.echo(f"   占用资金：¥{pool['used']:,.2f}")
        typer.echo(f"   资金使用率：{pool['usage_rate']:.1f}%")

        # 持仓信息
        positions = summary["positions"]
        if positions["total_quantity"] == 0:
            typer.echo(f"\n📈 持仓状况：空仓")
        else:
            typer.echo(f"\n📈 持仓状况")
            typer.echo(f"   股票代码：{summary['stock_code']}")
            typer.echo(f"   持仓数量：{positions['total_quantity']} 股")
            typer.echo(f"   持仓成本：¥{positions['total_cost']:,.2f}")
            avg_cost = positions['total_cost'] / positions['total_quantity'] if positions['total_quantity'] > 0 else 0
            typer.echo(f"   单股成本：¥{avg_cost:,.2f}")
            if positions.get('current_price'):
                typer.echo(f"   当前价格：¥{positions['current_price']:.2f}")

            # 除权复权记录（直接从 positions 中提取，体现股数和成本变化）
            exright_positions = summary.get("exright_positions", [])
            if exright_positions:
                typer.echo(f"   除权记录:")
                for p in exright_positions:
                    ts = p.get("timestamp", "")[:10]
                    op = p.get("operation", "")
                    qty = p.get("quantity", 0)
                    cost = p.get("total_cost", 0.0)
                    note = p.get("note", "")
                    if op == "exright_bonus" and qty > 0:
                        typer.echo(f"      📅 {ts}: 送转 {qty}股 | 成本不变 | {note}")
                    elif op == "exright_dividend" and cost < 0:
                        typer.echo(f"      📅 {ts}: 分红 ¥{abs(cost):,.2f} | 股数不变 | {note}")
                    elif note:
                        typer.echo(f"      📅 {ts}: {note}")

        # 收益信息
        profit = summary["profit"]
        typer.echo(f"\n💵 收益状况")
        typer.echo(f"   实现盈亏：{'📈 +' if profit['realized'] >= 0 else '📉 '}¥{profit['realized']:,.2f}")
        typer.echo(f"   浮动盈亏：{'📈 +' if profit['floating'] >= 0 else '📉 '}¥{profit['floating']:,.2f}")
        typer.echo(f"   总盈亏：{'📈 +' if profit['total'] >= 0 else '📉 '}¥{profit['total']:,.2f}")

        return_rate = (profit["total"] / pool["total"] * 100) if pool["total"] > 0 else 0
        typer.echo(f"   总收益率：{'📈 +' if return_rate >= 0 else '📉 '}{return_rate:.2f}%")

    else:
        # 列出所有账户的简要信息
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"📊 所有账户概览（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary:
                pool = summary["capital_pool"]
                profit = summary["profit"]
                positions = summary["positions"]

                # 持仓状态
                pos_status = f"{positions['total_quantity']}股" if positions["total_quantity"] > 0 else "空仓"

                # 收益图标
                profit_icon = "📈" if profit["total"] >= 0 else "📉"
                current_total = pool['available'] + pool['used']

                typer.echo(f"\n  • {name}:")
                typer.echo(f"    💰 ¥{pool['available']:,.2f} 可用 / ¥{current_total:,.2f} 当前总资产 (初始: ¥{pool['total']:,.2f})")
                typer.echo(f"    📈 {pos_status}")
                typer.echo(f"    {profit_icon} 收益: ¥{profit['total']:+.2f} ({(profit['total']/pool['total']*100):+.2f}%)")


@app.command()
def pool(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查询资金池状态"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    manager = PortfolioManager()

    if stock_name:
        # 查询单个股票
        summary = manager.get_account_summary(stock_name)

        if not summary:
            suggestions = get_stock_name_suggestions(stock_name, manager)
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的资金池记录{suggestions}")
            raise typer.Exit(1)

        pool = summary["capital_pool"]
        typer.echo(f"💰 {stock_name} 资金池状态")
        typer.echo(f"   初始资金：¥{pool['total']:,.2f}")
        typer.echo(f"   当前总资产：¥{pool['current_total']:,.2f}")
        typer.echo(f"   可用资金：¥{pool['available']:,.2f}")
        typer.echo(f"   占用资金：¥{pool['used']:,.2f}")
        typer.echo(f"   资金使用率：{pool['usage_rate']:.1f}%")
    else:
        # 列出所有股票
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"💰 所有资金池状态（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary:
                pool = summary["capital_pool"]
                current_total = pool['available'] + pool['used']
                typer.echo(f"  • {name}: ¥{pool['available']:,.2f} 可用 / ¥{current_total:,.2f} 当前总资产 (初始: ¥{pool['total']:,.2f})")


@app.command()
def holdings(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查看持仓"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    manager = PortfolioManager()

    if stock_name:
        # 查询单个股票
        summary = manager.get_account_summary(stock_name)

        if not summary:
            suggestions = get_stock_name_suggestions(stock_name, manager)
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的持仓记录{suggestions}")
            raise typer.Exit(1)

        positions_data = summary["positions"]
        if positions_data["total_quantity"] == 0:
            typer.echo(f"📊 {stock_name}: 暂无持仓")
        else:
            typer.echo(f"📊 {stock_name}:")
            typer.echo(f"   股票代码: {summary['stock_code']}")
            typer.echo(f"   持仓数量: {positions_data['total_quantity']} 股")
            typer.echo(f"   持仓成本: ¥{positions_data['total_cost']:,.2f}")
            avg_cost_h = positions_data['total_cost'] / positions_data['total_quantity'] if positions_data['total_quantity'] > 0 else 0
            typer.echo(f"   单股成本: ¥{avg_cost_h:,.2f}")
            if positions_data.get('current_price'):
                typer.echo(f"   当前价格: ¥{positions_data['current_price']:.2f}")
    else:
        # 列出所有股票
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"📊 所有持仓（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary and summary["positions"]["total_quantity"] > 0:
                positions_data = summary["positions"]
                price_str = f"¥{positions_data['current_price']:.2f}" if positions_data.get('current_price') else "N/A"
                typer.echo(f"\n  📈 {name}:")
                typer.echo(f"     • {summary['stock_code']}: {positions_data['total_quantity']}股 @ {price_str}")
            else:
                typer.echo(f"  📈 {name}: 暂无持仓")


@app.command()
def profit(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查看收益报告"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    from paper_trading_v2.reporting import ReportGenerator
    generator = ReportGenerator()

    if stock_name:
        # 查询单个股票
        manager = PortfolioManager()
        summary = manager.get_account_summary(stock_name)
        if not summary:
            suggestions = get_stock_name_suggestions(stock_name, manager)
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的账户记录{suggestions}")
            raise typer.Exit(1)
        report = generator.generate_profit_report(stock_name)
        typer.echo(report)
    else:
        # 列出所有股票
        manager = PortfolioManager()
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"💵 所有收益报告（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary:
                pool = summary["capital_pool"]
                total_capital = pool["total"]
                profit = summary["profit"]["total"]
                profit_rate = (profit / total_capital) * 100

                icon = "📈" if profit >= 0 else "📉"
                typer.echo(f"{icon} {name}: ¥{profit:+.2f} ({profit_rate:+.2f}%)")


@app.command()
def portfolio():
    """查看投资组合报告"""
    from paper_trading_v2.reporting import ReportGenerator
    generator = ReportGenerator()
    report = generator.generate_portfolio_report()
    typer.echo(report)


@app.command()
def delete(
    stock_name: str = typer.Argument(..., help="股票名称"),
    force: bool = typer.Option(False, "--force", "-f", help="强制删除（即使有持仓）")
):
    """删除账户"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    manager = PortfolioManager()

    try:
        result = manager.delete_account(stock_name, force=force)
        if result:
            typer.echo(f"✅ 已删除账户: {stock_name}")
        else:
            suggestions = get_stock_name_suggestions(stock_name, manager)
            typer.echo(f"❌ 未找到账户: {stock_name}{suggestions}")
            raise typer.Exit(1)
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command()
def operations(
    stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）"),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="仅显示最近N天的操作记录"),
    limit: Optional[int] = typer.Option(None, "--limit", "-n", help="最多显示最近N条操作记录")
):
    """查看操作历史"""
    stock_name = normalize_stock_name(stock_name)
    from paper_trading_v2.reporting import ReportGenerator
    from paper_trading_v2.portfolio import PortfolioManager
    generator = ReportGenerator()

    if stock_name:
        # 查询单个股票
        manager = PortfolioManager()
        account = manager.trader.get_account(stock_name)
        if not account:
            suggestions = get_stock_name_suggestions(stock_name, manager)
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的账户记录{suggestions}")
            raise typer.Exit(1)
        report = generator.generate_operations_report(stock_name, days=days, limit=limit)
        typer.echo(report)
    else:
        # 列出所有股票
        manager = PortfolioManager()
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        from datetime import datetime, timedelta

        typer.echo(f"📋 所有操作历史（共 {len(accounts)} 个）")
        for name in accounts:
            ops_data = manager.trader.storage.load_operations(name)
            if ops_data and ops_data.operations:
                ops = ops_data.operations
                # 按天数过滤
                if days is not None and days > 0:
                    cutoff = datetime.now() - timedelta(days=days)
                    ops = [
                        op for op in ops
                        if datetime.fromisoformat(op.timestamp) >= cutoff
                    ]
                if not ops:
                    continue
                typer.echo(f"\n  📅 {name}: {len(ops_data.operations)} 笔操作")
                display_ops = ops[-limit:] if limit else ops[-5:]
                for op in display_ops:
                    type_value = op.type.value if hasattr(op.type, 'value') else str(op.type)
                    # init 操作显示 capital，其他操作显示 amount
                    if hasattr(op, 'capital') and op.capital is not None:
                        amount_value = op.capital
                    else:
                        amount_value = op.amount if op.amount else 0
                    typer.echo(f"     • {op.timestamp[:10]} {type_value:4s}: {amount_value:.2f}")
            else:
                typer.echo(f"  📅 {name}: 暂无操作")


# ============ 行情/搜索命令组（真实网络抓取） ============

@app.command()
def fetch_price(
    code: str = typer.Argument(..., help="股票代码"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/json)")
):
    """获取股票实时价格"""
    from paper_trading_v2.price_fetcher import StockPriceFetcher
    try:
        fetcher = StockPriceFetcher()
        info = fetcher.get_realtime_price(code)

        if not info:
            typer.echo(f"❌ 未找到股票代码 '{code}' 的数据")
            raise typer.Exit(1)

        if format == "json":
            import json
            typer.echo(json.dumps({
                "code": info.code,
                "name": info.name,
                "market": info.market.value if hasattr(info.market, 'value') else str(info.market),
                "current_price": info.current_price,
                "pre_close": info.pre_close,
                "open_price": info.open_price,
                "high": info.high,
                "low": info.low,
                "volume": info.volume,
                "date": info.date,
                "time": info.time,
                "source": info.source
            }, ensure_ascii=False, indent=2))
        else:
            # 格式化价格变化百分比
            change_percent = 0
            if info.current_price and info.pre_close:
                change_percent = ((info.current_price - info.pre_close) / info.pre_close) * 100

            icon = "📈" if change_percent >= 0 else "📉"
            sign = "+" if change_percent >= 0 else ""

            typer.echo(f"📊 {info.name} ({info.code})\n")
            typer.echo(f"💰 当前价格: ¥{info.current_price:.2f}" if info.current_price else f"💰 当前价格: N/A")
            typer.echo(f"   昨收价格: ¥{info.pre_close:.2f}" if info.pre_close else f"   昨收价格: N/A")
            typer.echo(f"   开盘价格: ¥{info.open_price:.2f}" if info.open_price else f"   开盘价格: N/A")
            typer.echo(f"   最高价格: ¥{info.high:.2f}" if info.high else f"   最高价格: N/A")
            typer.echo(f"   最低价格: ¥{info.low:.2f}" if info.low else f"   最低价格: N/A")
            typer.echo(f"   成交量: {info.volume}" if info.volume else f"   成交量: N/A")

            typer.echo(f"   日期: {info.date}" if info.date else f"   日期: N/A")
            typer.echo(f"   时间: {info.time}" if info.time else f"   时间: N/A")
            typer.echo(f"   数据源: {info.source}")

            if change_percent != 0:
                typer.echo(f"\n{icon} 涨跌幅: {sign}{change_percent:.2f}%")

    except Exception as e:
        typer.echo(f"❌ 获取价格失败: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def fetch_kline(
    code: str = typer.Argument(..., help="股票代码"),
    kline_type: str = typer.Option("day", "--type", "-t", help="K线类型 (day/week/month/5min/10min/15min/30min/60min)"),
    count: int = typer.Option(120, "--count", "-n", help="获取最近N条数据"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/json)")
):
    """获取股票K线数据"""
    from paper_trading_v2.kline_fetcher import KLineDataFetcher
    try:
        fetcher = KLineDataFetcher()
        klines = fetcher.fetch_kline_data(code, kline_type=kline_type, count=count)

        if not klines:
            typer.echo(f"❌ 未找到股票代码 '{code}' 的K线数据")
            raise typer.Exit(1)

        if format == "json":
            import json
            typer.echo(json.dumps({
                "code": code,
                "kline_type": kline_type,
                "data": klines
            }, ensure_ascii=False, indent=2))
        else:
            typer.echo(f"📊 {code} {kline_type}K 数据（最近 {len(klines)} 条）\n")

            for kline in klines[-20:]:  # 显示最近20条
                time_str = kline.get('date', '')
                if 'time' in kline and kline['time']:
                    time_str = f"{time_str} {kline['time']}"

                typer.echo(f"📅 {time_str}:")
                typer.echo(f"   开: {kline['open']:.2f}, 收: {kline['close']:.2f}, "
                          f"高: {kline['high']:.2f}, 低: {kline['low']:.2f}")
                typer.echo(f"   成交量: {kline['volume']}")

            if len(klines) > 20:
                typer.echo(f"\n... 共 {len(klines)} 条数据")

    except Exception as e:
        typer.echo(f"❌ 获取K线数据失败: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def market_summary(
    code: str = typer.Argument(..., help="股票代码"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/json/markdown)")
):
    """获取股票多周期市场趋势汇总 (月K/周K/日K/分时)"""
    from paper_trading_v2.market_summary import MarketSummaryAnalyzer
    try:
        analyzer = MarketSummaryAnalyzer()
        data = analyzer.analyze(code)

        if data.get("error"):
            typer.echo(f"❌ {data['error']}", err=True)
            raise typer.Exit(1)

        if format == "json":
            import json
            typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        elif format == "markdown":
            typer.echo(analyzer.format_markdown(data))
        else:
            typer.echo(analyzer.format_pretty(data))

    except Exception as e:
        typer.echo(f"❌ 获取市场汇总失败: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def search(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    limit: int = typer.Option(10, "--limit", "-n", help="返回结果数量"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/json)")
):
    """搜索股票代码（支持A股、港股、美股热门股票）"""
    from paper_trading_v2.code_searcher import StockCodeSearcher
    try:
        searcher = StockCodeSearcher()
        # 使用综合搜索，包括新浪财经 API 和内置热门股票库
        search_results = searcher.search(keyword, limit=limit)

        # 合并 A股搜索和热门股票库的结果
        results = search_results.get('A_share', []) + search_results.get('hot_funds', [])

        if not results:
            typer.echo(f"❌ 未找到 '{keyword}' 相关的股票")
            raise typer.Exit(1)

        if format == "json":
            import json
            typer.echo(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            typer.echo(f"📊 搜索 '{keyword}' 的结果（共 {len(results)} 条）:\n")

            for idx, result in enumerate(results, 1):
                typer.echo(f"{idx}. {result['name']}")
                typer.echo(f"   代码: {result['code']}")
                typer.echo(f"   市场: {result['market']}")
                typer.echo(f"   来源: {result['source']}")
                typer.echo()

    except Exception as e:
        typer.echo(f"❌ 搜索失败: {e}", err=True)
        raise typer.Exit(1)


# ============ 迁移命令 ============

@app.command("migrate-existing")
def migrate_existing_cmd(
    source: Optional[str] = typer.Option(None, "--source", help="旧 JSON tradings 目录"),
    archive: Optional[str] = typer.Option(None, "--archive", help="归档目录"),
):
    """迁移旧 JSON 账户入 SQLite（一次性，源目录移入归档）"""
    from pathlib import Path
    from paper_trading_v2.migrate import migrate_existing
    from paper_trading_v2.config import get_workspace_config
    cfg = get_workspace_config()
    src = Path(source) if source else cfg['workspace_root'] / 'tradings'
    arch = Path(archive) if archive else cfg['workspace_root'] / 'tradings_archive'
    if not src.exists():
        typer.echo(f"❌ 源目录不存在：{src}", err=True)
        raise typer.Exit(1)
    has_account = any((src / d.name / 'account.json').exists() for d in src.iterdir() if d.is_dir())
    if not has_account:
        typer.echo(f"⚠ 源目录 {src} 下未找到任何 account.json", err=True)
    try:
        result = migrate_existing(src, cfg['db_path'], arch)
        typer.echo(f"✅ 迁移完成：{result['count']} 个账户 → {cfg['db_path']}")
        typer.echo(f"   归档目录：{arch}")
        if result['migrated']:
            typer.echo(f"   账户：{', '.join(result['migrated'])}")
    except Exception as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


# 注册 conditions/atr-sync/check-triggers/check-exright（T4 风险控制命令组，显式注册）
from paper_trading_v2.conditions_cmd import register as _register_conditions
_register_conditions(app)

# 注册 export/fix/fetch-news/temp-data/analysis（T5 数据/分析命令组，显式注册）
from paper_trading_v2.data_cmd import register as _register_data
_register_data(app)


if __name__ == "__main__":
    app()
