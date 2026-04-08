"""Test cases for PaperTrader"""

import pytest
import os
import tempfile
import shutil
from paper_trading.trading import PaperTrader


@pytest.fixture
def temp_storage():
    """Create a temporary storage directory"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def trader(temp_storage):
    """Create a PaperTrader instance with temporary storage"""
    from paper_trading.storage import StorageFactory
    storage = StorageFactory.create_storage("json", base_dir=temp_storage)
    return PaperTrader(storage=storage)


def test_init_account_validates_stock_name(trader):
    """Test that init_account validates stock names"""
    # Valid stock should succeed
    account = trader.init_account("赛力斯", capital=100000, stock_code="sh603527", force=True)
    assert account.stock_name == "赛力斯"
    assert account.capital_pool.total == 100000


def test_init_account_rejects_invalid_stock_name(trader):
    """Test that init_account rejects invalid stock names"""
    # Invalid stock should raise ValueError
    with pytest.raises(ValueError, match="股票名称.*未能通过验证"):
        trader.init_account("根本不存在的股票名称12345", capital=100000, force=True)


def test_init_account_auto_finds_valid_stock_code(trader):
    """Test that init_account can auto-find code for valid stock"""
    # Should auto-find the code for 赛力斯
    account = trader.init_account("赛力斯", capital=100000, force=True)
    assert account.stock_code is not None
    assert len(account.stock_code) > 0


def test_init_account_skips_validation_with_explicit_code(trader):
    """Test that validation is skipped when code is explicitly provided"""
    # Should not validate if stock_code is provided
    account = trader.init_account("测试股票名称", capital=100000, stock_code="sh000001", force=True)
    assert account.stock_name == "测试股票名称"
    assert account.stock_code == "sh000001"
