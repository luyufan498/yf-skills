"""临时数据管理器

提供股票临时数据的保存、读取和查询功能
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from paper_trading.config import get_stock_temp_data_dir, get_workspace_config
from paper_trading.models import TempDataRecord


class TempDataManager:
    """临时数据管理器"""

    LATEST_FILENAME = "最新.md"

    def __init__(self, base_dir: Optional[str] = None, validate_stock: bool = True):
        """
        初始化临时数据管理器

        Args:
            base_dir: 基础目录路径，默认使用配置文件中的 temp_data_dir
            validate_stock: 是否验证股票名称合法性（默认 True）
        """
        if base_dir:
            self.temp_data_dir = Path(base_dir)
        else:
            config = get_workspace_config()
            self.temp_data_dir = config['temp_data_dir']

        self.temp_data_dir.mkdir(parents=True, exist_ok=True)
        self.validate_stock = validate_stock

    def _get_category_dir(self, stock_name: str, category: str) -> Path:
        """
        获取类别目录路径

        Args:
            stock_name: 股票名称
            category: 数据类别

        Returns:
            Path: 类别目录路径，格式为 temp_data_dir/stock_name/category
        """
        category_dir = get_stock_temp_data_dir(stock_name, category)
        category_dir.mkdir(parents=True, exist_ok=True)
        return category_dir

    def _generate_filename(self, timestamp: datetime = None) -> str:
        """
        生成文件名（使用时间戳）

        格式: YYYY-MM-DD-HHMMSS.md

        Args:
            timestamp: 时间戳（默认当前时间）

        Returns:
            文件名
        """
        if timestamp is None:
            timestamp = datetime.now()
        time_str = timestamp.strftime("%Y-%m-%d-%H%M%S")
        return f"{time_str}.md"

    def _parse_timestamp_from_filename(self, filepath: Path) -> str:
        """
        从文件名中解析时间戳并转换为 ISO 格式

        Args:
            filepath: 文件路径

        Returns:
            ISO 格式的时间戳字符串
        """
        time_match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{6})', filepath.name)
        timestamp_str = time_match.group(1) if time_match else ""
        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d-%H%M%S").isoformat()
        except ValueError:
            timestamp = ""
        return timestamp

    def save_temp_data(
        self,
        stock_name: str,
        category: str,
        content: str,
        timestamp: datetime = None
    ) -> TempDataRecord:
        """
        保存临时数据

        Args:
            stock_name: 股票名称
            category: 数据类别（如 'deep-search', 'history-continuity', 'gf-summary'）
            content: 数据内容
            timestamp: 时间戳（默认当前时间）

        Returns:
            TempDataRecord 对象

        Raises:
            ValueError: 类别为空或验证失败
        """
        # 验证类别参数
        if not category or not category.strip():
            raise ValueError("类别不能为空，请指定有效的数据类别")

        # 验证股票名称
        if self.validate_stock:
            from paper_trading.code_searcher import validate_stock_name
            is_valid, _ = validate_stock_name(stock_name)
            if not is_valid:
                raise ValueError(f"股票名称 '{stock_name}' 未能通过验证")

        category_dir = self._get_category_dir(stock_name, category)
        filename = self._generate_filename(timestamp)
        filepath = category_dir / filename

        # 保存文件
        filepath.write_text(content, encoding='utf-8')
        print(f"✅ 临时数据已保存：{filepath}")

        # 创建最新数据软链接
        self._create_latest_symlink(category_dir, filepath)

        return TempDataRecord(
            stock_name=stock_name,
            category=category,
            content=content,
            timestamp=timestamp.isoformat() if timestamp else datetime.now().isoformat(),
            file_path=str(filepath)
        )

    def _create_latest_symlink(self, target_dir: Path, filepath: Path):
        """
        创建或更新最新数据软链接

        Args:
            target_dir: 目标目录（类别目录）
            filepath: 实际文件路径
        """
        symlink_path = target_dir / self.LATEST_FILENAME

        # 删除旧的软链接
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink()

        # 创建指向相对路径的软链接
        relative_path = filepath.name
        try:
            symlink_path.symlink_to(relative_path)
            print(f"🔗 已更新软链接：{symlink_path} -> {relative_path}")
        except (OSError, NotImplementedError):
            # 如果系统不支持软链接，静默失败
            pass

    def read_temp_data(
        self,
        stock_name: str,
        category: str,
        filename: str = None
    ) -> Optional[TempDataRecord]:
        """
        读取临时数据

        Args:
            stock_name: 股票名称
            category: 数据类别
            filename: 文件名（可选，默认读取最新的）

        Returns:
            TempDataRecord 对象，如果不存在返回 None
        """
        category_dir = self._get_category_dir(stock_name, category)

        if filename:
            filepath = category_dir / filename
        else:
            # 读取最新数据（通过软链接）
            symlink_path = category_dir / self.LATEST_FILENAME
            if not symlink_path.exists():
                return None
            # 解析软链接指向的实际文件
            try:
                target = symlink_path.readlink()
                filepath = symlink_path.parent / target
            except (OSError, RuntimeError):
                return None

        if not filepath.exists():
            return None

        content = filepath.read_text(encoding='utf-8')
        timestamp = self._parse_timestamp_from_filename(filepath)

        return TempDataRecord(
            stock_name=stock_name,
            category=category,
            content=content,
            timestamp=timestamp,
            file_path=str(filepath)
        )

    def list_temp_data(
        self,
        stock_name: str,
        category: str,
        limit: int = 30
    ) -> List[Path]:
        """
        列出某股票某类别的所有临时数据文件

        Args:
            stock_name: 股票名称
            category: 数据类别
            limit: 最多返回的记录数

        Returns:
            文件路径列表（按时间倒序）
        """
        category_dir = self._get_category_dir(stock_name, category)

        if not category_dir.exists():
            return []

        files = list(category_dir.glob("*.md"))
        # 排除软链接
        files = [f for f in files if not f.is_symlink()]
        # 按文件名排序（包含时间戳）
        files.sort(key=lambda x: x.name, reverse=True)

        return files[:limit]

    def list_categories(self, stock_name: str) -> List[str]:
        """
        列出某股票的所有数据类别

        Args:
            stock_name: 股票名称

        Returns:
            类别名称列表
        """
        stock_dir = get_stock_temp_data_dir(stock_name)

        if not stock_dir.exists():
            return []

        categories = []
        for item in stock_dir.iterdir():
            if item.is_dir():
                categories.append(item.name)

        return sorted(categories)
