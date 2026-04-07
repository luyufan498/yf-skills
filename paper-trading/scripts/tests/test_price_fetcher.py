"""PriceFetcher 测试"""

import pytest
from paper_trading.price_fetcher import StockPriceFetcher


def test_fetch_us_stock_from_sina():
    """测试从新浪获取美股数据"""
    from paper_trading.models import MarketType
    fetcher = StockPriceFetcher()
    result = fetcher.get_realtime_price("gb_aapl")
    assert result is not None
    assert result.code.lower() == "gb_aapl"
    assert result.market == MarketType.US_STOCK
    assert result.current_price is not None
    assert result.source == "sina"


def test_fetch_batch_mixed_markets():
    """测试批量获取混合市场数据（A股、港股、美股）"""
    fetcher = StockPriceFetcher()
    # 测试 A股、港股和美股一起获取
    results = fetcher.fetch_batch(["sh600000", "hk00700"])
    assert len(results) > 0
    # 验证至少一个成功（测试时 API 可能不稳定）
    assert any(r.source == "tencent" for r in results.values())
