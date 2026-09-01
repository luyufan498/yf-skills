"""G3 归并 / fail-open / 迁移 FIFO 对账 / 加仓锁测试（sleeve-m1）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch

from paper_trading_v2.gate import GateViolation


@pytest.fixture(autouse=True)
def no_network(ws):
    # 价格抓取全 mock：测试注入价（10/20 等）不触网——R7 价差防线以昨收为参照，
    # 未 mock 会拿真实行情判 30% 偏离（测试必须密闭，对齐 /tmp/sleeve_audit conftest）
    with patch('paper_trading_v2.trading.PaperTrader.init_account'), \
         patch('paper_trading_v2.trading.PaperTrader.get_account') as mg, \
         patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=None):
        def _fake_get(stock_name):
            from paper_trading_v2.storage import SqlStorage
            return SqlStorage(ws / 'master_pool.db').load_account(stock_name)
        mg.side_effect = _fake_get
        yield


@pytest.fixture
def env(ws, monkeypatch):
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', str(ws))
    return ws


@pytest.fixture
def pools(ws):
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(ws / 'master_pool.db')
    m.init_pool(10000000)
    m.init_pool(2000000, pool='sleeve')
    return m


def _opener(ws):
    from paper_trading_v2.sleeve_open import SleeveOpener
    return SleeveOpener(ws / 'master_pool.db')


def test_g3_merge_into_active_slot_no_new_slot(pools, ws):
    """G3：同键活跃槽 → 新成员并入，等权重算，不加坑，不重复扣坑。"""
    r1 = _opener(ws).open_slot(['甲票', '乙票'], budget=300000, event_key='ND#1',
                               news_kind='policy', code_map={'甲票': 'sh1', '乙票': 'sh2'})
    assert r1['mode'] == 'open'
    r2 = _opener(ws).open_slot(['丙票'], budget=100000, event_key='ND#1',
                               news_kind='policy', code_map={'丙票': 'sh3'})
    assert r2['mode'] == 'merge'
    conn = pools._conn()
    slots = conn.execute("SELECT COUNT(*) FROM event_slots WHERE event_key='ND#1'").fetchone()[0]
    members = [dict(x) for x in conn.execute(
        "SELECT stock, weight FROM event_slot_members WHERE event_key='ND#1'")]
    slot = dict(conn.execute("SELECT budget, status FROM event_slots WHERE event_key='ND#1'"
                             ).fetchone())
    # v9 段即账户：等权份额=成员段 cash（资金 0 起步补足后的段现金）
    shares = {a['stock']: a['cash'] for a in
              (dict(x) for x in conn.execute("SELECT stock, cash FROM position "
                                             "WHERE status='open' AND strategy='NEWS'"))}
    conn.close()
    assert slots == 1                                   # 不加坑
    assert {m['stock'] for m in members} == {'甲票', '乙票', '丙票'}
    assert all(abs(m['weight'] - 1 / 3) < 1e-9 for m in members)   # 等权重算
    assert slot['budget'] == 400000                     # 300k + 100k
    # 等权补足语义：新成员补足到新份额；已有成员不回收（只补缺口）——
    # 权重列（1/3）是权威，账户现金 ≥ 份额
    assert abs(shares['丙票'] - 400000 / 3) < 1e-6
    assert shares['甲票'] >= 400000 / 3 and shares['乙票'] >= 400000 / 3
    assert abs(pools.show(pool='sleeve')['free'] - (2000000 - 300000 - 400000 / 3)) < 1


def test_g3_second_wave_after_closed_slot_new_key(pools, ws):
    """G3：键已关闭再遇同催化 → 开新槽（二波新键 #b2），永不并回旧键。"""
    op = _opener(ws)
    op.open_slot(['甲票'], budget=100000, event_key='ND#2', news_kind='policy')
    # 模拟关闭：直接置 archived
    conn = pools._conn()
    conn.execute("UPDATE event_slots SET status='archived' WHERE event_key='ND#2'")
    conn.commit(); conn.close()
    r2 = op.open_slot(['甲票'], budget=100000, event_key='ND#2', news_kind='policy',
                      code_map={'甲票': 'sh1'})
    assert r2['mode'] == 'open' and r2['derived_wave'] == 'ND#2#b2'
    conn = pools._conn()
    keys = [x[0] for x in conn.execute("SELECT event_key FROM event_slots ORDER BY event_key")]
    conn.close()
    assert 'ND#2' in keys and 'ND#2#b2' in keys


def test_g3_fail_open_missing_key(pools, ws):
    """fail-open：缺键 → 'auto:<首票>:<日期>' 兜底键 + 影子账#9。"""
    r = _opener(ws).open_slot(['兜底票'], budget=100000, news_kind='other', reason='无键')
    assert r['key_missing'] is True
    assert r['event_key'].startswith('auto:兜底票:')
    conn = pools._conn()
    n9 = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='event_key_missing'"
                      ).fetchone()[0]
    slot = conn.execute("SELECT COUNT(*) FROM event_slots WHERE event_key=?",
                        (r['event_key'],)).fetchone()[0]
    conn.close()
    assert n9 >= 1 and slot == 1


