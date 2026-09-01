"""M1.5 修复轮回归测试（sleeve-m1.5，方案第六部分 R1-R8 + 验收门）

覆盖：竞态×3（test_08 对应）、段转策略桥端到端（A3 正主）、release 显式路由（同票双组）、
merge 禁入迁移槽、段不变量（预算恒=账户实际占用）、R7 fill 防线、R3 崩溃恢复、R6 闸门补齐。
隔离：全部在 pytest 临时 workspace（STOCK_ANALYSIS_WORKSPACE），价格/ATR 全 mock，零触网。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import sqlite3
import threading
import pytest
from unittest.mock import patch

from paper_trading_v2.gate import GateViolation


@pytest.fixture(autouse=True)
def no_network(ws):
    """密闭：价格抓取全 mock（R7 价差防线以昨收为参照，注入价不触网）。"""
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=None):
        yield


@pytest.fixture
def env(ws, monkeypatch):
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', str(ws))
    return ws


@pytest.fixture
def pools(env):
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(env / 'master_pool.db')
    m.init_pool(10000000)
    m.init_pool(2000000, pool='sleeve')
    return m


def _opener(env):
    from paper_trading_v2.sleeve_open import SleeveOpener
    return SleeveOpener(env / 'master_pool.db')


def _migrator(env):
    from paper_trading_v2.sleeve_migrate import SleeveMigrator
    return SleeveMigrator(env / 'master_pool.db')


def _conn(env):
    from paper_trading_v2.db import get_connection
    return get_connection(env / 'master_pool.db')


def _identity(conn):
    """系统资金恒等式：pool_ledger.free + sleeve_ledger.free + Σaccounts.capital_total。"""
    pool_free = conn.execute("SELECT free FROM pool_ledger").fetchone()[0]
    sleeve_free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    acct = conn.execute("SELECT COALESCE(SUM(capital_total),0) FROM accounts").fetchone()[0]
    return pool_free + sleeve_free + acct, pool_free, sleeve_free, acct


# ============ R2：TOCTOU 竞态×3（test_08/test_07 对应形态，tests_v2 侧锁定）============

def test_race_concurrent_cancel_no_double_refund(pools, env):
    op = _opener(env)
    op.open_slot(['竞弃票'], budget=100000, event_key='ND#R15A', code_map={'竞弃票': 'sh1'})
    errs = []

    def worker():
        try:
            op.cancel_pending('ND#R15A', reason='竞态')
        except ValueError:
            pass

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    conn = _conn(env)
    free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    drops = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='drop_order' "
                         "AND key='ND#R15A'").fetchone()[0]
    conn.close()
    assert free <= 2000000 + 0.01, f"双回款 free={free}"
    assert drops == 1


def test_race_concurrent_fill_no_double_buy(pools, env):
    op = _opener(env)
    op.open_slot(['竞成票'], budget=100000, event_key='ND#R15B', code_map={'竞成票': 'sh1'})
    errs = []

    def worker():
        try:
            op.fill_pending(event_key='ND#R15B', open_prices={'竞成票': 10.0},
                            skip_conditions=True)
        except Exception as e:
            errs.append(repr(e))

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    conn = _conn(env)
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='竞成票'").fetchone()[0]
    buys = conn.execute("SELECT COUNT(*) FROM positions WHERE account_id=? AND "
                        "operation='buy'", (aid,)).fetchone()[0]
    conn.close()
    assert buys <= 1, f"双买入 {buys} 笔"


def test_race_concurrent_open_distinct_keys_never_exceed_20(pools, env):
    op = _opener(env)
    errors = []

    def worker(i):
        try:
            op.open_slot([f'竞槽{i}'], budget=1000, event_key=f'ND#Q{i:02d}',
                         code_map={f'竞槽{i}': f'sh{i:04d}'})
        except ValueError as e:
            if '事件坑已满' not in str(e):
                errors.append(str(e))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    conn = _conn(env)
    n = conn.execute("SELECT COUNT(*) FROM event_slots WHERE status IN ('open','partial')"
                     ).fetchone()[0]
    conn.close()
    assert n <= 20, f"并发超发 {n} 槽"


def test_sleeve_ledger_negative_free_rejected(pools, env):
    """R2/F2：sleeve_ledger CHECK(free>=0)——超扣在写入瞬间崩，不静默污染。"""
    conn = _conn(env)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE sleeve_ledger SET free=-1 WHERE id=1")
    conn.close()


# ============ R1：段转策略桥端到端（A3 正主：迁移票卖出全链走通）============

def test_segment_transfer_bridge_e2e_sell_topup_release(pools, env, monkeypatch):
    """open→fill→migrate→topup(--source migrate 豁免/资金帽不豁免)→sell→release，
    资金恒等式逐站打印锁定（验收门 3 的 pytest 形态）。"""
    op, mg = _opener(env), _migrator(env)
    op.open_slot(['桥票'], budget=100000, event_key='ND#B15', code_map={'桥票': 'sh1'})
    op.fill_pending(event_key='ND#B15', open_prices={'桥票': 10.0}, skip_conditions=True)

    conn = _conn(env)
    inv0 = _identity(conn)[0]
    conn.close()

    r = mg.migrate('桥票', reason='V11', code='sh1')
    assert r['qty'] == 10000 and abs(r['avg_cost'] - 10.0) < 1e-6
    conn = _conn(env)
    # 段转：NEWS 段原地转 L1（id 不动）
    seg = dict(conn.execute("SELECT * FROM position WHERE stock='桥票' AND status='open'"
                            ).fetchone())
    assert seg['strategy'] == 'L1' and abs(seg['budget'] - 100000) < 1e-6
    slot = dict(conn.execute("SELECT * FROM event_slots WHERE event_key='ND#B15'").fetchone())
    assert slot['status'] == 'migrated' and slot['topup_locked'] == 1
    assert slot['fill_status'] == 'filled'          # 不回 pending
    news_acct = conn.execute("SELECT capital_total FROM accounts WHERE stock_name='桥票' "
                             "AND grp='news'").fetchone()[0]
    assert news_acct == 0                            # 历史壳清零
    inv1, pf1, sf1, _ = _identity(conn)
    assert abs(pf1 - (10000000 - 100000)) < 0.01
    assert abs(sf1 - 2000000) < 0.01
    conn.close()
    assert abs(inv1 - inv0) < 0.01                   # 守恒

    # 承接注资：--source migrate 豁免加仓锁（甜点动量检查豁免的机械面）
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager(env / 'master_pool.db')
    mpm.topup('桥票', 30000, reason='承接注资', source='migrate')
    conn = _conn(env)
    seg2 = dict(conn.execute("SELECT * FROM position WHERE stock='桥票' AND status='open'"
                             ).fetchone())
    inv2, _, _, _ = _identity(conn)
    conn.close()
    assert abs(seg2['budget'] - 130000) < 0.01
    assert abs(inv2 - inv0) < 0.01
    # 资金帽不豁免：累计 130000 + 2,900,000 > 30%×1000 万（300 万）→ 拒绝
    with pytest.raises(ValueError, match='30%'):
        mpm.topup('桥票', 2900000, reason='超帽', source='migrate')
    # 无 --source migrate → 加仓锁拒绝（默认 source=agent）
    with pytest.raises(GateViolation, match='加仓锁'):
        mpm.topup('桥票', 1000, reason='默认 source')

    # A3 正主：迁移票卖出全链走通（名字寻址段锚定命中 tech 持仓账户）
    from paper_trading_v2.trading import PaperTrader
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import StockInfo
    trader = PaperTrader(storage=SqlStorage(env / 'master_pool.db'))
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = StockInfo(code='sh1', name='桥票', current_price=12.0,
                                    pre_close=11.5)
        acct = trader.sell_stock('桥票', sell_all=True, note='保护链触发')
    assert acct.capital_pool.available == pytest.approx(150000, abs=1)   # 10000股@12 + 承接注资余 30000
    conn = _conn(env)
    aid_t = conn.execute("SELECT id FROM accounts WHERE stock_name='桥票' AND grp='tech'"
                         ).fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM positions WHERE account_id=? AND "
                        "operation='buy'", (aid_t,)).fetchone()[0] == 1
    # sell 清仓 → 自动 release 链已触发（R5 显式路由 grp='tech' → 主池 release）
    seg = conn.execute("SELECT status, close_value, realized_pnl FROM position WHERE "
                       "stock='桥票' AND status='closed'").fetchone()
    pool_row = dict(conn.execute("SELECT pool_status, strategy, event_key FROM pool "
                                 "WHERE stock='桥票'").fetchone())
    slot2 = dict(conn.execute("SELECT status FROM event_slots WHERE event_key='ND#B15'"
                              ).fetchone())
    inv5, pf5, sf5, acct5 = _identity(conn)
    conn.close()
    assert seg['close_value'] == pytest.approx(150000, abs=1)   # 卖出所得回主池
    assert seg['realized_pnl'] == pytest.approx(20000, abs=1)   # (12-10)×10000（段预算=承接后 13 万）
    assert pool_row['pool_status'] == 'archived' and pool_row['strategy'] == 'L1'
    assert pool_row['event_key'] == 'ND#B15'         # 键活槽空（键保留）
    assert slot2['status'] == 'migrated'             # 不复活 NEWS 旧槽
    assert abs(pf5 - (10000000 - 100000 - 30000 + 150000)) < 0.01
    assert abs(sf5 - 2000000) < 0.01 and acct5 == 0
    assert inv5 == pytest.approx(12000000 + 20000, abs=0.01)    # +2 万已实现盈亏进池


def test_migrated_ticket_name_addressing_resolves_tech(pools, env):
    """A3/Y3/D9 反转锁定：迁移后名字寻址命中 tech 持仓账户（不再命中 news 历史壳）。"""
    op, mg = _opener(env), _migrator(env)
    op.open_slot(['后门票'], budget=100000, event_key='ND#N15', code_map={'后门票': 'sh1'})
    op.fill_pending(event_key='ND#N15', open_prices={'后门票': 10.0}, skip_conditions=True)
    mg.migrate('后门票', reason='V11')
    from paper_trading_v2.storage import SqlStorage
    acct = SqlStorage(env / 'master_pool.db').load_account('后门票')
    assert acct.grp == 'tech'
    assert acct.capital_pool.total == pytest.approx(100000, abs=0.01)
    qty, cost = 0, 0.0
    from paper_trading_v2.sleeve_slots import account_remaining
    conn = _conn(env)
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='后门票' AND grp='tech'"
                       ).fetchone()[0]
    qty, cost = account_remaining(conn, aid)
    conn.close()
    assert qty == 10000 and cost == pytest.approx(100000, abs=0.01)


# ============ R5：清仓路由（同票双组场景）============

def test_release_route_dual_group_each_side_settles_own(pools, env):
    """同票双组（迁移持仓 + 二波新 NEWS 槽）：news 侧清仓回消息池、tech 侧清仓回主池。"""
    op, mg = _opener(env), _migrator(env)
    op.open_slot(['双组票'], budget=100000, event_key='ND#D15', code_map={'双组票': 'sh1'})
    op.fill_pending(event_key='ND#D15', open_prices={'双组票': 10.0}, skip_conditions=True)
    mg.migrate('双组票', reason='V11', code='sh1')
    # 二波新槽（迁移票不重复建仓的是已迁移持仓；二波=新事件新槽）
    op.open_slot(['双组票'], budget=50000, event_key='ND#D15B', code_map={'双组票': 'sh1'})
    op.fill_pending(event_key='ND#D15B', open_prices={'双组票': 20.0}, skip_conditions=True)
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    # 模拟 news 侧已清仓（真实链路由 sell_stock 完成；此处仅造"账户空仓+现金"态验路由）
    conn = _conn(env)
    aid_n = conn.execute("SELECT id FROM accounts WHERE stock_name='双组票' AND grp='news'"
                         ).fetchone()[0]
    conn.execute("DELETE FROM positions WHERE account_id=?", (aid_n,))
    conn.execute("UPDATE accounts SET capital_available=52000, capital_used=0 WHERE id=?",
                 (aid_n,))
    conn.commit(); conn.close()
    trader._auto_release_on_clear('双组票', grp='news')      # grp 锁定 news 路由
    conn = _conn(env)
    news_seg = conn.execute("SELECT status FROM position WHERE stock='双组票' AND "
                            "strategy='NEWS'").fetchone()[0]
    sleeve_free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    main_free = conn.execute("SELECT free FROM pool_ledger").fetchone()[0]
    conn.close()
    assert news_seg == 'closed'
    assert abs(sleeve_free - (2000000 - 50000 + 52000)) < 1   # news 侧现金回消息池
    assert abs(main_free - (10000000 - 100000)) < 1   # 主池未动（迁移持仓仍占）
    # tech 侧（迁移持仓）清仓：真实 sell 链（名字寻址段锚定→tech）→ 自动 release 路由
    from paper_trading_v2.models import StockInfo
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = StockInfo(code='sh1', name='双组票', current_price=12.0,
                                    pre_close=11.5)
        trader.sell_stock('双组票', sell_all=True, note='保护链触发')   # 命中 tech 账户
    conn = _conn(env)
    tech_seg = conn.execute("SELECT status FROM position WHERE stock='双组票' AND "
                            "strategy='L1'").fetchone()[0]
    main_free2 = conn.execute("SELECT free FROM pool_ledger").fetchone()[0]
    conn.close()
    assert tech_seg == 'closed'
    assert abs(main_free2 - (10000000 - 100000 + 120000)) < 1   # 迁移持仓清仓款回主池
    # 同票双组下池行 event_key 被二波新槽接管（_upsert_pool_row COALESCE 语义）——
    # 事件仍活跃（二波槽 open）→ 清仓走技术组 flag 语义（flag 关=降 L2），不档案化：
    conn = _conn(env)
    prow = dict(conn.execute("SELECT strategy, pool_status, event_key FROM pool "
                             "WHERE stock='双组票'").fetchone())
    conn.close()
    assert prow['event_key'] == 'ND#D15B' and prow['pool_status'] == 'active'


def test_release_route_no_segment_noop(pools, env):
    """无 open 段 → no-op（不抛异常、不误触 release）。"""
    from paper_trading_v2.trading import PaperTrader
    PaperTrader()._auto_release_on_clear('无段票')      # 不应抛
    conn = _conn(env)
    n = conn.execute("SELECT COUNT(*) FROM audit WHERE stock='无段票'").fetchone()[0]
    conn.close()
    assert n == 0


# ============ R4：merge 禁入迁移槽 / 纯归并 ============

def test_merge_forbidden_on_slot_with_migrated_member(pools, env):
    """含 migrated_at 成员的槽禁 merge——G3 强制开新槽（派生 #bN）。"""
    op, mg = _opener(env), _migrator(env)
    op.open_slot(['迁A'], budget=100000, event_key='ND#M15', code_map={'迁A': 'sh1'})
    op.fill_pending(event_key='ND#M15', open_prices={'迁A': 10.0}, skip_conditions=True)
    mg.migrate('迁A', reason='V11')
    conn = _conn(env)
    conn.execute("UPDATE event_slots SET status='partial' WHERE event_key='ND#M15'")
    conn.commit(); conn.close()
    r = op.open_slot(['新波票'], budget=50000, event_key='ND#M15',
                     code_map={'新波票': 'sh2'})
    assert r['mode'] == 'open' and r['derived_wave'] == 'ND#M15#b2'   # 强制开新槽
    conn = _conn(env)
    old = dict(conn.execute("SELECT status FROM event_slots WHERE event_key='ND#M15'"
                            ).fetchone())
    new = dict(conn.execute("SELECT status FROM event_slots WHERE event_key='ND#M15#b2'"
                            ).fetchone())
    conn.close()
    assert old['status'] == 'partial' and new['status'] == 'open'


