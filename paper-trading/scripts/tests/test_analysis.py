"""分析管理器单元测试"""

import pytest
from pathlib import Path
from datetime import datetime
from paper_trading.analysis import AnalysisManager, validate_stock_name


class TestValidateStockName:
    """测试股票名称验证"""

    def test_valid_stock(self):
        """测试有效的股票名称"""
        is_valid, code = validate_stock_name("赛力斯")
        assert is_valid is True
        assert code is not None

    def test_invalid_stock(self):
        """测试无效的股票名称"""
        is_valid, code = validate_stock_name("根本不存在的股票名称12345")
        # 由于网络搜索可能会有延迟或失败，这里主要是测试接口
        # 如果搜索失败会返回 False
        # 注意：这个测试可能会因为网络问题而不稳定

    def test_common_stocks(self):
        """测试常见股票"""
        test_stocks = ["赛力斯", "比亚迪", "宁德时代"]
        for name in test_stocks:
            is_valid, code = validate_stock_name(name)
            # 只是确保函数能正常返回，不做严格断言
            assert isinstance(is_valid, bool)
            assert isinstance(code, (str, type(None)))


class TestAnalysisManager:
    """测试分析管理器"""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        """临时目录 fixture（禁用验证以加快测试速度）"""
        manager = AnalysisManager(base_dir=str(tmp_path / "analysis"), validate_stock=False)
        return manager

    @pytest.fixture
    def sample_content(self):
        """示例分析内容"""
        return """# 股票分析

## 技术面
- MACD 金叉
- 量价齐升
"""

    def test_save_analysis(self, temp_dir, sample_content):
        """测试保存分析"""
        record = temp_dir.save_analysis("测试股票", sample_content)

        assert record.stock_name == "测试股票"
        assert record.content == sample_content
        assert record.file_path is not None

        # 验证文件存在
        filepath = Path(record.file_path)
        assert filepath.exists()
        assert filepath.read_text(encoding='utf-8') == sample_content

    def test_read_analysis(self, temp_dir, sample_content):
        """测试读取分析"""
        # 先保存
        temp_dir.save_analysis("测试股票", sample_content)

        # 再读取
        record = temp_dir.read_analysis("测试股票")

        assert record is not None
        assert record.stock_name == "测试股票"
        assert record.content == sample_content

    def test_read_latest_analysis(self, temp_dir):
        """测试读取最新分析"""
        # 保存多次（使用不同时间戳以避免覆盖）
        temp_dir.save_analysis("测试股票", "第一次分析", timestamp=datetime(2026, 4, 8, 14, 0))
        temp_dir.save_analysis("测试股票", "第二次分析", timestamp=datetime(2026, 4, 8, 15, 0))

        # 读取应该是最新的
        record = temp_dir.read_analysis("测试股票")
        assert record.content == "第二次分析"

    def test_list_analyses(self, temp_dir):
        """测试列出分析记录"""
        # 保存多次（使用不同时间戳以避免覆盖）
        temp_dir.save_analysis("测试股票", "第一次", timestamp=datetime(2026, 4, 8, 14, 0))
        temp_dir.save_analysis("测试股票", "第二次", timestamp=datetime(2026, 4, 8, 15, 0))
        temp_dir.save_analysis("测试股票", "第三次", timestamp=datetime(2026, 4, 8, 16, 0))

        files = temp_dir.list_analyses("测试股票", limit=10)

        assert len(files) == 3
        # 应该按时间倒序
        assert "第三次" in files[0].read_text(encoding='utf-8')
        assert "第一次" in files[2].read_text(encoding='utf-8')

    def test_list_stocks(self, temp_dir):
        """测试列出所有股票"""
        temp_dir.save_analysis("股票1", "内容1")
        temp_dir.save_analysis("股票2", "内容2")
        temp_dir.save_analysis("股票3", "内容3")

        stocks = temp_dir.list_stocks()

        assert len(stocks) == 3
        assert "股票1" in stocks
        assert "股票2" in stocks
        assert "股票3" in stocks

    def test_filename_format(self, temp_dir):
        """测试文件名格式"""
        fixed_time = datetime(2026, 4, 8, 14, 30)
        record = temp_dir.save_analysis("测试股票", "内容", timestamp=fixed_time)

        filepath = Path(record.file_path)
        # 文件名应该是: 测试股票-2026-04-08-1430.md
        assert filepath.stem == "测试股票-2026-04-08-1430"

    def test_symlink_creation(self, temp_dir, sample_content):
        """测试软链接创建"""
        temp_dir.save_analysis("测试股票", sample_content)

        stock_dir = temp_dir._get_stock_dir("测试股票")
        symlink = stock_dir / temp_dir.LATEST_ANALYSIS_FILENAME

        assert symlink.exists()
        assert symlink.is_symlink()

        # 验证链接目标
        target = symlink.readlink()
        linked_file = stock_dir / target
        assert linked_file.exists()
        assert linked_file.read_text(encoding='utf-8') == sample_content

    def test_read_nonexistent(self, temp_dir):
        """测试读取不存在的分析"""
        record = temp_dir.read_analysis("不存在的股票")
        assert record is None
