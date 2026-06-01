"""条件 CLI 测试"""

import json
import tempfile
import os
from unittest import mock
from typer.testing import CliRunner
from paper_trading.cli import app

runner = CliRunner()


def _mock_position():
    """模拟有持仓的返回结果"""
    return {
        "positions": {
            "total_quantity": 1000,
            "total_cost": 78000.0,
            "current_price": 85.0,
        }
    }


def _mock_no_position():
    """模拟无持仓"""
    return None


def test_conditions_show_not_found():
    """show 未初始化的股票"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        result = runner.invoke(app, ["conditions", "测试股不存在", "--action", "show"])
        assert result.exit_code == 0
        assert "未找到" in result.stdout


def test_conditions_set_and_show():
    """set 设定条件 + show 查看"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])
        assert result.exit_code == 0, result.output
        assert "条件设定成功" in result.output

        # show pretty
        result = runner.invoke(app, ["conditions", "测试股", "--action", "show"])
        assert result.exit_code == 0
        assert "移动止损" in result.output
        assert "78.00" in result.output

        # show markdown
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "show",
            "--format", "markdown",
        ])
        assert result.exit_code == 0
        assert "### 本期触发条件表" in result.output

        # show json
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "show",
            "--format", "json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["stock_name"] == "测试股"


def test_conditions_set_soft():
    """set 软条件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "take_profit_1",
            "--price", "88.00",
            "--action-str", "减仓30%",
            "--category", "soft",
            "--expiry-days", "7",
        ])
        assert result.exit_code == 0
        assert "条件设定成功" in result.output


def test_conditions_update_level1():
    """update 自动通行（浮盈上移止损）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 先初始化
        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])

        # mock 有持仓且浮盈（成本75 < 当前价85 < 新止损88）
        pos = {
            "positions": {
                "total_quantity": 1000,
                "total_cost": 75000.0,
                "current_price": 85.0,
            }
        }
        with mock.patch("paper_trading.cli.PortfolioManager.get_account_summary", return_value=pos):
            result = runner.invoke(app, [
                "conditions", "测试股",
                "--action", "update",
                "--type", "trailing_stop",
                "--price", "88.00",
            ])
        assert result.exit_code == 0
        assert "修改成功" in result.output
        assert "AUTO" in result.output


def test_conditions_trigger_and_expire():
    """trigger 和 expire"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 初始化止盈条件
        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "take_profit_1",
            "--price", "88.00",
            "--action-str", "减仓30%",
            "--category", "soft",
            "--expiry-days", "7",
        ])

        # trigger
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "trigger",
            "--type", "take_profit_1",
            "--trigger-price", "88.50",
        ])
        assert result.exit_code == 0
        assert "条件已触发" in result.output

        # expire
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "expire",
            "--type", "take_profit_1",
        ])
        assert result.exit_code == 0
        assert "条件已标记过期" in result.output


def test_conditions_check():
    """check 过期检查"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 初始化一个软条件
        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "take_profit_1",
            "--price", "88.00",
            "--action-str", "减仓30%",
            "--category", "soft",
            "--expiry-days", "7",
        ])

        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "check",
        ])
        assert result.exit_code == 0
        assert "过期条件" in result.output or "无过期条件" in result.output


def test_conditions_remove():
    """remove 移除条件"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 初始化
        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "add_position",
            "--price", "70.00",
            "--action-str", "加仓",
            "--category", "soft",
        ])

        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "remove",
            "--type", "add_position",
        ])
        assert result.exit_code == 0
        assert "已移除" in result.output

        # 再次移除（条件已不存在）应该报错
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "remove",
            "--type", "add_position",
        ])
        assert result.exit_code == 1
        assert "未找到" in result.output or "❌" in result.output


def test_conditions_update_with_reason():
    """update 带理由（Level 2，浮亏状态下移止损）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])

        # mock 有持仓但浮亏（成本80 > 当前价76）
        pos = {
            "positions": {
                "total_quantity": 1000,
                "total_cost": 80000.0,
                "current_price": 76.0,
            }
        }
        with mock.patch("paper_trading.cli.PortfolioManager.get_account_summary", return_value=pos):
            result = runner.invoke(app, [
                "conditions", "测试股",
                "--action", "update",
                "--type", "trailing_stop",
                "--price", "75.00",
                "--reason", "给足波动空间，前低支撑",
            ])
        assert result.exit_code == 0
        assert "修改成功" in result.output
        assert "REASON" in result.output