def test_merge_zero_budget_pure_merge_no_funding(pools, env):
    """R4：merge budget=0 合法（纯归并）——零拨款、新成员 0 起步、既有成员不动。"""
    op = _opener(env)
    op.open_slot(['合A'], budget=100000, event_key='ND#Z15', code_map={'合A': 'sh1'})
    r = op.open_slot(['合B'], budget=0, event_key='ND#Z15', code_map={'合B': 'sh2'})
    assert r['mode'] == 'merge'
    conn = _conn(env)
    acct_b = conn.execute("SELECT capital_total FROM accounts WHERE stock_name='合B' "
                          "AND grp='news'").fetchone()[0]
    seg_b = conn.execute("SELECT budget FROM position WHERE stock='合B' AND status='open'"
                         ).fetchone()[0]
    free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    conn.close()
    assert acct_b == 0.0 and seg_b == 0.0            # 0 元起步，预算=实际占用（不超分）
    assert abs(free - (2000000 - 100000)) < 0.01     # 零拨款


# ============ 段不变量（验收门 4）：sleeve 段预算恒=账户实际占用（不超分）============

def test_segment_invariant_budget_equals_account_allocation(pools, env):
    """全流程（open/等权 merge/纯归并/fill）后：每个 open NEWS 段 budget == 成员账户
    capital_total；sleeve_ledger.free + Σ open NEWS 段预算 == 消息池 total（不超分，
    验收门 4 从机制保证变为可测断言）。槽 budget 口径=累计拨款（M1 偏差 D8：merge
    等权重算后段预算和≠槽 budget，槽列为事件拨款账，不变量锚定在段↔账户）。"""
    op = _opener(env)
    op.open_slot(['变A', '变B'], budget=300000, event_key='ND#I15',
                 code_map={'变A': 'sh1', '变B': 'sh2'})
    op.open_slot(['变C'], budget=60000, event_key='ND#I15', code_map={'变C': 'sh3'})  # merge
    op.open_slot(['变D'], budget=0, event_key='ND#I15', code_map={'变D': 'sh4'})      # 纯归并
    op.fill_pending(event_key='ND#I15', open_prices={'变A': 10.0, '变B': 20.0},
                    skip_conditions=True)
    conn = _conn(env)
    segs = conn.execute("SELECT stock, budget FROM position WHERE status='open' "
                        "AND strategy='NEWS'").fetchall()
    sleeve_free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    conn.close()
    seg_sum = 0.0
    for r in segs:
        c = _conn(env)
        acct = c.execute("SELECT capital_total FROM accounts WHERE stock_name=? AND "
                         "grp='news'", (r['stock'],)).fetchone()
        c.close()
        assert acct is not None
        assert abs(acct[0] - r['budget']) < 0.01, \
            f"{r['stock']} 段预算 {r['budget']} ≠ 账户实际 {acct[0]}（超分）"
        seg_sum += r['budget']
    assert seg_sum > 0
    # 不超分：消息池 free + 全部在槽段预算 == 消息池 total（零盈亏态）
    assert abs(sleeve_free + seg_sum - 2000000) < 0.01


