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
