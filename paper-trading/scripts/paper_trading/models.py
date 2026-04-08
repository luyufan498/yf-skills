"""数据模型定义

使用 Pydantic 进行数据验证和序列化
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class MarketType(str, Enum):
    """股票市场类型"""
    A_SHARE = "A股"
    HK_STOCK = "港股"
    US_STOCK = "美股"


class OperationType(str, Enum):
    """操作类型"""
    INIT = "init"
    BUY = "buy"
    SELL = "sell"


class StockInfo(BaseModel):
    """股票基本信息"""
    code: str = Field(..., min_length=1, description="股票代码")
    name: str = Field(..., min_length=1, description="股票名称")
    market: MarketType = Field(default=MarketType.A_SHARE, description="市场类型")
    current_price: Optional[float] = None
    pre_close: Optional[float] = None
    open_price: Optional[float] = Field(None, alias="open")
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    source: str = "unknown"

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """验证股票代码"""
        if not v:
            raise ValueError("股票代码不能为空")
        return v.lower()

    class Config:
        use_enum_values = True
        json_encoders = {datetime: lambda v: v.isoformat()}


class CapitalPool(BaseModel):
    """资金池"""
    total: float = Field(..., gt=0, description="总资金")
    available: float = Field(..., description="可用资金")
    used: float = Field(default=0.0, description="占用资金")

    @field_validator("available")
    @classmethod
    def validate_available(cls, v: float) -> float:
        """验证可用资金不超过总资金"""
        return v

    @field_validator("used")
    @classmethod
    def validate_used(cls, v: float) -> float:
        """验证占用资金不超过总资金并允许小的负值为计算误差"""
        return v

    def withdraw(self, amount: float) -> bool:
        """扣减资金"""
        if amount > self.available:
            return False
        self.available -= amount
        self.used += amount
        return True

    def deposit(self, amount: float):
        """增加资金"""
        self.available += amount
        self.used -= amount

    @property
    def usage_rate(self) -> float:
        """资金使用率"""
        return (self.used / self.total * 100) if self.total > 0 else 0.0


class Position(BaseModel):
    """持仓记录"""
    stock_code: str
    quantity: int = Field(..., gt=0)
    price: float = Field(..., gt=0)
    total_cost: float = Field(..., gt=0)
    operation: OperationType
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    note: str = ""

    class Config:
        use_enum_values = True


class Operation(BaseModel):
    """操作记录"""
    type: OperationType
    price: Optional[float] = None
    quantity: Optional[int] = None
    amount: Optional[float] = None
    cost: Optional[float] = None
    profit: Optional[float] = None
    capital: Optional[float] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    note: str = ""

    class Config:
        use_enum_values = True


class Account(BaseModel):
    """账户信息"""
    stock_name: str
    stock_code: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    capital_pool: CapitalPool
    positions: List[Position] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "stock_name": "赛力斯",
                "stock_code": "sh603527",
                "capital_pool": {
                    "total": 100000.0,
                    "available": 100000.0,
                    "used": 0.0
                },
                "positions": []
            }
        }


class AccountHistory(BaseModel):
    """账户历史记录"""
    stock_name: str
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    operations: List[Operation] = Field(default_factory=list)


class PortfolioSummary(BaseModel):
    """投资组合汇总"""
    total_capital: float
    total_available: float
    total_used: float
    total_positions: int
    total_market_value: float
    total_cost: float
    realized_profit: float
    floating_profit: float
    total_profit: float
    return_rate: float


class PerformanceMetrics(BaseModel):
    """性能指标"""
    total_return: float
    annualized_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    total_trades: int
    win_rate: Optional[float] = None
    avg_win_amount: Optional[float] = None
    avg_loss_amount: Optional[float] = None
    profit_loss_ratio: Optional[float] = None
    avg_holding_period: Optional[float] = None
    turnover_rate: Optional[float] = None


class KLineData(BaseModel):
    """K线数据"""
    code: str = Field(..., min_length=1, description="股票代码")
    date: str = Field(..., min_length=1, description="日期")
    open: Optional[float] = Field(None, description="开盘价")
    close: Optional[float] = Field(None, description="收盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")
    volume: Optional[float] = Field(None, description="成交量")
    amount: Optional[float] = Field(None, description="成交额")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """验证股票代码"""
        if not v:
            raise ValueError("股票代码不能为空")
        return v.lower()

    class Config:
        use_enum_values = True


class IntradayData(BaseModel):
    """分时数据"""
    code: str = Field(..., description="股票代码")
    date: str = Field(..., description="交易日期")
    time: Optional[str] = Field(None, description="时间")
    price: Optional[float] = Field(None, description="价格")
    volume: Optional[float] = Field(None, description="成交量")
    amount: Optional[float] = Field(None, description="成交额")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """验证股票代码"""
        if not v:
            raise ValueError("股票代码不能为空")
        return v.lower()

    class Config:
        use_enum_values = True


class NewsItem(BaseModel):
    """单条新闻"""
    title: str = Field(..., description="标题")
    content: str = Field(..., description="内容")
    time: str = Field(..., description="时间 HH:MM:SS")
    date: str = Field(..., description="日期 YYYY-MM-DD")
    datetime: str = Field(..., description="完整时间 ISO 格式")
    url: str = Field(default="", description="新闻链接")
    source: str = Field(..., description="来源: 财联社电报, 新浪财经, TradingView外媒")
    is_red: bool = Field(default=False, description="是否重要")
    tags: List[str] = Field(default_factory=list, description="标签")
    description: str = Field(default="", description="描述")


class MarketNews(BaseModel):
    """市场新闻集合"""
    total: int = Field(..., description="总数量")
    items: List[NewsItem] = Field(default_factory=list, description="新闻列表")


class AnalysisRecord(BaseModel):
    """分析记录"""
    stock_name: str = Field(..., min_length=1, description="股票名称")
    stock_code: Optional[str] = Field(None, description="股票代码")
    content: str = Field(..., description="分析内容（Markdown格式）")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(), description="创建时间")
    file_path: Optional[str] = Field(None, description="文件路径")

    class Config:
        json_schema_extra = {
            "example": {
                "stock_name": "赛力斯",
                "stock_code": "sh603527",
                "content": "# 股票分析报告\\n\\n## 技术面分析\\n...",
                "timestamp": "2026-04-08T22:30:00"
            }
        }