# ============ R7：fill 防线 ============

def test_fill_blocked_when_price_deviates_prev_close(pools, env):
    """R7：开盘价偏离昨收>30% → 拒绝成交 + shadow_log(fill_blocked)。"""
    op = _opener(env)
    op.open_slot(['偏票'], budget=100000, event_key='ND#V15', code_map={'偏票': 'sh1'})
    r = op.fill_pending(event_key='ND#V15', open_prices={'偏票': 20.0},
                        prev_close_map={'偏票': 10.0}, skip_conditions=True)
    assert r[0]['filled'] == []
    assert any('偏离昨收' in w for _, w in r[0]['skipped'])
    conn = _conn(env)
    slot = dict(conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#V15'"
                             ).fetchone())
    n = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='fill_blocked' "
                     "AND key='ND#V15'").fetchone()[0]
    conn.close()
    assert slot['fill_status'] == 'pending'
    assert n >= 1


def test_fill_blocked_when_atr_unresolvable(pools, env):
    """R7/H1/C1：ATR 解析失败 → 拒绝成交（禁静默裸奔）+ fill_blocked 留痕，槽保持 pending。"""
    op = _opener(env)
    op.open_slot(['裸奔票'], budget=100000, event_key='ND#A15', code_map={'裸奔票': 'sh1'})
    with patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
               side_effect=RuntimeError('网络断')):
        r = op.fill_pending(event_key='ND#A15', open_prices={'裸奔票': 10.0})   # 不传 atr
    assert r[0]['filled'] == []
    assert any('ATR' in w for _, w in r[0]['skipped'])
    conn = _conn(env)
    n_pos = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    n_blk = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='fill_blocked' "
                         "AND key='ND#A15'").fetchone()[0]
    slot = dict(conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#A15'"
                             ).fetchone())
    conn.close()
    assert n_pos == 0                                 # 无成交（不裸奔）
    assert n_blk >= 1                                 # 留痕
    assert slot['fill_status'] == 'pending'           # 下轮重试


