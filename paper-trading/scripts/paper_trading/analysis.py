"""分析报告管理器

提供股票分析报告的保存、读取和查询功能
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from paper_trading.config import get_workspace_config
from paper_trading.models import AnalysisRecord
from paper_trading.code_searcher import StockCodeSearcher, validate_stock_name


class AnalysisManager:
    """分析报告管理器"""
    LATEST_ANALYSIS_FILENAME = "最新分析.md"

    def __init__(self, base_dir: Optional[str] = None, validate_stock: bool = True):
        """
        初始化分析管理器

        Args:
            base_dir: 基础目录路径，默认使用配置文件中的 stocks_analysis_dir（workspace_root/stocks_analysis）
            validate_stock: 是否验证股票名称合法性（默认 True）
        """
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            config = get_workspace_config()
            self.base_dir = config['stocks_analysis_dir']

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.validate_stock = validate_stock

    def _get_stock_dir(self, stock_name: str) -> Path:
        """
        获取股票分析目录

        直接使用原始股票名称，不做清理
        目录结构: stocks_analysis_dir/stock_name
        """
        stock_dir = self.base_dir / stock_name
        stock_dir.mkdir(parents=True, exist_ok=True)
        return stock_dir

    def _generate_filename(self, stock_name: str, timestamp: datetime = None) -> str:
        """
        生成分析文件名
        固定使用 timestamp 模式：{股票名称}-YYYY-MM-DD-HHMM.md

        Args:
            stock_name: 股票名称
            timestamp: 时间戳（默认当前时间）

        Returns:
            文件名
        """
        if timestamp is None:
            timestamp = datetime.now()
        time_str = timestamp.strftime("%Y-%m-%d-%H%M")
        return f"{stock_name}-{time_str}.md"

    def _create_latest_symlink(self, target_dir: Path, filepath: Path):
        """
        创建或更新最新分析软链接

        Args:
            target_dir: 目标基础目录
            filepath: 实际文件路径
        """
        symlink_name = self.LATEST_ANALYSIS_FILENAME
        symlink_path = target_dir / symlink_name

        # 删除旧的软链接
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink()

        # 创建指向相对路径的软链接
        relative_path = filepath.name
        symlink_path.symlink_to(relative_path)
        print(f"🔗 已更新软链接：{symlink_path} -> {relative_path}")

    def save_analysis(
        self,
        stock_name: str,
        content: str,
        stock_code: Optional[str] = None,
        timestamp: datetime = None
    ) -> AnalysisRecord:
        """
        保存股票分析报告

        Args:
            stock_name: 股票名称
            content: 分析内容（Markdown格式）
            stock_code: 股票代码（可选，如果不提供会自动查询）
            timestamp: 时间戳（默认当前时间）

        Returns:
            AnalysisRecord 对象

        Raises:
            ValueError: 股票名称验证失败
        """
        # 验证股票名称
        if self.validate_stock:
            is_valid, auto_code = validate_stock_name(stock_name)
            if not is_valid:
                raise ValueError(f"❌ 股票名称 '{stock_name}' 未能通过验证，请确保使用正确的股票名称")
            # 如果未提供股票代码，使用查询到的代码
            if stock_code is None:
                stock_code = auto_code

        stock_dir = self._get_stock_dir(stock_name)
        filename = self._generate_filename(stock_name, timestamp)
        filepath = stock_dir / filename

        # 保存文件
        filepath.write_text(content, encoding='utf-8')
        print(f"✅ 分析已保存：{filepath}")

        # 创建软链接指向最新分析
        self._create_latest_symlink(stock_dir, filepath)

        return AnalysisRecord(
            stock_name=stock_name,
            stock_code=stock_code,
            content=content,
            timestamp=timestamp.isoformat() if timestamp else datetime.now().isoformat(),
            file_path=str(filepath)
        )

    def read_analysis(self, stock_name: str, filename: str = None) -> Optional[AnalysisRecord]:
        """
        读取分析报告

        Args:
            stock_name: 股票名称
            filename: 文件名（可选，默认读取最新的）

        Returns:
            AnalysisRecord 对象，如果不存在返回 None
        """
        stock_dir = self._get_stock_dir(stock_name)

        if filename:
            filepath = stock_dir / filename
        else:
            # 读取最新分析（通过软链接）
            symlink_path = stock_dir / self.LATEST_ANALYSIS_FILENAME
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
        # 从文件名中提取时间戳
        time_match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{4})', filepath.name)
        timestamp_str = time_match.group(1) if time_match else ""
        # 转换为 ISO 格式
        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d-%H%M").isoformat()
        except ValueError:
            timestamp = ""

        return AnalysisRecord(
            stock_name=stock_name,
            content=content,
            timestamp=timestamp,
            file_path=str(filepath)
        )

    def list_analyses(self, stock_name: str, limit: int = 10) -> List[Path]:
        """
        列出某股票的所有分析记录

        Args:
            stock_name: 股票名称
            limit: 最多返回的记录数

        Returns:
            文件路径列表（按时间倒序）
        """
        stock_dir = self._get_stock_dir(stock_name)

        if not stock_dir.exists():
            return []

        files = list(stock_dir.glob("*.md"))
        # 排除软链接
        files = [f for f in files if not f.is_symlink()]
        # 按文件名排序（包含时间戳）
        files.sort(key=lambda x: x.name, reverse=True)

        return files[:limit]

    def read_analyses_count(self, stock_name: str, count: int = 1) -> List[AnalysisRecord]:
        """
        读取最近 N 份分析报告

        Args:
            stock_name: 股票名称
            count: 要读取的份数（默认 1）

        Returns:
            AnalysisRecord 对象列表（按时间倒序）
        """
        files = self.list_analyses(stock_name, limit=count)
        records = []

        for filepath in files:
            if not filepath.exists():
                continue

            content = filepath.read_text(encoding='utf-8')
            # 从文件名中提取时间戳
            time_match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{4})', filepath.name)
            timestamp_str = time_match.group(1) if time_match else ""
            # 转换为 ISO 格式
            try:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d-%H%M").isoformat()
            except ValueError:
                timestamp = ""

            records.append(AnalysisRecord(
                stock_name=stock_name,
                content=content,
                timestamp=timestamp,
                file_path=str(filepath)
            ))

        return records

    def list_stocks(self) -> List[str]:
        """
        列出所有已分析的股票

        Returns:
            股票名称列表
        """
        if not self.base_dir.exists():
            return []

        stocks = []
        for item in self.base_dir.iterdir():
            if item.is_dir():
                stocks.append(item.name)

        return sorted(stocks)
