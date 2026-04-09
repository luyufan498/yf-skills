"""CLI 集成测试"""

import pytest
import tempfile
from pathlib import Path
from typer.testing import CliRunner
from paper_trading.cli import app
from datetime import datetime

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
