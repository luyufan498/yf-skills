"""SqlStorage 读写/水合/保序"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from paper_trading_v2.models import (
    Account, AccountHistory, CapitalPool, Operation, Position,
)

@pytest.fixture
def store(ws):
    from paper_trading_v2.storage import SqlStorage
    return SqlStorage(ws / 'master_pool.db')

def test_schema_created(ws):
    from paper_trading_v2.db import get_connection, migrate_db
    conn = get_connection(ws / 'master_pool.db')
    migrate_db(conn)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in ['accounts', 'positions', 'operations', 'conditions',
              'condition_history', 'exright_applied', 'schema_meta']:
        assert t in tables
    conn.close()

def test_save_load_account_roundtrip(store):
    account = Account(
        stock_name='赛力斯', stock_code='sh603527',
        capital_pool=CapitalPool(total=500000, available=300000, used=200000),
        positions=[
            Position(stock_code='sh603527', quantity=1000, price=100.0,
                     total_cost=100000, operation='buy', timestamp='2026-01-01T10:00:00'),
            Position(stock_code='sh603527', quantity=500, price=120.0,
                     total_cost=60000, operation='buy', timestamp='2026-01-02T10:00:00'),
        ],
    )
    store.save_account(account)
    loaded = store.load_account('赛力斯')
    assert loaded is not None
    assert loaded.capital_pool.total == 500000
    assert loaded.capital_pool.available == 300000
    assert len(loaded.positions) == 2
    assert loaded.positions[0].operation == 'buy'
    assert loaded.positions[0].price == 100.0
    assert loaded.positions[1].price == 120.0
    assert loaded.stock_code == 'sh603527'

def test_save_load_operations_roundtrip(store):
    store.save_account(Account(stock_name='中科曙光', stock_code='sz000977',
                               capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    ops = AccountHistory(stock_name='中科曙光', operations=[
        Operation(type='init', capital=500000, timestamp='2026-01-01T09:00:00'),
        Operation(type='buy', price=100.0, quantity=1000, amount=100000,
                  timestamp='2026-01-01T10:00:00'),
    ])
    store.save_operations('中科曙光', ops)
    loaded = store.load_operations('中科曙光')
    assert loaded is not None
    assert len(loaded.operations) == 2
    assert loaded.operations[0].type == 'init'
    assert loaded.operations[1].type == 'buy'

def test_list_and_delete(store):
    for name in ['赛力斯', '英维克']:
        store.save_account(Account(stock_name=name,
                                   capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    assert store.list_accounts() == ['赛力斯', '英维克']
    assert store.delete_account('赛力斯') is True
    assert store.list_accounts() == ['英维克']
    assert store.delete_account('不存在') is False

def test_compat_symbols_exist(ws):
    from paper_trading_v2 import storage
    assert hasattr(storage, 'StorageBackend')
    assert hasattr(storage, 'StorageFactory')
    assert hasattr(storage, 'JsonStorage')
    assert storage.JsonStorage is storage.SqlStorage
