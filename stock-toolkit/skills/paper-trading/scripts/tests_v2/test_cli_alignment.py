"""ptrade2 CLI 对齐冒烟测试：移植命令可用、驱动 SQLite"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from typer.testing import CliRunner
import pytest

runner = CliRunner()


def test_version_command(ws):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0
    assert "2.0.0" in r.output


def test_normalize_stock_name(ws):
    from paper_trading_v2.cli import _normalize_stock_name
    assert _normalize_stock_name('英維克') == '英维克'
    assert _normalize_stock_name('赛力斯') == '赛力斯'
