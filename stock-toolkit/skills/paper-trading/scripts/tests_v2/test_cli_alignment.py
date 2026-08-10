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
    _mk_acct(ws)
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
