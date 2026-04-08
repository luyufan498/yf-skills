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
