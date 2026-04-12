"""测试 KLineDataFetcher 类"""
from paper_trading.kline_fetcher import KLineDataFetcher
import pytest


def test_fetch_kline_data_a_share():
    """测试获取A股K线数据"""
    fetcher = KLineDataFetcher()
    klines = fetcher.fetch_kline_data("sh600000", kline_type='day', count=5)
    assert isinstance(klines, list)
    if klines:  # API 可能不返回数据
        assert all(isinstance(k, dict) for k in klines)
        assert 'date' in klines[0]
        assert 'open' in klines[0]
        assert 'close' in klines[0]


def test_fetch_intraday_data_hk():
    """测试获取港股分时数据"""
    fetcher = KLineDataFetcher()
    intraday = fetcher.fetch_minute_data("hk00700", recent_minutes=10)
    assert isinstance(intraday, dict)
    assert intraday.get('code') == 'hk00700'
    assert 'date' in intraday
    assert 'data' in intraday


def test_fetch_us_stock_10min_kline():
    """测试获取美股10分钟K线数据"""
    fetcher = KLineDataFetcher()
    klines = fetcher.fetch_kline_data("AAPL", kline_type='10min', count=5)
    assert isinstance(klines, list)
    if klines:  # API 可能不返回数据
        assert all(isinstance(k, dict) for k in klines)
        assert 'date' in klines[0]
        assert 'open' in klines[0]
        assert 'close' in klines[0]
        assert 'time' in klines[0]  # 分钟数据应该包含时间字段