def test_migrate_fifo_cost_basis_and_ledger_conservation(pools, ws):
    """sleeve-migrate 段转策略（v4.2）：FIFO 行随迁不重买（禁迁移成本行）+ 双 ledger 守恒。"""
    op = _opener(ws)
    op.open_slot(['迁票'], budget=100000, event_key='ND#3', news_kind='sentiment',
                 code_map={'迁票': 'sh600100'})
    op.fill_pending(event_key='ND#3', open_prices={'迁票': 10.0}, skip_conditions=True)
    # 部分止盈卖 1/3 @ 13（模拟保护链出场）→ 剩余成本基准变化
    from paper_trading_v2.db import get_connection
    conn = get_connection(ws / 'master_pool.db')
    aid = conn.execute("SELECT id FROM position WHERE stock='迁票' AND status='open' "
                       "AND strategy='NEWS'").fetchone()[0]
    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM operations WHERE account_id=?",
                       (aid,)).fetchone()[0]
    conn.execute("INSERT INTO operations (account_id, seq, type, price, quantity, amount, cost,"
                 " profit, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (aid, seq, 'sell', 13.0, 3333, 43329.0, 33330.0, 9999.0,
                  '2026-09-01T10:00:00', 'TP1 模拟'))
    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM trades WHERE account_id=?",
                       (aid,)).fetchone()[0]
    conn.execute("INSERT INTO trades (account_id, seq, operation, quantity, price, "
                 "total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?)",
                 (aid, seq, 'sell', 3333, 13.0, 33330.0, '2026-09-01T10:00:00', 'TP1 模拟'))
    # 卖出所得入账（真实路径由 sell_stock 更新段现金；此处对账模拟）
    conn.execute("UPDATE position SET cash=cash+43329.0 WHERE id=?", (aid,))
    n_pos_before = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    n_ops_before = conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    conn.commit(); conn.close()

    from paper_trading_v2.sleeve_migrate import SleeveMigrator
    r = SleeveMigrator(ws / 'master_pool.db').migrate('迁票', reason='V11')
    assert r['qty'] == 6667 and abs(r['avg_cost'] - 10.0) < 1e-6   # FIFO 剩余成本
    conn = get_connection(ws / 'master_pool.db')
    aid_t = conn.execute("SELECT id FROM position WHERE stock='迁票' AND status='open'"
                         ).fetchone()[0]
    # 段转策略：NEWS 段原地转 L1（id 不动），budget=主池实际承接成本
    seg = dict(conn.execute("SELECT * FROM position WHERE stock='迁票' AND status='open'"
                            ).fetchone())
    # FIFO 行随迁（account_id 改挂 tech，不插新行）——迁移前后全库行数不变
    n_pos_after = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    n_ops_after = conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0]
    moved_buy = dict(conn.execute("SELECT * FROM trades WHERE account_id=? AND "
                                  "operation='buy'", (aid_t,)).fetchone())
    assert seg['id'] and seg['strategy'] == 'L1' and abs(seg['budget'] - 66670.0) < 1e-6
    assert n_pos_after == n_pos_before and n_ops_after == n_ops_before   # 零新增行
    assert moved_buy['quantity'] == 10000 and abs(moved_buy['price'] - 10.0) < 1e-6
    assert abs(moved_buy['total_cost'] - 100000.0) < 1e-6
    assert '段转ND#3' in (moved_buy['note'] or '')                       # 留痕
    # 技术组账户 FIFO 剩余=迁移持仓（成本基准连续，经名字寻址可查）
    from paper_trading_v2.sleeve_slots import account_remaining
    qty_left, cost_left = account_remaining(conn, aid_t)
    assert qty_left == 6667 and abs(cost_left - 66670.0) < 1e-6
    conn.close()
    # 资金守恒：主池 -66670，消息池 +（现金 43329 + 结转 66670）
    main_free = pools.show()['free']
    sleeve_free = pools.show(pool='sleeve')['free']
    assert abs(main_free - (10000000 - 66670)) < 1
    assert abs(sleeve_free - (2000000 - 100000 + 43329.0 + 66670.0)) < 1


def test_topup_locked_rejects_second_topup_after_migrate(pools, ws):
    """加仓锁：migrated 槽 → mpm.topup 直接拒绝（闸门 + shadow_log）。"""
    op = _opener(ws)
    op.open_slot(['锁票'], budget=100000, event_key='ND#4', news_kind='policy',
                 code_map={'锁票': 'sh600004'})
    op.fill_pending(event_key='ND#4', open_prices={'锁票': 10.0}, skip_conditions=True)
    from paper_trading_v2.sleeve_migrate import SleeveMigrator
    SleeveMigrator(ws / 'master_pool.db').migrate('锁票', reason='V11')
    with pytest.raises(GateViolation, match='加仓锁'):
        pools.topup('锁票', 50000, reason='二次加仓')
    conn = pools._conn()
    n = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='gate_violation' "
                     "AND key='锁票'").fetchone()[0]
    conn.close()
    assert n >= 1
