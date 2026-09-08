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

def test_atr_sync_skips_triggered_trailing(cm):
    """2026-09-07（中芯 #1962 教训）：triggered 态 trailing 线不被 ATR 棘轮抬升。

    已触发线若继续"只升不降"会被抬出虚高触发价（125.56→126.60 案例）
    → 同轮破位重复计账 + 虚触发。重建（恢复 active）后棘轮才恢复。
    """
    from paper_trading_v2.conditions_manager import ConditionsManager
    rec = ConditionsRecord(stock_name='赛力斯', updated_at='x')
    rec.conditions['trailing_stop'] = Condition(
        id='trailing_stop', type='trailing_stop', name='移动止损',
        price=100.0, action='清仓', category='hard',
        status='triggered', peak_price=110.0)
    cm.save_conditions(rec)
    # 调用 sync_trailing_stop：旧 peak 110 且本轮 high 更高 → 若被抬会升价
    klines = [{'high': 115.0, 'low': 108.0, 'close': 114.0, 'date': '2026-09-07'}]
    cm.sync_trailing_stop('赛力斯', avg_cost=120.0, klines=klines,
                          atr=4.0, realtime_high=115.0)
    loaded = cm.load_conditions('赛力斯')
    ts = loaded.conditions['trailing_stop']
    assert ts.price == 100.0, f'triggered 线不应被抬升，实得 {ts.price}'
    assert ts.peak_price == 110.0, 'triggered 线 peak 也不应更新'
    assert ts.status == 'triggered'

def test_atr_sync_active_still_ratchets(cm):
    """active 线棘轮抬升照常（回归护栏：修 triggered 跳过后 active 行为不变）。"""
    from paper_trading_v2.conditions_manager import ConditionsManager
    rec = ConditionsRecord(stock_name='赛力斯', updated_at='x')
    rec.conditions['trailing_stop'] = Condition(
        id='trailing_stop', type='trailing_stop', name='移动止损',
        price=100.0, action='清仓', category='hard',
        status='active', peak_price=110.0)
    cm.save_conditions(rec)
    klines = [{'high': 115.0, 'low': 108.0, 'close': 114.0, 'date': '2026-09-07'}]
    cm.sync_trailing_stop('赛力斯', avg_cost=120.0, klines=klines,
                          atr=4.0, realtime_high=115.0)
    loaded = cm.load_conditions('赛力斯')
    ts = loaded.conditions['trailing_stop']
    assert ts.price >= 100.0, 'active 线仍走棘轮（peak115−2.5×4=105 → max(100,105)=105）'
    assert ts.price == 105.0, f'预期 105.0，实得 {ts.price}'

def test_update_down_rebuild_clamps_peak(cm):
    """2026-09-08 根修：update 下调 trailing_stop（重建线）→ peak 同步钳制。

    赣锋判例：线 49.38(旧peak 54.67 抬的虚高) 下调至 46.42 重建——若 peak 残留
    54.67，下次 atr-sync 会再抬回 ~49.38 → 虚破位复发。下调即重建语义 → peak=新价。
    """
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.conditions import ConditionsRecord, Condition, ConditionType
    rec = ConditionsRecord(stock_name='赛力斯', updated_at='x')
    rec.conditions['trailing_stop'] = Condition(
        id='trailing_stop', type='trailing_stop', name='移动止损',
        price=49.38, action='清仓', category='hard',
        status='active', peak_price=54.67)
    cm.save_conditions(rec)
    result, record = cm.update_condition(
        '赛力斯', ConditionType.TRAILING_STOP,
        new_price=46.42, current_price=48.86, avg_cost=52.0,
        has_position=True, user_reason='测试：减仓后重建恢复期线')
    assert result.allowed
    ts = record.conditions['trailing_stop']
    assert ts.price == 46.42
    assert ts.peak_price == 46.42, f'peak 应钳制为新价 46.42，实得 {ts.peak_price}'
    assert 'peak 同步钳制' in ts.history[-1].reason

def test_update_up_no_peak_clamp(cm):
    """上调 trailing 不动 peak（止盈上移/棘轮路径不受干扰）。"""
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.conditions import ConditionsRecord, Condition
    rec = ConditionsRecord(stock_name='赛力斯', updated_at='x')
    rec.conditions['trailing_stop'] = Condition(
        id='trailing_stop', type='trailing_stop', name='移动止损',
        price=46.42, action='清仓', category='hard',
        status='active', peak_price=54.67)
    cm.save_conditions(rec)
    from paper_trading_v2.conditions import ConditionType
    result, record = cm.update_condition(
        '赛力斯', ConditionType.TRAILING_STOP,
        new_price=50.0, current_price=55.0, avg_cost=52.0,
        has_position=True, user_reason='测试上调')
    ts = record.conditions['trailing_stop']
    assert ts.peak_price == 54.67, '上调不改 peak'
