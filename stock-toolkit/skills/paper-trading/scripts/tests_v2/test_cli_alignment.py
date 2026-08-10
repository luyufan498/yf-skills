"""ptrade2 CLI 对齐冒烟测试：移植命令可用、驱动 SQLite"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from typer.testing import CliRunner
import pytest

runner = CliRunner()


def test_version_command(ws):
    from paper_trading_v2.cli import app
    from paper_trading_v2 import __version__
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert __version__ in r.output


def test_normalize_stock_name(ws):
    from paper_trading_v2.cli import _normalize_stock_name
    assert _normalize_stock_name('英維克') == '英维克'
    assert _normalize_stock_name('赛力斯') == '赛力斯'


def test_get_stock_name_suggestions(ws):
    from paper_trading_v2.cli import _get_stock_name_suggestions
    class FakeManager:
        def list_accounts(self):
            return ['赛力斯', '英维克', '中科曙光']
    m = FakeManager()
    # 相近名给出建议
    s = _get_stock_name_suggestions('赛力', m)
    assert '赛力斯' in s
    # 空账户返回空串
    assert _get_stock_name_suggestions('赛力', object()) == ''


def test_auto_exright_check_no_account_returns_false(ws):
    """get_account 返回 None 时 early-return False（不触网络/DB）"""
    from paper_trading_v2.cli import _auto_exright_check
    class FakeTrader:
        def get_account(self, stock_name):
            return None
    assert _auto_exright_check(FakeTrader(), '赛力斯') is False


def test_auto_exright_check_no_code_returns_false(ws):
    """账户无 stock_code 时 early-return False"""
    from paper_trading_v2.cli import _auto_exright_check
    from paper_trading_v2.models import Account, CapitalPool
    class FakeTrader:
        def get_account(self, stock_name):
            return Account(stock_name=stock_name, stock_code=None,
                           capital_pool=CapitalPool(total=500000, available=500000, used=0))
    assert _auto_exright_check(FakeTrader(), '赛力斯') is False


def _mk_acct(ws, name='测试股', code='sz000001'):
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool
    SqlStorage().save_account(Account(stock_name=name, stock_code=code,
        capital_pool=CapitalPool(total=500000, available=500000, used=0)))


def test_info_command_markdown(ws):
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool
    # code=None 让 _auto_exright_check 走 early-return，避免真实网络除权抓取
    SqlStorage().save_account(Account(stock_name='测试股', stock_code=None,
        capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    r = runner.invoke(app, ["info", "测试股", "--format", "markdown"])
    assert r.exit_code == 0
    # vendored generate_info_markdown_table 输出的是无股票名的数据表格
    assert "| 资金池状态" in r.output
    assert "¥500,000.00" in r.output


def test_pool_command(ws):
    from paper_trading_v2.cli import app
    _mk_acct(ws)
    r = runner.invoke(app, ["pool"])
    assert r.exit_code == 0
    assert "测试股" in r.output


def test_holdings_command(ws):
    from paper_trading_v2.cli import app
    _mk_acct(ws)
    r = runner.invoke(app, ["holdings", "测试股"])
    assert r.exit_code == 0


def test_profit_command(ws):
    from paper_trading_v2.cli import app
    _mk_acct(ws)
    r = runner.invoke(app, ["profit", "测试股"])
    assert r.exit_code == 0


def test_portfolio_command(ws):
    from paper_trading_v2.cli import app
    _mk_acct(ws)
    r = runner.invoke(app, ["portfolio"])
    assert r.exit_code == 0


def test_delete_command(ws):
    from paper_trading_v2.cli import app
    _mk_acct(ws)
    r = runner.invoke(app, ["delete", "测试股"])
    assert r.exit_code == 0
    from paper_trading_v2.storage import SqlStorage
    assert SqlStorage().load_account('测试股') is None


def test_delete_requires_force_with_position(ws):
    """有持仓时 delete 需 --force"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool, Position
    SqlStorage().save_account(Account(stock_name='测试股', stock_code='sz000001',
        capital_pool=CapitalPool(total=500000, available=500000, used=0),
        positions=[Position(stock_code='sz000001', quantity=100, price=10.0,
                            total_cost=1000, operation='buy')]))
    r = runner.invoke(app, ["delete", "测试股"])
    assert r.exit_code == 1
    assert "仍有持仓" in r.output
    # 有持仓 + --force 删除成功
    r = runner.invoke(app, ["delete", "测试股", "--force"])
    assert r.exit_code == 0
    assert SqlStorage().load_account('测试股') is None


