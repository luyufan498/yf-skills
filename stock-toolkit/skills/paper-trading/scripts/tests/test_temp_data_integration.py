"""临时数据集成测试

测试 ptrade temp-data 命令的端到端功能
"""

import os
import pytest
import tempfile
from pathlib import Path
from typer.testing import CliRunner
from paper_trading.cli import app

runner = CliRunner()


def test_full_workflow():
    """测试完整工作流：保存 -> 列出 -> 读取"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 步骤1：保存数据
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "save", "--category", "deep-search", "--content", "深度搜索结果内容"]
        )
        assert result.exit_code == 0
        assert "临时数据保存成功" in result.stdout

        # 步骤2：列出数据
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "list", "--category", "deep-search"]
        )
        assert result.exit_code == 0
        assert "deep-search" in result.stdout
        assert "测试股票" in result.stdout

        # 步骤3：读取数据
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "read", "--category", "deep-search"]
        )
        assert result.exit_code == 0
        assert "深度搜索结果内容" in result.stdout


def test_multiple_categories():
    """测试多个类别的保存和管理"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 保存多个类别的数据
        result1 = runner.invoke(
            app,
            ["temp-data", "赛力斯", "--action", "save", "--category", "deep-search", "--content", "深度搜索内容"]
        )
        assert result1.exit_code == 0

        result2 = runner.invoke(
            app,
            ["temp-data", "赛力斯", "--action", "save", "--category", "history-continuity", "--content", "历史连续性内容"]
        )
        assert result2.exit_code == 0

        result3 = runner.invoke(
            app,
            ["temp-data", "赛力斯", "--action", "save", "--category", "gf-summary", "--content", "广发摘要内容"]
        )
        assert result3.exit_code == 0

        # 列出所有类别
        result = runner.invoke(
            app,
            ["temp-data", "赛力斯", "--action", "list"]
        )
        assert result.exit_code == 0
        assert "deep-search" in result.stdout
        assert "history-continuity" in result.stdout
        assert "gf-summary" in result.stdout

        # 验证每个类别都有内容
        assert "1 条数据" in result.stdout or "3 条数据" in result.stdout


def test_file_input():
    """测试从文件输入数据"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        # 创建临时输入文件
        input_file = Path(tmpdir) / "input.md"
        input_file.write_text("这是从文件读取的测试内容\n包含多行文本", encoding='utf-8')

        # 使用文件输入保存数据
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "save", "--category", "deep-search", "--file", str(input_file)]
        )
        assert result.exit_code == 0
        assert "临时数据保存成功" in result.stdout

        # 读取并验证内容
        result = runner.invoke(
            app,
            ["temp-data", "测试股票", "--action", "read", "--category", "deep-search"]
        )
        assert result.exit_code == 0
        assert "这是从文件读取的测试内容" in result.stdout
        assert "包含多行文本" in result.stdout


def test_latest_symlink():
    """测试最新数据软链接功能"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ['STOCK_ANALYSIS_WORKSPACE'] = tmpdir

        stock_name = "比亚迪"
        category = "test-category"

        # 保存多次数据，每次保存的时间不同
        result1 = runner.invoke(
            app,
            ["temp-data", stock_name, "--action", "save", "--category", category, "--content", "第一次保存的内容"]
        )
        assert result1.exit_code == 0

        result2 = runner.invoke(
            app,
            ["temp-data", stock_name, "--action", "save", "--category", category, "--content", "第二次保存的内容"]
        )
        assert result2.exit_code == 0

        result3 = runner.invoke(
            app,
            ["temp-data", stock_name, "--action", "save", "--category", category, "--content", "第三次保存的内容"]
        )
        assert result3.exit_code == 0

        # 读取最新数据（应该读取最后一次保存的）
        result = runner.invoke(
            app,
            ["temp-data", stock_name, "--action", "read", "--category", category]
        )
        assert result.exit_code == 0
        assert "第三次保存的内容" in result.stdout
        # 确保不包含旧内容
        assert "第二次保存的内容" not in result.stdout
        assert "第一次保存的内容" not in result.stdout

        # 验证软链接文件存在
        symlinks = list(Path(tmpdir).glob("**/最新.md"))
        # 验证至少有一个软链接文件存在
        assert len(symlinks) > 0, f"未找到软链接文件，搜索路径：{tmpdir}/temp_data/{stock_name}/{category}/"
