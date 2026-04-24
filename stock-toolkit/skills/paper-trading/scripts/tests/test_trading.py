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


def test_get_account_preserves_realized_profit(trader):
    """Bug regression: get_account() must NOT wipe realized profit by resetting available = total - used."""
    from paper_trading.models import Operation, OperationType

    account = trader.init_account("测试股票", capital=100000, stock_code="sh000001", force=True)

    # Record buy and sell operations (real flow: init 100k, buy 50k, sell 60k)
    # Available after sell = 100000 - 50000 + 60000 = 110000 (profit of 10000 baked in)
    buy_op = Operation(type=OperationType.BUY, price=50.0, quantity=1000, amount=50000)
    trader.storage.save_operation("测试股票", buy_op)

    sell_op = Operation(
        type=OperationType.SELL,
        price=60.0,
        quantity=1000,
        amount=60000,
        cost=50000,
        profit=10000
    )
    trader.storage.save_operation("测试股票", sell_op)

    # Force a corrupted low available value that old logic would recreate
    account2 = trader.storage.load_account("测试股票")
    account2.capital_pool.available = 100000  # corrupted: should be 110000
    account2.capital_pool.used = 0
    trader.storage.save_account(account2)

    # Next read through get_account() must rebuild to 110000 from operation history
    reloaded = trader.get_account("测试股票")
    assert abs(reloaded.capital_pool.available - 110000) < 0.01
    assert abs(reloaded.capital_pool.used - 0) < 0.01
    assert abs(reloaded.capital_pool.current_total - 110000) < 0.01


def test_get_account_recalibrates_available_from_operations(trader):
    """If available is corrupted, get_account() should rebuild it from operations."""
    from paper_trading.models import Operation, OperationType

    account = trader.init_account("测试股票2", capital=100000, stock_code="sh000002", force=True)

    # Corrupt available
    account.capital_pool.available = 50000
    trader.storage.save_account(account)

    buy_op = Operation(type=OperationType.BUY, price=10, quantity=1000, amount=10000)
    sell_op = Operation(type=OperationType.SELL, price=12, quantity=1000, amount=12000, cost=10000, profit=2000)
    trader.storage.save_operation("测试股票2", buy_op)
    trader.storage.save_operation("测试股票2", sell_op)

    # Expected: 100000 - 10000 + 12000 = 102000
    reloaded = trader.get_account("测试股票2")
    assert abs(reloaded.capital_pool.available - 102000) < 0.01


def test_get_account_recalibrates_used_from_positions(trader):
    """If used is corrupted, get_account() should rebuild it from remaining positions."""
    from paper_trading.models import Position, OperationType

    account = trader.init_account("测试股票3", capital=100000, stock_code="sh000003", force=True)
    pos = Position(stock_code="sh000003", quantity=500, price=20, total_cost=10000, operation=OperationType.BUY)
    account.positions.append(pos)
    account.capital_pool.available = 90000
    account.capital_pool.used = 5000  # corrupted: should be 10000
    trader.storage.save_account(account)

    reloaded = trader.get_account("测试股票3")
    assert abs(reloaded.capital_pool.used - 10000) < 0.01


def test_capital_pool_current_total_and_usage_rate():
    """current_total and usage_rate should reflect available + used without recalibration."""
    from paper_trading.models import CapitalPool

    pool = CapitalPool(total=100000, available=70000, used=30000)
    assert pool.current_total == 100000
    assert abs(pool.usage_rate - 30.0) < 0.01  # 30000 / 100000 * 100


def test_usage_rate_after_profit(trader):
    """Usage rate should decrease relative to total capital after a profit."""
    from paper_trading.models import Operation, OperationType, Position

    account = trader.init_account("测试股票5", capital=100000, stock_code="sh000005", force=True)
    pos = Position(stock_code="sh000005", quantity=1000, price=50, total_cost=50000, operation=OperationType.BUY)
    account.positions.append(pos)
    account.fifo_index = 0
    account.fifo_offset = 0
    account.capital_pool.available = 50000
    account.capital_pool.used = 50000
    trader.storage.save_account(account)

    sell_op = Operation(type=OperationType.SELL, price=60, quantity=1000, amount=60000, cost=50000, profit=10000)
    trader.storage.save_operation("测试股票5", sell_op)

    # Simulate sell effect: available += 60000, used -= 50000
    account2 = trader.storage.load_account("测试股票5")
    account2.capital_pool.available = 110000
    account2.capital_pool.used = 0
    account2.positions = []
    account2.fifo_index = -1
    account2.fifo_offset = 0
    trader.storage.save_account(account2)

    reloaded = trader.get_account("测试股票5")
    assert reloaded.capital_pool.usage_rate == 0.0  # 0 used / 110000 total = 0%
