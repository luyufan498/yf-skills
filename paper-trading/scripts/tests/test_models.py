"""测试数据模型"""

from paper_trading.models import KLineData, IntradayData
from pydantic import ValidationError
import pytest


def test_kline_data_valid():
    """测试有效的 KLineData"""
    kline = KLineData(
        code="sh600000",
        date="2024-04-07",
        open=10.5,
        close=10.8,
        high=10.9,
        low=10.4,
        volume=1000000,
        amount=10800000.0
    )
    assert kline.code == "sh600000"
    assert kline.open == 10.5
    assert kline.close == 10.8


def test_kline_data_invalid():
    """测试无效的 KLineData - 空代码"""
    with pytest.raises(ValidationError):
        KLineData(
            code="",
            date="2024-04-07",
            open=10.5,
            close=10.8,
            high=10.9,
            low=10.4,
            volume=1000000
        )


def test_intraday_data_valid():
    """测试有效的 IntradayData"""
    intraday = IntradayData(
        code="sh600000",
        date="2024-04-07",
        time=["09:30", "09:31", "09:32"],
        price=[10.5, 10.55, 10.6],
        volume=[100, 200, 150],
        amount=[1050, 2110, 1590]
    )
    assert intraday.code == "sh600000"
    assert len(intraday.time) == 3
    assert intraday.time[0] == "09:30"
