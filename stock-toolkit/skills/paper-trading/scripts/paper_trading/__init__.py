"""Paper Trading System - 模拟盘交易系统

支持A股、港股和美股的模拟交易，管理独立资金池和持仓。
包含完整的股票价格获取、K线数据和代码搜索功能。
"""

__version__ = "1.0.2"
__author__ = "Paper Trading Team"

# 导出主要类和模型
from paper_trading.models import (
    MarketType,
    OperationType,
    StockInfo,
    CapitalPool,
    Position,
    Operation,
    Account,
    AccountHistory,
    PortfolioSummary,
    PerformanceMetrics,
    KLineData,
    IntradayData,
    NewsItem,        # 新增
    MarketNews,      # 新增
)

from paper_trading.price_fetcher import StockPriceFetcher
from paper_trading.kline_fetcher import KLineDataFetcher
from paper_trading.code_searcher import StockCodeSearcher, validate_stock_name, StockValidationError
from paper_trading.news_fetcher import MarketNewsFetcher

__all__ = [
    # 模型
    'MarketType',
    'OperationType',
    'StockInfo',
    'CapitalPool',
    'Position',
    'Operation',
    'Account',
    'AccountHistory',
    'PortfolioSummary',
    'PerformanceMetrics',
    'KLineData',
    'IntradayData',
    'NewsItem',      # 新增
    'MarketNews',    # 新增
    # 获取器
    'StockPriceFetcher',
    'KLineDataFetcher',
    'StockCodeSearcher',
    'validate_stock_name',
    'StockValidationError',
    'MarketNewsFetcher',  # 新增
]
