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
        time="09:30",
        price=10.5,
        volume=100,
        amount=1050
    )
    assert intraday.code == "sh600000"
    assert intraday.time == "09:30"
    assert intraday.price == 10.5
    assert intraday.volume == 100
    assert intraday.amount == 1050


def test_intraday_data_optional_fields():
    """测试 IntradayData 的可选字段"""
    intraday = IntradayData(
        code="sh600000",
        date="2024-04-07"
    )
    assert intraday.code == "sh600000"
    assert intraday.time is None
    assert intraday.price is None
    assert intraday.volume is None
    assert intraday.amount is None


def test_kline_data_code_lowercase():
    """测试 KLineData 的代码自动转为小写"""
    kline = KLineData(
        code="SH600000",
        date="2024-04-07",
        open=10.5,
        close=10.8
    )
    assert kline.code == "sh600000"


def test_intraday_data_code_lowercase():
    """测试 IntradayData 的代码自动转为小写"""
    intraday = IntradayData(
        code="SZ000001",
        date="2024-04-07",
        time="09:30",
        price=10.5
    )
    assert intraday.code == "sz000001"


def test_intraday_data_invalid():
    """测试无效的 IntradayData - 空代码"""
    with pytest.raises(ValidationError):
        IntradayData(
            code="",
            date="2024-04-07",
            time="09:30",
            price=10.5
        )


def test_temp_data_record_model():
    """测试 TempDataRecord 模型"""
    from paper_trading.models import TempDataRecord
    from datetime import datetime

    record = TempDataRecord(
        stock_name="测试股票",
        category="deep-search",
        content="测试内容",
        timestamp="2026-04-09T10:30:00",
        file_path="/path/to/file.md"
    )

    assert record.stock_name == "测试股票"
    assert record.category == "deep-search"
    assert record.content == "测试内容"
    assert record.file_path == "/path/to/file.md"
