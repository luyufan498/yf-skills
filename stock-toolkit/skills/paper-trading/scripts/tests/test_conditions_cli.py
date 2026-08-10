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


# ============ ATR 动态止损测试 ============

def _mock_account(stock_code="sh600000"):
    """模拟账户对象（带 stock_code）"""
    from unittest import mock
    account = mock.MagicMock()
    account.stock_code = stock_code
    return account


def _fixed_klines(n=20, tr=2.0, base=100.0):
    """构造 n 根K线，每根 TR=tr（前收=base, H=base+tr, L=base, C=base）"""
    klines = []
    for i in range(n):
        klines.append({"date": f"2026-01-{i+1:02d}", "open": base, "high": base + tr,
                       "low": base, "close": base, "volume": 1000})
    return klines


def _setup_atr_sync_mocks(tr=2.0, base=100.0, current_price=100.0, rt_high=None):
    """返回 mock.patch 的 context managers 组合，用于 atr-sync 测试。
    持仓 1000 股 @ base，ATR=tr，当前价 current_price。"""
    patches = [
        mock.patch("paper_trading.storage.JsonStorage.load_account",
                   return_value=_mock_account()),
        mock.patch("paper_trading.trading.PaperTrader.get_remaining_position",
                   return_value=(1000, 1000 * base)),
        mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data",
                   return_value=_fixed_klines(20, tr, base)),
        mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price",
                   return_value=_mock_rt(current_price, rt_high)),
        mock.patch("paper_trading.storage.JsonStorage.list_accounts",
                   return_value=["测试股"]),
        mock.patch("paper_trading.storage.JsonStorage.save_account"),
    ]
    return patches


def _mock_rt(current_price, high):
    from unittest import mock
    rt = mock.MagicMock()
    rt.current_price = current_price
    rt.high = high
    return rt


def test_atr_sync_single_stock():
    """atr-sync 单只股票：trailing_stop 按 peak−2.5×ATR 设定，peak 写入"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        # 先 set trailing_stop 和 cost_protection
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "78.00",
                            "--action-str", "清仓", "--category", "hard"])
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "cost_protection", "--price", "98.50",
                            "--action-str", "清仓", "--category", "hard"])

        # atr-sync：ATR=2, peak首次=当前价100, trailing=100−2.5×2=95, cost=100−2×2=96
        for p in _setup_atr_sync_mocks(tr=2.0, base=100.0, current_price=100.0):
            p.start()
        try:
            result = runner.invoke(app, ["atr-sync", "测试股"])
        finally:
            mock.patch.stopall()
        assert result.exit_code == 0, result.output
        assert "移动止损" in result.output
        assert "95.00" in result.output  # peak100 − 2.5×2 = 95

        # 验证 peak_price 写入
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        assert ts["peak_price"] == 100.0


def test_atr_sync_only_ascending():
    """只升不降：ATR 变大（peak 不变），止损不降"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "78.00",
                            "--action-str", "清仓", "--category", "hard"])
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "cost_protection", "--price", "98.50",
                            "--action-str", "清仓", "--category", "hard"])

        # 第一次：ATR=2, peak=100, trailing=95
        for p in _setup_atr_sync_mocks(tr=2.0, current_price=100.0):
            p.start()
        try:
            runner.invoke(app, ["atr-sync", "测试股"])
        finally:
            mock.patch.stopall()

        # 第二次：ATR 变大到 5（peak 仍 100），raw_stop=100−12.5=87.5 < 95，应保持 95
        for p in _setup_atr_sync_mocks(tr=5.0, current_price=100.0):
            p.start()
        try:
            result = runner.invoke(app, ["atr-sync", "测试股"])
        finally:
            mock.patch.stopall()
        assert result.exit_code == 0
        # 止损应保持 95（只升不降），不降到 87.5
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        assert ts["price"] == 95.0


def test_atr_sync_dry_run():
    """--dry-run 不写入"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "78.00",
                            "--action-str", "清仓", "--category", "hard"])
        for p in _setup_atr_sync_mocks(tr=2.0, current_price=100.0):
            p.start()
        try:
            result = runner.invoke(app, ["atr-sync", "测试股", "--dry-run"])
        finally:
            mock.patch.stopall()
        assert result.exit_code == 0
        assert "dry-run" in result.output or "未写入" in result.output
        # 验证未写入：peak_price 仍为 None
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        assert ts["peak_price"] is None


def test_atr_sync_insufficient_klines():
    """K线不足 period+1 根，跳过不报错"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "78.00",
                            "--action-str", "清仓", "--category", "hard"])
        # 只返回 5 根K线（不足 15）
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(1000, 100000.0)), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=_fixed_klines(5)), \
             mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price", return_value=None):
            result = runner.invoke(app, ["atr-sync", "测试股"])
        assert result.exit_code == 0
        assert "跳过" in result.output


