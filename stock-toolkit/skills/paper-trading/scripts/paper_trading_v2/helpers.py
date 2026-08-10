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