def test_fill_price_zero_or_negative_rejected(pools, env):
    op = _opener(env)
    op.open_slot(['脏票'], budget=100000, event_key='ND#P15', code_map={'脏票': 'sh1'})
    r0 = op.fill_pending(event_key='ND#P15', open_prices={'脏票': 0}, skip_conditions=True)
    r1 = op.fill_pending(event_key='ND#P15', open_prices={'脏票': -5.0}, skip_conditions=True)
    assert r0[0]['filled'] == [] and r1[0]['filled'] == []
    conn = _conn(env)
    n = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    conn.close()
    assert n == 0


# ============ R3：v8 安全迁移重建（崩溃恢复）============

def test_v8_sleeve_ledger_rebuild_crash_recovery(ws):
    """R3/F1：灌完新表后进程死（*_old 残留+版本未升）→ 重跑走恢复分支，数据零丢失。"""
    from paper_trading_v2.db import (get_connection, migrate_db, V7_DDL,
                                     SLEEVE_LEDGER_V8_DDL, _table_exists)
    db = ws / 'master_pool.db'
    conn = get_connection(db)
    conn.executescript("CREATE TABLE schema_meta (version INTEGER NOT NULL, migrated_at TEXT);"
                       "INSERT INTO schema_meta VALUES (7,'x');")
    conn.executescript(V7_DDL)
    conn.execute("INSERT INTO sleeve_ledger (id, total, free, updated_at) "
                 "VALUES (1, 2000000, 1900000, 'x')")
    conn.commit()
    # 重放重建前三步并逐句落盘（模拟崩溃点）
    conn.execute("ALTER TABLE sleeve_ledger RENAME TO sleeve_ledger_old")
    conn.execute(SLEEVE_LEDGER_V8_DDL)
    conn.execute("INSERT INTO sleeve_ledger SELECT id, total, free, updated_at "
                 "FROM sleeve_ledger_old")
    conn.commit()
    conn.close()                                       # ← 进程死
    conn = get_connection(db)
    migrate_db(conn)                                   # 重跑 → 恢复分支
    row = conn.execute("SELECT * FROM sleeve_ledger").fetchone()
    version = conn.execute("SELECT version FROM schema_meta").fetchone()[0]
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='sleeve_ledger'").fetchone()[0]
    residue = _table_exists(conn, 'sleeve_ledger_old')
    conn.close()
    assert version == 8
    assert tuple(row) == (1, 2000000.0, 1900000.0, 'x')   # 数据零丢失
    assert 'CHECK' in ddl and residue is False