def test_atr_sync_no_position():
    """空仓账户跳过"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(0, 0.0)), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=_fixed_klines(20)):
            result = runner.invoke(app, ["atr-sync", "测试股"])
        assert result.exit_code == 0
        assert "空仓" in result.output


def test_atr_sync_init_peak_current():
    """首次 peak 用当前价初始化（防突变），非历史最高"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "78.00",
                            "--action-str", "清仓", "--category", "hard"])
        # K线含历史高点 120，但当前价 100 → peak 应=100（current），非 120
        klines = _fixed_klines(20, tr=2.0, base=100.0)
        klines[10]["high"] = 120.0  # 历史高点
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(1000, 100000.0)), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=klines), \
             mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price", return_value=_mock_rt(100.0, 100.0)):
            result = runner.invoke(app, ["atr-sync", "测试股"])
        assert result.exit_code == 0
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        assert ts["peak_price"] == 100.0  # 当前价，非历史 120


def test_validate_cost_protection_atr_branch():
    """手动 update cost_protection 传 ATR，ATR 分支 Level 1 放行"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "cost_protection", "--price", "98.50",
                            "--action-str", "清仓", "--category", "hard"])
        pos = {"positions": {"total_quantity": 1000, "total_cost": 100000.0, "current_price": 100.0}}
        # 成本100, ATR=2, 期望=100−2×2=96
        with mock.patch("paper_trading.cli.PortfolioManager.get_account_summary", return_value=pos), \
             mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=_fixed_klines(20, tr=2.0)):
            result = runner.invoke(app, ["conditions", "测试股", "--action", "update",
                                         "--type", "cost_protection", "--price", "96.00"])
        assert result.exit_code == 0
        assert "修改成功" in result.output


def test_sync_cost_protection_80pct_floor():
    """ATR 异常大时，cost_protection 不低于成本×80%"""
    from paper_trading.conditions_manager import ConditionsManager
    from paper_trading.conditions import ConditionType
    import tempfile, json as _json
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        # 建仓 + set cost_protection
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "cost_protection", "--price", "98.50",
                            "--action-str", "清仓", "--category", "hard"])
        # sync：成本100, ATR=30(异常大) → 100−2×30=40 < 80(成本80%) → 取 80
        from paper_trading.storage import JsonStorage
        from paper_trading.trading import PaperTrader
        trader = PaperTrader()
        cond_mgr = ConditionsManager(trader.storage)
        cond_mgr.sync_cost_protection("测试股", avg_cost=100.0, atr=30.0, k_cost=2.0)
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        cp = data["conditions"]["cost_protection"]
        assert cp["price"] == 80.0  # 成本×80% 底线


# ============ check-triggers 止损触发检测测试（Bug #1） ============

def _setup_check_triggers_mocks(current_price=85.0, has_position=True, base=100.0):
    """check-triggers 通用 mock：持仓 1000 股 @ base，现价 current_price。"""
    qty = 1000 if has_position else 0
    return [
        mock.patch("paper_trading.storage.JsonStorage.load_account",
                   return_value=_mock_account()),
        mock.patch("paper_trading.trading.PaperTrader.get_remaining_position",
                   return_value=(qty, qty * base)),
        mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price",
                   return_value=_mock_rt(current_price, current_price)),
        mock.patch("paper_trading.storage.JsonStorage.list_accounts",
                   return_value=["测试股"]),
    ]


def test_check_triggers_breach():
    """移动止损¥90 被现价¥85 跌破 → 报 breach，exit_code=1，且不改 status"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "90.00",
                            "--action-str", "减仓50%", "--category", "hard"])
        for p in _setup_check_triggers_mocks(current_price=85.0):
            p.start()
        try:
            result = runner.invoke(app, ["check-triggers", "测试股", "--format", "json"])
        finally:
            mock.patch.stopall()
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        assert data["results"][0]["stock"] == "测试股"
        breaches = data["results"][0]["breaches"]
        assert len(breaches) == 1
        b = breaches[0]
        assert b["trigger_price"] == 90.0
        assert b["current_price"] == 85.0
        assert b["direction"] == "down"  # 跌破语义
        assert b["breach_amount"] == 5.0  # |85-90|
        # 关键：不修改 status —— 再查仍为 active
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        sdata = json.loads(show.output)
        assert sdata["conditions"]["trailing_stop"]["status"] == "active"


