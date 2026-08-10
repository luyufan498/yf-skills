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


if __name__ == "__main__":
    app()