def test_operations_command(ws):
    """operations 命令：驱动 SQLite，单股操作历史"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool, AccountHistory, Operation
    SqlStorage().save_account(Account(stock_name='测试股', stock_code='sz000001',
        capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    SqlStorage().save_operations('测试股', AccountHistory(stock_name='测试股', operations=[
        Operation(type='init', capital=500000, timestamp='2026-01-01T09:00:00')]))
    r = runner.invoke(app, ["operations", "测试股"])
    assert r.exit_code == 0
    assert "操作历史" in r.output
    # 单股操作历史走 ReportGenerator 报告：INIT 渲染为 初始化（与 v1 一致）
    assert "初始化" in r.output


def test_operations_command_not_found(ws):
    """operations 单股不存在时给出建议并 exit 1"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["operations", "不存在的股"])
    assert r.exit_code == 1
    assert "未找到股票" in r.output


def test_operations_list_all_and_days_filter(ws):
    """operations 列出全部 + --days 过滤（--days 是 operations 的子命令选项）"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool, AccountHistory, Operation
    s = SqlStorage()
    s.save_account(Account(stock_name='测试股', stock_code='sz000001',
        capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    s.save_operations('测试股', AccountHistory(stock_name='测试股', operations=[
        Operation(type='init', capital=500000, timestamp='2026-01-01T09:00:00'),
        Operation(type='buy', price=100.0, quantity=100, amount=10000,
                  timestamp='2026-08-01T10:00:00'),
    ]))
    # 不指定股票 → 列出全部（多账户分支打印 op.type.value）
    r = runner.invoke(app, ["operations"])
    assert r.exit_code == 0
    assert "测试股" in r.output
    assert "buy" in r.output
    assert "init" in r.output
    # --days 365 时间窗覆盖两条操作（均为近一年内）
    r = runner.invoke(app, ["operations", "--days", "365"])
    assert r.exit_code == 0
    # --days 30 只保留 2026-08-01 的 buy，过滤掉 2026-01-01 的 init
    r = runner.invoke(app, ["operations", "--days", "30"])
    assert r.exit_code == 0
    assert "buy" in r.output
    assert "init" not in r.output


def test_fetch_price_command_registered(ws):
    """fetch-price 命令已注册（--help 不触网络）"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["fetch-price", "--help"])
    assert r.exit_code == 0
    assert "fetch-price" in r.output


def test_fetch_kline_command_registered(ws):
    """fetch-kline 命令已注册（--help 不触网络）"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["fetch-kline", "--help"])
    assert r.exit_code == 0
    assert "fetch-kline" in r.output


def test_market_summary_command_registered(ws):
    """market-summary 命令已注册（--help 不触网络）"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["market-summary", "--help"])
    assert r.exit_code == 0
    assert "market-summary" in r.output


def test_search_command_registered(ws):
    """search 命令已注册（--help 不触网络）"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["search", "--help"])
    assert r.exit_code == 0
    assert "search" in r.output


# ============ T4：风险控制命令组（conditions / atr-sync / check-triggers / check-exright） ============

def test_conditions_command(ws):
    """conditions 读取 SQLite 中的条件"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.conditions import ConditionsRecord, Condition
    s = SqlStorage()
    s.save_account(Account(stock_name='测试股', stock_code=None,
        capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    cm = ConditionsManager(storage=s)
    cm.save_conditions(ConditionsRecord(stock_name='测试股', conditions={
        'trailing_stop': Condition(id='c1', type='trailing_stop', name='移动止损', price=80.0,
                                   action='减仓50%', category='hard', status='active')}))
    # code=None 账户 → conditions 的实时价/除权路径 early-return，避免网络
    r = runner.invoke(app, ["conditions", "测试股", "--format", "markdown", "--template", "all"])
    assert r.exit_code == 0
    assert "移动止损" in r.output


def test_atr_sync_command_registered(ws):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["atr-sync", "--help"])
    assert r.exit_code == 0


def test_check_triggers_command_registered(ws):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["check-triggers", "--help"])
    assert r.exit_code == 0


def test_check_exright_no_code_account(ws):
    """code-less 账户：check-exright 正常返回（不崩、不触网络）"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool
    SqlStorage().save_account(Account(stock_name='测试股', stock_code=None,
        capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    r = runner.invoke(app, ["check-exright", "测试股"])
    assert r.exit_code == 0
    assert "股票代码为空" in r.output
