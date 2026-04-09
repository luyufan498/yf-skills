"""临时数据管理器测试"""

from pathlib import Path
from datetime import datetime
import tempfile
import shutil
import re


def test_save_temp_data():
    """测试保存临时数据"""
    from paper_trading.temp_data_manager import TempDataManager

    # 创建临时目录
    temp_dir = tempfile.mkdtemp()
    manager = TempDataManager(base_dir=temp_dir, validate_stock=False)

    try:
        result = manager.save_temp_data(
            stock_name="测试股票",
            category="deep-search",
            content="测试内容"
        )

        assert result.stock_name == "测试股票"
        assert result.category == "deep-search"
        assert result.content == "测试内容"
        assert Path(result.file_path).exists()

        # 验证文件名包含时间戳
        assert re.search(r'\d{4}-\d{2}-\d{2}-\d{6}', Path(result.file_path).name)
    finally:
        shutil.rmtree(temp_dir)


def test_read_temp_data():
    """测试读取临时数据"""
    from paper_trading.temp_data_manager import TempDataManager

    temp_dir = tempfile.mkdtemp()
    manager = TempDataManager(base_dir=temp_dir, validate_stock=False)

    try:
        # 先保存数据
        saved = manager.save_temp_data(
            stock_name="测试股票",
            category="deep-search",
            content="原始内容"
        )

        # 读取最新数据
        read = manager.read_temp_data(
            stock_name="测试股票",
            category="deep-search"
        )

        assert read is not None
        assert read.stock_name == "测试股票"
        assert read.category == "deep-search"
        assert read.content == "原始内容"
        assert read.file_path == saved.file_path
    finally:
        shutil.rmtree(temp_dir)
