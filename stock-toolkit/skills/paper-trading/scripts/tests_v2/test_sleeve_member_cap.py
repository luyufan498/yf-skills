"""事件槽成员帽回归（9/2 用户裁决 MAX_SLOT_MEMBERS=3：一般 1-2 只即够）

- ① 开槽 >3 只 → ValueError，槽不落库、消息池零扣款
- ② G3 并入超帽 → ValueError，原槽成员/预算零变化
- ③ 帽内路径不受影响：3 只开槽成功、并入至帽内成功、已在槽成员重复 merge 不占名额
- ④ --force 不豁免帽（策略硬帽，与同票双组冲突的 force 降级语义不同）
隔离：pytest 临时 workspace，零触网，生产库零接触。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from paper_trading_v2.db import get_connection
from paper_trading_v2.sleeve_open import SleeveOpener, MAX_SLOT_MEMBERS


@pytest.fixture
def env(ws, monkeypatch):
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', str(ws))
    from paper_trading_v2.cli import app
    from tests_v2.test_sleeve_cli import _run
    _run(app, 'master-pool-init', '--amount', '10000000')
    _run(app, 'sleeve-pool-init', '--amount', '2000000')
    return ws


def _db(env):
    return env / 'master_pool.db'


def _slot_row(env, event_key):
    conn = get_connection(_db(env))
    try:
        r = conn.execute("SELECT * FROM event_slots WHERE event_key=?",
                         (event_key,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _sleeve_free(env):
    conn = get_connection(_db(env))
    try:
        return conn.execute("SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
    finally:
        conn.close()


def test_cap_blocks_over_cap_open(env):
    """① 开槽 4 只 > 帽 → 拒绝且库零变化（不建槽不扣钱）。"""
    free0 = _sleeve_free(env)
    with pytest.raises(ValueError, match="成员帽"):
        SleeveOpener(_db(env)).open_slot(
            ['甲', '乙', '丙', '丁'], budget=400000, event_key='ND#C1')
    assert _slot_row(env, 'ND#C1') is None
    assert _sleeve_free(env) == free0


def test_cap_allows_exact_cap_open(env):
    """③a 恰好 3 只开槽成功（帽边界内），每股等权。"""
    out = SleeveOpener(_db(env)).open_slot(
        ['甲', '乙', '丙'], budget=300000, event_key='ND#C2')
    assert out
    assert _slot_row(env, 'ND#C2') is not None
    assert _sleeve_free(env) == pytest.approx(2000000 - 300000)


def test_cap_blocks_over_cap_merge(env):
    """② 2 成员槽并 2 新（=4 超帽）→ 拒绝；原槽成员/预算/资金零变化。"""
    op = SleeveOpener(_db(env))
    op.open_slot(['甲', '乙'], budget=200000, event_key='ND#C3')
    free0 = _sleeve_free(env)
    with pytest.raises(ValueError, match="成员帽"):
        op.open_slot(['丙', '丁'], budget=200000, event_key='ND#C3')
    conn = get_connection(_db(env))
    try:
        n = conn.execute("SELECT COUNT(*) FROM event_slot_members "
                         "WHERE event_key='ND#C3'").fetchone()[0]
        b = conn.execute("SELECT budget FROM event_slots "
                         "WHERE event_key='ND#C3'").fetchone()[0]
    finally:
        conn.close()
    assert n == 2 and b == 200000
    assert _sleeve_free(env) == free0


def test_merge_within_cap_ok(env):
    """③b 2 成员并 1 新（=3 帽内）成功；重复成员 merge 不重复占名额。"""
    op = SleeveOpener(_db(env))
    op.open_slot(['甲', '乙'], budget=200000, event_key='ND#C4')
    op.open_slot(['丙'], budget=100000, event_key='ND#C4')          # 并至 3=帽内
    op.open_slot(['甲'], budget=0, event_key='ND#C4')               # 已在槽，幂等不占新名额
    conn = get_connection(_db(env))
    try:
        stocks = {r[0] for r in conn.execute(
            "SELECT stock FROM event_slot_members WHERE event_key='ND#C4'")}
    finally:
        conn.close()
    assert stocks == {'甲', '乙', '丙'}


def test_force_does_not_bypass_cap(env):
    """④ --force 不豁免成员帽（策略硬帽，不同于双组冲突的人工裁决放行）。"""
    free0 = _sleeve_free(env)
    with pytest.raises(ValueError, match="成员帽"):
        SleeveOpener(_db(env)).open_slot(
            ['甲', '乙', '丙', '丁'], budget=400000, event_key='ND#C5', force=True)
    assert _slot_row(env, 'ND#C5') is None
    assert _sleeve_free(env) == free0


def test_exited_member_frees_slot(env):
    """帽按活跃成员计：已退出成员（exited_at）不占名额。"""
    op = SleeveOpener(_db(env))
    op.open_slot(['甲', '乙', '丙'], budget=300000, event_key='ND#C6')
    conn = get_connection(_db(env))
    with conn:
        conn.execute("UPDATE event_slot_members SET exited_at=? "
                     "WHERE event_key='ND#C6' AND stock='甲'",
                     (os.environ.get('FAKE_NOW', '2026-09-02T15:00:00'),))
    conn.close()
    # 甲已退出 → 活跃 2 + 新 1 = 3 ≤ 帽，允许
    op.open_slot(['丁'], budget=100000, event_key='ND#C6')
    conn = get_connection(_db(env))
    try:
        assert conn.execute("SELECT 1 FROM event_slot_members "
                            "WHERE event_key='ND#C6' AND stock='丁'").fetchone()
    finally:
        conn.close()
