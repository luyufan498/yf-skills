"""SqlStorage 读写/水合/保序"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from paper_trading_v2.models import (
    Account, AccountHistory, CapitalPool, ExRightAppliedRecord, Operation, Position,
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
    # v9（M1.6 账户层退役）：positions→trades（positions 仅存为兼容视图）、
    # accounts 退役为 accounts_old（保留禁 DROP）
    for t in ['trades', 'operations', 'conditions', 'condition_history',
              'exright_applied', 'schema_meta', 'position', 'accounts_old']:
        assert t in tables, f"缺表 {t}"
    assert 'accounts' not in tables, "accounts 应已退役（仅存 accounts_old）"
    views = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view'").fetchall()]
    # 2026-09-04 v10 债清理：U1 兼容视图+触发器垫片已删（新代码全走 trades 表）
    assert 'positions' not in views, "U1 垫片已清：positions 视图不应存在（直读 trades）"
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

def test_fifo_index_offset_roundtrip(store):
    account = Account(
        stock_name='奥来德', stock_code='sz300331',
        capital_pool=CapitalPool(total=500000, available=500000, used=0),
        positions=[
            Position(stock_code='sz300331', quantity=1000, price=50.0,
                     total_cost=50000, operation='buy', timestamp='2026-01-01T10:00:00'),
        ],
        fifo_index=0, fifo_offset=400.0,
    )
    store.save_account(account)
    loaded = store.load_account('奥来德')
    assert loaded.fifo_index == 0
    assert loaded.fifo_offset == 400.0

def test_exright_roundtrip(store):
    account = Account(
        stock_name='恒申新材', stock_code='sz000782',
        capital_pool=CapitalPool(total=500000, available=500000, used=0),
        exright_applied=[
            ExRightAppliedRecord(cqr='2026-05-01', fhcontent='10送5', reason='迁移'),
        ],
    )
    store.save_account(account)
    loaded = store.load_account('恒申新材')
    assert len(loaded.exright_applied) == 1
    assert loaded.exright_applied[0].cqr == '2026-05-01'
    assert loaded.exright_applied[0].migrated is False

def test_update_existing_account(store):
    store.save_account(Account(stock_name='赛力斯', stock_code='sh603527',
                               capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    store.save_account(Account(stock_name='赛力斯', stock_code='sh603527',
                               capital_pool=CapitalPool(total=500000, available=400000, used=100000)))
    loaded = store.load_account('赛力斯')
    assert loaded.capital_pool.available == 400000
    # v9 段即账户：used 不再是存储列（=FIFO 占用成本，从 trades 现算）；
    # 无流水段 → used=0，段现金（cash）仍是权威存储
    assert loaded.capital_pool.used == 0

def test_delete_account_cascades(store):
    store.save_account(Account(stock_name='英维克', stock_code='sz000301',
                               capital_pool=CapitalPool(total=500000, available=500000, used=0),
                               positions=[Position(stock_code='sz000301', quantity=100, price=10.0,
                                                   total_cost=1000, operation='buy')]))
    store.delete_account('英维克')
    conn = store._conn()
    try:
        c = conn.execute("SELECT COUNT(*) c FROM trades").fetchone()['c']
        assert c == 0
    finally:
        conn.close()