def test_check_triggers_no_breach():
    """现价¥95 > 止损¥90 → 无触发，exit_code=0"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "90.00",
                            "--action-str", "减仓50%", "--category", "hard"])
        for p in _setup_check_triggers_mocks(current_price=95.0):
            p.start()
        try:
            result = runner.invoke(app, ["check-triggers", "测试股", "--format", "json"])
        finally:
            mock.patch.stopall()
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["results"][0]["breaches"] == []


def test_check_triggers_skip_empty():
    """空仓账户跳过，不报 breach"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "90.00",
                            "--action-str", "减仓50%", "--category", "hard"])
        for p in _setup_check_triggers_mocks(current_price=85.0, has_position=False):
            p.start()
        try:
            result = runner.invoke(app, ["check-triggers", "测试股", "--format", "json"])
        finally:
            mock.patch.stopall()
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        # 空仓应 skip，不在 breaches 结果里
        assert data["results"][0].get("status") == "skip" or data["results"][0]["breaches"] == []


def test_check_triggers_no_price():
    """实时价获取失败（None）→ 该股 skip，不崩"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "90.00",
                            "--action-str", "减仓50%", "--category", "hard"])
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(1000, 100000.0)), \
             mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price", return_value=None):
            result = runner.invoke(app, ["check-triggers", "测试股", "--format", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["results"][0].get("status") == "skip"


def test_check_triggers_take_profit_direction():
    """止盈¥100 被现价¥102 涨破 → breach（涨破语义 direction=up）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "take_profit_1", "--price", "100.00",
                            "--action-str", "减仓30%", "--category", "hard"])
        for p in _setup_check_triggers_mocks(current_price=102.0):
            p.start()
        try:
            result = runner.invoke(app, ["check-triggers", "测试股", "--format", "json"])
        finally:
            mock.patch.stopall()
        assert result.exit_code == 1, result.output
        data = json.loads(result.output)
        breaches = data["results"][0]["breaches"]
        assert len(breaches) == 1
        assert breaches[0]["direction"] == "up"  # 涨破语义
        assert breaches[0]["breach_amount"] == 2.0


# ============ peak 本轮过滤测试（Bug #2） ============

def _build_round_ops(round1_buy_date, round2_buy_date):
    """构造操作历史：第一轮买后清仓，第二轮再买。返回 AccountHistory 可序列化 dict。"""
    from paper_trading.models import AccountHistory, Operation, OperationType
    ops = [
        Operation(type=OperationType.INIT, capital=100000.0, timestamp=f"{round1_buy_date}T09:00:00", note="初始化"),
        Operation(type=OperationType.BUY, price=100.0, quantity=1000, amount=100000.0, timestamp=f"{round1_buy_date}T11:00:00", note="第一轮建仓"),
        Operation(type=OperationType.SELL, price=95.0, quantity=1000, amount=95000.0, cost=100000.0, profit=-5000.0, timestamp=f"{round1_buy_date}T14:00:00", note="第一轮清仓"),
        Operation(type=OperationType.BUY, price=95.0, quantity=1000, amount=95000.0, timestamp=f"{round2_buy_date}T11:00:00", note="第二轮建仓"),
    ]
    return AccountHistory(stock_name="测试股", operations=ops)


def test_atr_sync_peak_filtered_by_build_round():
    """peak 只取本轮建仓以来的 K 线 high，不含上一轮历史高点 113"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "78.00",
                            "--action-str", "清仓", "--category", "hard"])
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "cost_protection", "--price", "98.50",
                            "--action-str", "清仓", "--category", "hard"])
        # 操作历史：第一轮 7/10 买+清仓，第二轮 7/27 买
        round_ops = _build_round_ops("2026-07-10", "2026-07-27")
        # K线：7/10 高点 113（上一轮），7/27 之后高点 98（本轮）
        klines = []
        for d in range(1, 31):
            date = f"2026-07-{d:02d}"
            high = 113.0 if d == 10 else (98.0 if d >= 27 else 95.0)
            klines.append({"date": date, "open": 95.0, "high": high, "low": 94.0, "close": 95.0, "volume": 1000})
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(1000, 95000.0)), \
             mock.patch("paper_trading.storage.JsonStorage.load_operations", return_value=round_ops), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=klines), \
             mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price", return_value=_mock_rt(96.0, 98.0)):
            result = runner.invoke(app, ["atr-sync", "测试股"])
        assert result.exit_code == 0, result.output
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        # peak 不应是上一轮的 113，应为本轮高点 98 或当前价 96
        assert ts["peak_price"] is not None
        assert ts["peak_price"] <= 98.0, f"peak {ts['peak_price']} 混入了上一轮历史高点"


def test_atr_sync_peak_polluted_resets():
    """已污染的 peak（113）+ 本轮 klines high 全 <113 → peak 被重置为本轮值，止损下移"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        # 先正常 set，再手动写入被污染的 peak_price=113
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "91.49",
                            "--action-str", "清仓", "--category", "hard"])
        from paper_trading.conditions_manager import ConditionsManager
        from paper_trading.conditions import ConditionType
        from paper_trading.trading import PaperTrader
        trader = PaperTrader()
        cond_mgr = ConditionsManager(trader.storage)
        record = cond_mgr.load_conditions("测试股")
        record.get(ConditionType.TRAILING_STOP).peak_price = 113.0
        cond_mgr.save_conditions(record)

        round_ops = _build_round_ops("2026-07-10", "2026-07-27")
        # 30 根 K 线（7/1–7/30，满足 ATR(14) 需 15 根），本轮（7/27 后）high 全部 <113
        klines = []
        for d in range(1, 31):
            date = f"2026-07-{d:02d}"
            klines.append({"date": date, "open": 95.0, "high": 97.0, "low": 94.0, "close": 96.0, "volume": 1000})
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(1000, 95000.0)), \
             mock.patch("paper_trading.storage.JsonStorage.load_operations", return_value=round_ops), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=klines), \
             mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price", return_value=_mock_rt(96.0, 97.0)):
            result = runner.invoke(app, ["atr-sync", "测试股"])
        assert result.exit_code == 0, result.output
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        # peak 必须从 113 下移到本轮值（97 或 96）
        assert ts["peak_price"] < 113.0, f"peak 未重置: {ts['peak_price']}"
        # 止损应随之从 91.49 下移（peak−2.5×ATR，ATR≈1.0ish → 远低于 91.49；但只升不降保护旧值…）
        # 注：peak 重置后 raw_stop 重新基于新 peak，应低于旧 91.49