def test_conditions_update_override():
    """update 强制解锁（Level 3）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])

        with mock.patch("paper_trading.cli.PortfolioManager.get_account_summary", return_value=_mock_position()):
            result = runner.invoke(app, [
                "conditions", "测试股",
                "--action", "update",
                "--type", "trailing_stop",
                "--price", "55.00",
                "--override-trigger", "technical_breakdown",
                "--override-reason", "跌破年线+MACD死叉，观点由看多转看空，原止损位78过高",
            ])
        assert result.exit_code == 0
        assert "修改成功" in result.output
        assert "OVERRIDE" in result.output


def test_conditions_update_override_short_reason():
    """update Level 3 理由太短会被阻断"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])

        with mock.patch("paper_trading.cli.PortfolioManager.get_account_summary", return_value=_mock_position()):
            result = runner.invoke(app, [
                "conditions", "测试股",
                "--action", "update",
                "--type", "trailing_stop",
                "--price", "55.00",
                "--override-trigger", "technical_breakdown",
                "--override-reason", "太短",
            ])
        assert result.exit_code == 1
        assert "阻断" in result.output or "少于20字" in result.output


def test_conditions_update_invalid_trigger():
    """update 非法触发器"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])

        with mock.patch("paper_trading.cli.PortfolioManager.get_account_summary", return_value=_mock_position()):
            result = runner.invoke(app, [
                "conditions", "测试股",
                "--action", "update",
                "--type", "trailing_stop",
                "--price", "55.00",
                "--override-trigger", "invalid_trigger",
                "--override-reason", "跌破年线+MACD死叉，观点由看多转看空",
            ])
        assert result.exit_code == 1
        assert "非法触发器" in result.output


def test_conditions_update_no_position():
    """update 无持仓状态下允许取消止损（Level 2）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])

        # 无持仓时允许取消止损
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "update",
            "--type", "trailing_stop",
            "--price", "70.00",
        ])
        # 无持仓时返回 Level 2 "空仓状态，取消止损条件"
        assert result.exit_code == 0
        assert "修改成功" in result.output


def test_conditions_markdown_all_templates():
    """markdown 各种模板输出"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 初始化多种条件
        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "trailing_stop",
            "--price", "78.00",
            "--action-str", "减仓50%",
            "--category", "hard",
        ])
        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "cost_protection",
            "--price", "88.00",
            "--action-str", "清仓",
            "--category", "hard",
        ])
        runner.invoke(app, [
            "conditions", "测试股",
            "--action", "set",
            "--type", "take_profit_1",
            "--price", "88.00",
            "--action-str", "减仓30%",
            "--category", "soft",
            "--expiry-days", "7",
        ])

        # all
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "show",
            "--format", "markdown",
            "--template", "all",
        ])
        assert result.exit_code == 0
        assert "本期触发条件表" in result.output
        assert "硬条件修改审计" in result.output
        assert "上期触发条件执行检查" in result.output

        # audit-table
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "show",
            "--format", "markdown",
            "--template", "audit-table",
        ])
        assert result.exit_code == 0
        assert "硬条件修改审计" in result.output
        assert "本期触发条件表" not in result.output

        # expired-table（让软条件过期后查看）
        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "expire",
            "--type", "take_profit_1",
        ])
        assert result.exit_code == 0

        result = runner.invoke(app, [
            "conditions", "测试股",
            "--action", "show",
            "--format", "markdown",
            "--template", "expired-table",
        ])
        assert result.exit_code == 0
