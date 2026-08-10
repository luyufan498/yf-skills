"""ptrade2 CLI — master-pool / watchlist / 交易命令组"""
import typer
from typing import Optional

app = typer.Typer(help="ptrade2 — SQLite 深迁移 + 弹性组合总池",
                  add_completion=False, no_args_is_help=True)


def _normalize_stock_name(stock_name: str) -> str:
    """繁体→简体 归一（与 ptrade v1 一致）"""
    try:
        from opencc import OpenCC
        return OpenCC('t2s').convert(stock_name)
    except Exception:
        return stock_name


def _get_stock_name_suggestions(stock_name: str, manager) -> str:
    """获取股票名称纠错建议"""
    import difflib
    try:
        accounts = manager.list_accounts() or []
    except Exception:
        return ""
    suggestions = difflib.get_close_matches(stock_name, accounts, n=3, cutoff=0.5)
    if suggestions:
        return f"\n    💡 你是不是想找：{', '.join(suggestions)}？"
    return ""


def _auto_exright_check(trader, stock_name: str) -> bool:
    """自动除权检查（懒加载），返回是否发生了变更"""
    from paper_trading_v2.exright_cache import ExRightCache
    from paper_trading_v2.exright_handler import ExRightHandler
    try:
        account = trader.get_account(stock_name)
        if not account or not account.stock_code:
            return False

        cache = ExRightCache()
        handler = ExRightHandler(trader, cache)
        changed, msg = handler.check_and_apply(stock_name, account)
        if changed:
            typer.echo(f"📢 除权除息已处理: {msg}")
        return changed
    except Exception:
        return False


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
def master_pool_show():
    """总池状态"""
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    d = mpm.show()
    if 'error' in d:
        typer.echo(d['error'], err=True)
        raise typer.Exit(1)
    typer.echo(f"💰 总池：¥{d['total']:,.0f} ｜ 空闲：¥{d['free']:,.0f}")
    typer.echo(f"   已分配（open段）：¥{d['occupied']:,.0f} ({d['usage_rate']*100:.0f}%)")
    typer.echo(f"   已实现盈亏（进池）：¥{d['realized_pnl']:,.0f} ｜ 活跃段：{d['open_segments']}")


@app.command("master-pool-allocate")
def master_pool_allocate(
    stock: str = typer.Argument(...),
    amount: float = typer.Option(..., "--amount", help="分配金额"),
    reason: str = typer.Option("", "--reason", help="原因（审计）"),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
    code: Optional[str] = typer.Option(None, "--code", help="股票代码（可选）"),
):
    """开持仓段（从 free 拨 budget）"""
    stock = _normalize_stock_name(stock)
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    try:
        mpm.allocate(stock, amount, reason, source=source, code=code)
        typer.echo(f"✅ 分配 {stock} ¥{amount:,.0f}")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("master-pool-topup")
def master_pool_topup(
    stock: str = typer.Argument(...),
    amount: float = typer.Option(..., "--amount"),
    reason: str = typer.Option("", "--reason"),
):
    """段内注资"""
    stock = _normalize_stock_name(stock)
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager()
    try:
        mpm.topup(stock, amount, reason)
        typer.echo(f"✅ 注资 {stock} ¥{amount:,.0f}")
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
    stock = _normalize_stock_name(stock)
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


# ============ watchlist 命令组 ============

@app.command("watchlist-list")
def watchlist_list():
    """池名单"""
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist()
    for s in w.list():
        typer.echo(f"  {s['strategy']}  {s['stock']}  {str(s['code'] or '')}")


@app.command("watchlist-add")
def watchlist_add(
    stock: str = typer.Argument(...),
    code: Optional[str] = typer.Option(None, "--code"),
    strategy: str = typer.Option("L2", "--strategy", help="L1/L2/L3"),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
    reason: str = typer.Option("", "--reason"),
):
    """入池"""
    stock = _normalize_stock_name(stock)
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist()
    try:
        w.add(stock, code, strategy, source, reason)
        typer.echo(f"✅ 入池 {stock} ({strategy})")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("watchlist-remove")