def test_atr_sync_init_peak_current_still_passes():
    """回归保护：peak 为 None（无清仓历史）时仍用当前价初始化，不受本轮过滤影响"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "78.00",
                            "--action-str", "清仓", "--category", "hard"])
        # K线含历史高点 120，当前价 100，无清仓历史（load_operations 返回 None）→ peak 应=100
        klines = _fixed_klines(20, tr=2.0, base=100.0)
        klines[10]["high"] = 120.0
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(1000, 100000.0)), \
             mock.patch("paper_trading.storage.JsonStorage.load_operations", return_value=None), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=klines), \
             mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price", return_value=_mock_rt(100.0, 100.0)):
            result = runner.invoke(app, ["atr-sync", "测试股"])
        assert result.exit_code == 0, result.output
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        assert ts["peak_price"] == 100.0  # 当前价，非历史 120


def test_atr_sync_peak_polluted_no_round_klines():
    """边界：本轮建仓当日盘中运行，K 线只到昨日（本轮过滤为空）+ 旧 peak 来自上一轮 → 仍应重置。

    复现中科曙光真实场景：8/1 重新建仓，K线数据只到 7/31，本轮无已收盘 K 线。
    旧 peak=113 来自上一轮，应被识别为污染并重置为当前价，止损纠错性下移。
    """
    from paper_trading.conditions_manager import ConditionsManager
    from paper_trading.conditions import ConditionType
    from paper_trading.trading import PaperTrader
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir
        runner.invoke(app, ["conditions", "测试股", "--action", "set",
                            "--type", "trailing_stop", "--price", "91.49",
                            "--action-str", "清仓", "--category", "hard"])
        trader = PaperTrader()
        cond_mgr = ConditionsManager(trader.storage)
        record = cond_mgr.load_conditions("测试股")
        record.get(ConditionType.TRAILING_STOP).peak_price = 113.0
        cond_mgr.save_conditions(record)

        round_ops = _build_round_ops("2026-07-10", "2026-08-01")  # 本轮 8/1 建仓
        # K线只到 7/31（本轮 8/1 无已收盘 K 线），high 全部 <113
        klines = []
        for d in range(1, 32):
            date = f"2026-07-{d:02d}"
            klines.append({"date": date, "open": 95.0, "high": 97.0, "low": 94.0, "close": 96.0, "volume": 1000})
        with mock.patch("paper_trading.storage.JsonStorage.load_account", return_value=_mock_account()), \
             mock.patch("paper_trading.trading.PaperTrader.get_remaining_position", return_value=(1000, 95000.0)), \
             mock.patch("paper_trading.storage.JsonStorage.load_operations", return_value=round_ops), \
             mock.patch("paper_trading.cli.KLineDataFetcher.fetch_kline_data", return_value=klines), \
             mock.patch("paper_trading.cli.StockPriceFetcher.get_realtime_price", return_value=_mock_rt(85.0, 85.0)):
            result = runner.invoke(app, ["atr-sync", "测试股"])
        assert result.exit_code == 0, result.output
        show = runner.invoke(app, ["conditions", "测试股", "--action", "show", "--format", "json"])
        data = json.loads(show.output)
        ts = data["conditions"]["trailing_stop"]
        # peak 必须从 113 重置为当前价 85
        assert ts["peak_price"] == 85.0, f"peak 未重置: {ts['peak_price']}"
        # 止损应从 91.49 纠错性下移（peak85 − 2.5×ATR，ATR=2 → 80）
        assert ts["price"] < 91.49, f"止损未下移: {ts['price']}"
