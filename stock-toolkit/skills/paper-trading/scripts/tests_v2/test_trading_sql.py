"""交易引擎在 SqlStorage 上回归：buy/sell/FIFO/持久化"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from unittest.mock import patch
from paper_trading_v2.models import Account, CapitalPool, Operation, OperationType


@pytest.fixture
def trader(ws):
    from paper_trading_v2.trading import PaperTrader
    return PaperTrader()


def _mk_account(trader, name, code='sz000001'):
    trader.storage.save_account(Account(
        stock_name=name, stock_code=code,
        capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    # 与 init_account 一致：写入 init 操作记录（持久化断言需确认 init+buy 都已入库）
    trader.storage.save_operation(name, Operation(type=OperationType.INIT, capital=500000))


def _patch_price(current_price):
    return patch(
        'paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
        **{'return_value.current_price': current_price})


def test_buy_updates_pool(trader):
    _mk_account(trader, '测试股')
    with _patch_price(100.0):
        trader.buy_stock('测试股', quantity=1000)
    loaded = trader.storage.load_account('测试股')
    assert loaded.capital_pool.available == 400000
    qty, cost = trader.get_remaining_position(loaded)
    assert qty == 1000
    assert abs(cost - 100000) < 1


def test_buy_sell_fifo_cost(trader):
    _mk_account(trader, '测试股')
    with _patch_price(100.0):
        trader.buy_stock('测试股', quantity=1000)   # 1000 @ 100 = 100000
    with _patch_price(120.0):
        trader.buy_stock('测试股', quantity=500)    # 500 @ 120 = 60000
    with _patch_price(110.0):
        trader.sell_stock('测试股', quantity=1200)  # FIFO: 1000@100 + 200@120
    loaded = trader.storage.load_account('测试股')
    qty, cost = trader.get_remaining_position(loaded)
    assert qty == 300
    assert abs(cost - 36000) < 1  # 300 @ 120


def test_buy_by_amount_truncates(trader):
    _mk_account(trader, '测试股')
    with _patch_price(100.0):
        trader.buy_stock('测试股', amount=150000)  # int(150000/100) = 1500 shares
    loaded = trader.storage.load_account('测试股')
    qty, cost = trader.get_remaining_position(loaded)
    assert qty == 1500
    assert loaded.capital_pool.available == 500000 - 150000


def test_sell_all_clears_position(trader):
    _mk_account(trader, '测试股')
    with _patch_price(100.0):
        trader.buy_stock('测试股', quantity=1000)
    with _patch_price(110.0):
        trader.sell_stock('测试股', sell_all=True)
    loaded = trader.storage.load_account('测试股')
    qty, cost = trader.get_remaining_position(loaded)
    assert qty == 0
    assert cost == 0.0
    assert abs(loaded.capital_pool.available - 500000 - 10000) < 1  # 1000*(110-100) realized profit


def test_buy_insufficient_funds_raises(trader):
    _mk_account(trader, '测试股')
    with _patch_price(100.0):
        with pytest.raises(ValueError, match="资金不足"):
            trader.buy_stock('测试股', quantity=100000)  # 10M > 500k available


def test_persistence_across_instances(trader, ws):
    _mk_account(trader, '赛力斯', 'sh603527')
    with _patch_price(100.0):
        trader.buy_stock('赛力斯', quantity=1000)
    from paper_trading_v2.trading import PaperTrader
    trader2 = PaperTrader()  # new instance, same DB (ws fixture)
    loaded = trader2.storage.load_account('赛力斯')
    qty, cost = trader2.get_remaining_position(loaded)
    assert qty == 1000
    ops = trader2.storage.load_operations('赛力斯')
    assert ops is not None
    # 严格顺序：init 先于 buy，且都持久化到 SQL
    assert [op.type for op in ops.operations] == ['init', 'buy']
