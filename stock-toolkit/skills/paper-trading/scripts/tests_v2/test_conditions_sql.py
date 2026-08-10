"""条件系统在 SqlStorage 上回归"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from paper_trading_v2.models import (
    Account, CapitalPool,
)
from paper_trading_v2.conditions import (
    ConditionsRecord, Condition, ConditionChange,
)

@pytest.fixture
def cm(ws):
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    s = SqlStorage(ws / 'master_pool.db')
    s.save_account(Account(stock_name='赛力斯', stock_code='sh603527',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    return ConditionsManager(storage=s)

def test_conditions_roundtrip(cm):
    cond = Condition(
        id='abc12345', type='trailing_stop', name='移动止损', price=75.0,
        action='减仓50%', category='hard', status='active', peak_price=78.0,
        created_at='2026-06-01T11:12:32', modified_at='2026-06-04T11:00:09',
        history=[ConditionChange(old_price=78.0, new_price=75.0, reason='浮亏下移',
                                 timestamp='2026-06-03T11:00:00', level='reason')],
    )
    record = ConditionsRecord(stock_name='赛力斯', conditions={'trailing_stop': cond})
    cm.save_conditions(record)
    loaded = cm.load_conditions('赛力斯')
    assert loaded is not None
    assert 'trailing_stop' in loaded.conditions
    ts = loaded.conditions['trailing_stop']
    assert ts.price == 75.0
    assert ts.peak_price == 78.0
    assert ts.id == 'abc12345'          # 关键：app 级 uid 保留
    assert ts.created_at == '2026-06-01T11:12:32'
    assert len(ts.history) == 1
    assert ts.history[0].old_price == 78.0
    assert ts.history[0].new_price == 75.0

def test_event_conditions_preserved(cm):
    # 事件条件 type 统一为 trailing_stop（add_event_condition 的同款用法）
    ev = Condition(id='ev000001', type='trailing_stop', name='事件A', price=100.0,
                   action='加仓', category='soft', status='active')
    record = ConditionsRecord(stock_name='赛力斯', events=[ev])
    cm.save_conditions(record)
    loaded = cm.load_conditions('赛力斯')
    assert len(loaded.events) == 1
    assert loaded.events[0].id == 'ev000001'   # 事件 id 保留 → trigger_event_condition 才能匹配
    assert loaded.events[0].type == 'trailing_stop'

def test_empty_conditions_return_none(cm):
    assert cm.load_conditions('不存在') is None

def test_status_and_override_triggers_roundtrip(cm):
    cond = Condition(
        id='s1', type='trailing_stop', name='移动止损', price=80.0,
        action='减仓50%', category='hard', status='suspended', peak_price=90.0,
        history=[ConditionChange(old_price=90.0, new_price=80.0, reason='测试',
                                 timestamp='2026-06-01T10:00:00', level='reason',
                                 override_triggers=['硬条件减仓'])],
    )
    cm.save_conditions(ConditionsRecord(stock_name='赛力斯', conditions={'trailing_stop': cond}))
    loaded = cm.load_conditions('赛力斯')
    ts = loaded.conditions['trailing_stop']
    assert ts.status == 'suspended'
    assert len(ts.history) == 1
    assert ts.history[0].override_triggers == ['硬条件减仓']

def test_multi_history_order_preserved(cm):
    cond = Condition(
        id='h1', type='trailing_stop', name='移动止损', price=70.0,
        action='减仓50%', category='hard', status='active',
        history=[
            ConditionChange(old_price=80.0, new_price=75.0, reason='一', timestamp='2026-06-01T10:00:00', level='auto'),
            ConditionChange(old_price=75.0, new_price=70.0, reason='二', timestamp='2026-06-02T10:00:00', level='reason'),
        ],
    )
    cm.save_conditions(ConditionsRecord(stock_name='赛力斯', conditions={'trailing_stop': cond}))
    loaded = cm.load_conditions('赛力斯')
    ts = loaded.conditions['trailing_stop']
    assert len(ts.history) == 2
    assert ts.history[0].new_price == 75.0
    assert ts.history[1].new_price == 70.0

def test_idempotent_resave(cm):
    cond = Condition(id='i1', type='trailing_stop', name='移动止损', price=75.0,
                     action='减仓50%', category='hard', status='active')
    cm.save_conditions(ConditionsRecord(stock_name='赛力斯', conditions={'trailing_stop': cond}))
    cond.price = 72.0
    cm.save_conditions(ConditionsRecord(stock_name='赛力斯', conditions={'trailing_stop': cond}))
    loaded = cm.load_conditions('赛力斯')
    assert len(loaded.conditions) == 1
    assert loaded.conditions['trailing_stop'].price == 72.0
    assert loaded.conditions['trailing_stop'].id == 'i1'

def test_v1_to_v2_migration(ws):
    """v1 DB（conditions 表无新列）→ migrate 后补列 + version=最新（含 v3 池层表 / v4 归档表）"""
    import sqlite3
    from paper_trading_v2.db import get_connection, migrate_db, SCHEMA_VERSION
    db = ws / 'master_pool.db'
    conn = get_connection(db)
    conn.executescript("""
        CREATE TABLE schema_meta (version INTEGER NOT NULL, migrated_at TEXT);
        CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, stock_name TEXT UNIQUE NOT NULL, stock_code TEXT, capital_total REAL NOT NULL, capital_available REAL NOT NULL, capital_used REAL NOT NULL, fifo_index INTEGER DEFAULT -1, fifo_offset REAL DEFAULT 0, created_at TEXT, updated_at TEXT);
        CREATE TABLE conditions (id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL, cond_key TEXT, is_event INTEGER DEFAULT 0, type TEXT NOT NULL, name TEXT, price REAL, action TEXT, category TEXT, expiry_date TEXT, status TEXT, auto_link_cost INTEGER DEFAULT 0, peak_price REAL, seq INTEGER);
        CREATE TABLE condition_history (id INTEGER PRIMARY KEY AUTOINCREMENT, condition_id INTEGER NOT NULL, old_price REAL, new_price REAL, reason TEXT, timestamp TEXT, level TEXT, override_triggers TEXT);
        INSERT INTO schema_meta (version, migrated_at) VALUES (1, 'x');
    """)
    conn.commit()
    conn.close()
    conn = get_connection(db)
    migrate_db(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(conditions)").fetchall()]
    assert 'cond_uid' in cols and 'created_at' in cols and 'modified_at' in cols
    v = conn.execute("SELECT version FROM schema_meta").fetchone()[0]
    assert v == SCHEMA_VERSION
    # v3 池层表 + v4 归档表也应建齐
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in ['pool', 'position', 'pool_ledger', 'audit', 'watchlog', 'operations_archive']:
        assert t in tables
    conn.close()