# ============ R6：闸门补齐 ============

def test_gate_buy_blocked_on_news_pool_row_even_without_account(pools, env):
    """R6/Y5：NEWS 档票无账户（pending 未成交）→ buy 拒绝 + shadow_log 留痕。"""
    op = _opener(env)
    op.open_slot(['待开票'], budget=100000, event_key='ND#G15', code_map={'待开票': 'sh1'})
    conn = _conn(env)
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE stock_name='待开票' AND "
                        "grp='news'").fetchone()[0] >= 1
    # 制造"无账户"态（先清子表满足 FK）
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='待开票'").fetchone()[0]
    conn.execute("DELETE FROM condition_history WHERE condition_id IN "
                 "(SELECT id FROM conditions WHERE account_id=?)", (aid,))
    for t in ('conditions', 'exright_applied', 'operations', 'positions'):
        conn.execute(f"DELETE FROM {t} WHERE account_id=?", (aid,))
    conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
    conn.commit()
    n0 = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='gate_violation' "
                      "AND key='待开票'").fetchone()[0]
    conn.close()
    from paper_trading_v2.gate import enforce
    with pytest.raises(GateViolation, match='NEWS 档'):
        enforce('待开票', 'buy')
    conn = _conn(env)
    n1 = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='gate_violation' "
                      "AND key='待开票'").fetchone()[0]
    conn.close()
    assert n1 == n0 + 1


