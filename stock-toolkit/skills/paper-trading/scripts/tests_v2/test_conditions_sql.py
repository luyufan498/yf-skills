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
