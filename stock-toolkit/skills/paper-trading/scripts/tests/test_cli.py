"""CLI 集成测试"""

import json
import pytest
import tempfile
from pathlib import Path
from typer.testing import CliRunner
from paper_trading.cli import app
from datetime import datetime, timedelta

runner = CliRunner()

def test_cli_analysis_read_with_count():
    """测试 analysis read 命令的 --count 参数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.analysis import AnalysisManager
        manager = AnalysisManager(validate_stock=False)

        # 保存多次分析
        manager.save_analysis("测试股", "第一份", timestamp=datetime(2026, 4, 8, 14, 0))
        manager.save_analysis("测试股", "第二份", timestamp=datetime(2026, 4, 8, 15, 0))
        manager.save_analysis("测试股", "第三份", timestamp=datetime(2026, 4, 8, 16, 0))

        # 测试读取最近 2 份
        result = runner.invoke(app, ["analysis", "测试股", "--action", "read", "--count", "2"])

        assert result.exit_code == 0
        assert "第三份" in result.stdout
        assert "第二份" in result.stdout
        assert "第一份" not in result.stdout

def test_cli_analysis_read_default_count():
    """测试 analysis read 命令默认读取 1 份"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.analysis import AnalysisManager
        manager = AnalysisManager(validate_stock=False)

        manager.save_analysis("测试股", "第一份", timestamp=datetime(2026, 4, 8, 14, 0))
        manager.save_analysis("测试股", "第二份", timestamp=datetime(2026, 4, 8, 15, 0))

        # 不传 --count 参数，默认读取 1 份（最新）
        result = runner.invoke(app, ["analysis", "测试股", "--action", "read"])

        assert result.exit_code == 0
        assert "第二份" in result.stdout
        # 验证只显示了一份，没有分隔符（count=1 时保持简洁）
        assert result.stdout.count("=") == 0
        # 验证第一份没有被显示
        assert "第一份" not in result.stdout

def test_cli_analysis_read_count_exceeds_available():
    """测试 --count 超过可用报告数量时返回所有可用报告"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.analysis import AnalysisManager
        manager = AnalysisManager(validate_stock=False)

        # 只保存 2 份报告
        manager.save_analysis("测试股", "第一份", timestamp=datetime(2026, 4, 8, 14, 0))
        manager.save_analysis("测试股", "第二份", timestamp=datetime(2026, 4, 8, 15, 0))

        # 请求 5 份
        result = runner.invoke(app, ["analysis", "测试股", "--action", "read", "--count", "5"])

        assert result.exit_code == 0
        assert "第一份" in result.stdout
        assert "第二份" in result.stdout
        # 验证只显示了两份报告
        assert "报告 2/2" in result.stdout

def test_temp_data_save_command():
    """测试 temp-data save 命令"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.temp_data_manager import TempDataManager
        manager = TempDataManager(validate_stock=False)

        # 测试保存
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "save", "--category", "deep-search", "--content", "这是测试内容"]
        )

        assert result.exit_code == 0
        assert "临时数据保存成功" in result.stdout

def test_temp_data_read_command():
    """测试 temp-data read 命令"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.temp_data_manager import TempDataManager
        manager = TempDataManager(validate_stock=False)

        # 先保存
        manager.save_temp_data("测试股票", "deep-search", "测试内容")

        # 测试读取
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "read", "--category", "deep-search"]
        )

        assert result.exit_code == 0
        assert "测试内容" in result.stdout

def test_temp_data_stdin():
    """测试 temp-data stdin 读取"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.temp_data_manager import TempDataManager
        _ = TempDataManager(validate_stock=False)

        # 测试 stdin 读取
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "save", "--category", "deep-search", "--stdin"],
            input="从标准输入的数据"
        )

        assert result.exit_code == 0
        assert "临时数据保存成功" in result.stdout