def test_allocate_warns_on_active_news_side(pools, env, capsys):
    """R6/Y4：技术组 allocate 对侧检查——活跃 news 槽（迁移后 partial 槽）→ 告警 + 既有段拒绝。"""
    op, mg = _opener(env), _migrator(env)
    op.open_slot(['双侧票', '同槽票'], budget=200000, event_key='ND#W15',
                 code_map={'双侧票': 'sh1', '同槽票': 'sh2'})
    op.fill_pending(event_key='ND#W15',
                    open_prices={'双侧票': 10.0, '同槽票': 10.0}, skip_conditions=True)
    mg.migrate('双侧票', reason='V11')                # 槽转 partial（同槽票仍 NEWS 持仓）
    from paper_trading_v2.master_pool import MasterPoolManager
    mpm = MasterPoolManager(env / 'master_pool.db')
    with pytest.raises(ValueError, match='已有 open 段'):
        mpm.allocate('双侧票', 100000, reason='对侧检查验证')
    out = capsys.readouterr().out
    assert '同票双组暴露' in out and '活跃槽' in out     # 对侧告警已打印


# ============ 审计套件遗留形态的守卫（4 红转绿后不再复发的最小锁定）============

def test_cancel_twice_single_refund(pools, env):
    op = _opener(env)
    op.open_slot(['复弃票'], budget=123456.78, event_key='ND#C15', code_map={'复弃票': 'sh1'})
    r1 = op.cancel_pending('ND#C15', reason='第一次')
    with pytest.raises(ValueError, match='非 pending'):
        op.cancel_pending('ND#C15', reason='第二次')
    conn = _conn(env)
    free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    conn.close()
    assert r1['refund'] == pytest.approx(123456.78, abs=0.01)
    assert free == pytest.approx(2000000, abs=0.01)
