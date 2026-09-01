"""共享 CLI 辅助函数（打破 cli ↔ cmd 子模块循环依赖）

T5-refactor：从 paper_trading_v2/cli.py 抽出三个跨子模块共享的辅助函数，
更名为公开名（去掉下划线前缀）。conditions_cmd / data_cmd 直接 import 本模块，
不再 import cli —— 避免"cli 末尾注册 cmd 子模块 ↔ cmd 顶部 import cli"的循环。
"""
import typer


def normalize_stock_name(stock_name: str) -> str:
    """繁体→简体 归一（与 ptrade v1 一致）"""
    try:
        from opencc import OpenCC
        return OpenCC('t2s').convert(stock_name)
    except Exception:
        return stock_name


def get_stock_name_suggestions(stock_name: str, manager) -> str:
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


def auto_exright_check(trader, stock_name: str) -> bool:
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


MIN_LISTING_TRADING_DAYS = 40   # G2 次新否决（方案 1.9/2.2，sleeve-m1 watchlist-add 硬检查）


def _trading_days_since(date_str) -> "int | None":
    """上市日 → 今天的交易日数（trading_calendar 单一真源，import 方式同
    watch_scan.py:40-44：workspace 根插入 sys.path 后 import trading_calendar）。"""
    import os
    import sys
    from datetime import date, datetime, timedelta
    root = os.environ.get("STOCK_ANALYSIS_WORKSPACE_ROOT",
                          "/home/catmouse/Github_Project/daily-stock-workspace")
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from trading_calendar import is_trading_day as _cal
    except Exception:
        return None
    try:
        d0 = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    n, d, today = 0, d0, date.today()
    while d < today and n < 5000:
        d += timedelta(days=1)
        try:
            if _cal(d):
                n += 1
        except Exception:
            return None
    return n


def check_listing_age(stock_name: str, code: str = None,
                      min_days: int = MIN_LISTING_TRADING_DAYS) -> "tuple[bool, str]":
    """G2 次新否决：上市不足 min_days 个交易日 → (False, 理由)。

    证据=日K 根数（每根=1 个交易日）；取样窗 60 根，≥min_days+1 根直接通过。
    不足窗时用 trading_calendar 从最早可得日精确复计（停牌日稳健）。
    数据不可得 → fail-closed（消息组硬闸：宁可阻塞也不放进次新票）。
    """
    from paper_trading_v2.kline_fetcher import KLineDataFetcher
    try:
        klines = KLineDataFetcher().fetch_kline_data(code, 'day', 60) if code else []
    except Exception:
        klines = []
    if not klines or len(klines) < 2:
        return False, (f"G2 无法核验上市日（{stock_name} 日K不可得）——"
                       f"次新<40 交易日硬检查 fail-closed，请稍后重试或人工核验")
    bars = len(klines)
    listing_date = klines[0].get('date')
    if bars >= min_days + 1:
        return True, f"上市≥{min_days} 交易日（取样 {bars} 根，最早 {listing_date}）"
    days = _trading_days_since(listing_date)
    if days is None:
        days = bars
    if days < min_days:
        return False, (f"G2 次新否决：{stock_name} 上市 {listing_date}，"
                       f"至今 {days} 个交易日 < {min_days}")
    return True, f"上市 {days} 个交易日 ≥ {min_days}"
