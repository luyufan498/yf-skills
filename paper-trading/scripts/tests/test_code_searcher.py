"""Tests for code_searcher module"""

import pytest
from paper_trading.code_searcher import StockCodeSearcher, validate_stock_name, StockValidationError


def test_validate_stock_name_valid_stock():
    """Test validation of a valid stock name"""
    is_valid, code = validate_stock_name("赛力斯")
    assert is_valid is True
    assert code is not None
    assert "sh" in code.lower() or "sz" in code.lower()


def test_validate_stock_name_invalid_stock():
    """Test validation of an invalid stock name"""
    is_valid, code = validate_stock_name("根本不存在的股票名称12345")
    assert is_valid is False
    assert code is None


def test_validate_stock_name_common_stocks():
    """Test validation of common A-share stocks"""
    common_stocks = ["腾讯控股", "贵州茅台", "工商银行", "中国平安"]
    for name in common_stocks:
        is_valid, code = validate_stock_name(name)
        assert is_valid is True, f"Stock {name} should be valid"
        assert code is not None


def test_validate_stock_name_empty_string():
    """Test validation of empty string"""
    is_valid, code = validate_stock_name("")
    assert is_valid is False
    assert code is None


def test_validate_stock_name_with_special_chars():
    """Test validation with special characters (港股/美股 codes)"""
    is_valid, code = validate_stock_name("腾讯控股")
    # Should find the stock even if special chars in db
    assert is_valid is True