def watchlist_remove(
    stock: str = typer.Argument(...),
    source: str = typer.Option("agent", "--source", help="agent/manual"),
    reason: str = typer.Option("", "--reason"),
):
    """移出池"""
    stock = _normalize_stock_name(stock)
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist()
    try:
        w.remove(stock, source, reason)
        typer.echo(f"✅ 移出 {stock}")
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
    """初始化资金池（一般用 master-pool-allocate 替代）"""
    stock_name = _normalize_stock_name(stock_name)
    # 有 open 段的股票，账户资金由段管理，禁止 init --force（防幻影资金）
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
        typer.echo(f"❌ {stock_name} 有 open 段，账户资金由段管理，禁止 init --force", err=True)
        raise typer.Exit(1)
    from paper_trading_v2.trading import PaperTrader
    try:
        account = PaperTrader().init_account(stock_name, capital, stock_code=code, force=force)
        typer.echo(f"✅ 资金池初始化成功：{account.stock_name} ¥{capital:,.0f}")
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
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.trading import PaperTrader
    try:
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
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.trading import PaperTrader
    try:
        account = PaperTrader().sell_stock(stock_name, quantity=qty, sell_all=all, note=note)
        typer.echo(f"✅ 卖出成功：{stock_name} ｜ 可用 ¥{account.capital_pool.available:,.0f}")
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command("list")
def list_cmd():
    """列出所有账户"""
    from paper_trading_v2.storage import SqlStorage
    s = SqlStorage()
    names = s.list_accounts()
    if not names:
        typer.echo("📭 暂无账户")
        return
    typer.echo(f"📊 共有 {len(names)} 个账户：")
    for name in names:
        acct = s.load_account(name)
        if acct:
            typer.echo(f"  • {name}: ¥{acct.capital_pool.available:,.0f} 可用 / "
                       f"¥{acct.capital_pool.current_total:,.0f} 当前总资产")


# ============ 查询命令组（v1 对齐，驱动 SQLite） ============

@app.command()
def info(
    stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/markdown)"),
):
    """查看账户详情（合并显示资金池、持仓、收益）"""
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    from paper_trading_v2.reporting import ReportGenerator
    from paper_trading_v2.trading import PaperTrader
    manager = PortfolioManager()

    if stock_name:
        # 自动除权检查
        try:
            trader = PaperTrader()
            _auto_exright_check(trader, stock_name)
        except Exception:
            pass
        # 查询单个股票的完整信息
        summary = manager.get_account_summary(stock_name)

        if not summary:
            suggestions = _get_stock_name_suggestions(stock_name, manager)
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
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    manager = PortfolioManager()

    if stock_name:
        # 查询单个股票
        summary = manager.get_account_summary(stock_name)

        if not summary:
            suggestions = _get_stock_name_suggestions(stock_name, manager)
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
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    manager = PortfolioManager()

    if stock_name:
        # 查询单个股票
        summary = manager.get_account_summary(stock_name)

        if not summary:
            suggestions = _get_stock_name_suggestions(stock_name, manager)
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
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    from paper_trading_v2.reporting import ReportGenerator
    generator = ReportGenerator()

    if stock_name:
        # 查询单个股票
        manager = PortfolioManager()
        summary = manager.get_account_summary(stock_name)
        if not summary:
            suggestions = _get_stock_name_suggestions(stock_name, manager)
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
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.portfolio import PortfolioManager
    manager = PortfolioManager()

    try:
        result = manager.delete_account(stock_name, force=force)
        if result:
            typer.echo(f"✅ 已删除账户: {stock_name}")
        else:
            suggestions = _get_stock_name_suggestions(stock_name, manager)
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
    stock_name = _normalize_stock_name(stock_name)
    from paper_trading_v2.reporting import ReportGenerator
    from paper_trading_v2.portfolio import PortfolioManager
    generator = ReportGenerator()

    if stock_name:
        # 查询单个股票
        manager = PortfolioManager()
        account = manager.trader.get_account(stock_name)
        if not account:
            suggestions = _get_stock_name_suggestions(stock_name, manager)
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


if __name__ == "__main__":
    app()