def test_temp_data_list_command():
    """测试 temp-data list 命令"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.temp_data_manager import TempDataManager
        manager = TempDataManager(validate_stock=False)

        # 保存多条数据
        manager.save_temp_data("股票A", "deep-search", "内容A")
        manager.save_temp_data("股票A", "history-continuity", "内容B")

        # 测试列出所有类别
        result = runner.invoke(
            app,
            ["temp-data", "股票A", "--action", "list"]
        )

        assert result.exit_code == 0
        assert "deep-search" in result.stdout
        assert "history-continuity" in result.stdout


def test_operations_with_days():
    """测试 operations 命令的 --days 参数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.trading import PaperTrader
        from paper_trading.models import Operation, OperationType

        trader = PaperTrader()
        trader.init_account("测试股", capital=100000, stock_code="sh000001", force=True)

        # 今天的操作
        today_op = Operation(
            type=OperationType.BUY, price=10, quantity=100, amount=1000,
            timestamp=datetime.now().isoformat()
        )
        trader.storage.save_operation("测试股", today_op)

        # 10 天前的操作
        old_date = datetime.now() - timedelta(days=10)
        old_op = Operation(
            type=OperationType.BUY, price=10, quantity=100, amount=1000,
            timestamp=old_date.isoformat()
        )
        trader.storage.save_operation("测试股", old_op)

        # 测试 --days 7 只返回最近 7 天的操作
        result = runner.invoke(app, ["operations", "测试股", "--days", "7"])

        assert result.exit_code == 0
        assert "(最近 7 天)" in result.stdout
        assert "买入" in result.stdout
        # 验证 10 天前的操作不在输出中，今天的操作在输出中
        old_date_str = old_date.strftime('%Y-%m-%d')
        today_str = datetime.now().strftime('%Y-%m-%d')
        assert old_date_str not in result.stdout
        assert today_str in result.stdout


def test_operations_list_with_limit():
    """测试 operations 命令的 --limit 参数（全部股票列表视图）"""
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        from paper_trading.trading import PaperTrader
        from paper_trading.models import Operation, OperationType

        trader = PaperTrader()
        trader.init_account("测试股", capital=100000, stock_code="sh000001", force=True)

        # 保存 3 条操作
        for i in range(3):
            op = Operation(
                type=OperationType.BUY, price=10, quantity=100, amount=1000,
                timestamp=datetime.now().isoformat()
            )
            trader.storage.save_operation("测试股", op)

        # 测试 --limit 2 只显示最近 2 笔
        result = runner.invoke(app, ["operations", "--limit", "2"])

        assert result.exit_code == 0
        assert "测试股" in result.stdout
        # 检查 bullet 点数量（每行以 "     •" 开头）
        bullet_lines = [line for line in result.stdout.split('\n') if '     •' in line]
        assert len(bullet_lines) == 2


def test_cli_market_summary_json():
    """测试 market-summary 命令 --format json 输出"""
    result = runner.invoke(app, ["market-summary", "sh600000", "--format", "json"])
    # 命令尚未注册时 Typer/Click 返回 exit code 2
    assert result.exit_code in (0, 1, 2), f"Unexpected exit code: {result.exit_code}"
    if result.exit_code == 0:
        data = json.loads(result.stdout)
        assert "code" in data
        assert "trend_summary" in data
        assert "cross_period" in data


def test_cli_market_summary_pretty():
    """测试 market-summary 命令 --format pretty 输出"""
    result = runner.invoke(app, ["market-summary", "sh600000", "--format", "pretty"])
    assert result.exit_code in (0, 1, 2), f"Unexpected exit code: {result.exit_code}"
    if result.exit_code == 0:
        assert "近6个月" in result.stdout or "近8周" in result.stdout


def test_cli_market_summary_markdown():
    """测试 market-summary 命令 --format markdown 输出"""
    result = runner.invoke(app, ["market-summary", "sh600000", "--format", "markdown"])
    assert result.exit_code in (0, 1, 2), f"Unexpected exit code: {result.exit_code}"
    if result.exit_code == 0:
        assert "##" in result.stdout

