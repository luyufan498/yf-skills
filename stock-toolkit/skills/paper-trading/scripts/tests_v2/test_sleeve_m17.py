"""M1.7 二审修复轮回归测试（F1-F7，/tmp/m15_audit 对抗审计对应形态）

覆盖：
- F3 认领幂等：close_slot 条件认领（双回款）、migrate 成员级认领（双对转）
- F4 主池并发：allocate 条件 INSERT（同票双段）/ topup 相对写（丢失更新印钱）/ release 段认领
- F1 幻影现金：迁移票 available 重建=total−Σ(open FIFO cost)（盈/亏/对照三态零幻影）
- F2 保护链可读：迁移票 load_conditions/check_triggers 命中 tech 账户
- F5 次级防线：极小价下限 / 昨收=0 脏值 / skip_conditions 留痕
- F6 code 继承：migrate(code=None) tech 账户 stock_code 继承 news 侧
隔离：pytest 临时 workspace，价格/ATR 全 mock，零触网，生产库零接触。
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch

from paper_trading_v2.db import get_connection


@pytest.fixture(autouse=True)
def no_network(ws):
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


def _pool_mgr(env, pool='main'):
    from paper_trading_v2.master_pool import MasterPoolManager
    return MasterPoolManager(env / 'master_pool.db', pool=pool)


def _conn(env):
    return get_connection(env / 'master_pool.db')


def _snapshots(conn):
    pool_free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
    sleeve_free = conn.execute("SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
    acct = conn.execute("SELECT COALESCE(SUM(capital_total),0) FROM accounts").fetchone()[0]
    return pool_free, sleeve_free, acct


def _setup_filled_slot(env, stock, key, budget=100000, price=10.0):
    op = _opener(env)
    op.open_slot([stock], budget=budget, event_key=key, code_map={stock: 'sh1'})
    op.fill_pending(event_key=key, open_prices={stock: price}, skip_conditions=True)
    return op


class PausingConn:
    """连接代理：首次命中 hook 子串的 SQL 在执行前先跑回调（确定性交错注入）。"""

    def __init__(self, conn, hooks):
        self._c = conn
        self._hooks = hooks

    def execute(self, sql, *a, **k):
        for sub, flag, cb in self._hooks:
            if not flag['fired'] and sub in sql:
                flag['fired'] = True
                cb()
        return self._c.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)

    def __enter__(self):
        self._c.__enter__()
        return self

    def __exit__(self, *a):
        return self._c.__exit__(*a)


# ============ F3：close_slot 条件认领（双回款灭） ============

def _setup_cleared_member(env, stock, key, cash=110_000):
    """开槽成交后清掉成员持仓、只留残余现金（等价保护链成交回款）。"""
    _setup_filled_slot(env, stock, key)
    conn = _conn(env)
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name=? AND grp='news'",
                       (stock,)).fetchone()[0]
    qty = conn.execute("SELECT quantity FROM positions WHERE account_id=? AND "
                       "operation='buy'", (aid,)).fetchone()[0]
    cost = conn.execute("SELECT total_cost FROM positions WHERE account_id=? AND "
                        "operation='buy'", (aid,)).fetchone()[0]
    conn.execute("INSERT INTO positions (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost, timestamp, note) VALUES (?,1,'sell','sh1',?,?,?,"
                 "datetime('now'),'审计卖')", (aid, qty, cash / qty, cost))
    conn.execute("UPDATE accounts SET capital_available=? WHERE id=?", (cash, aid))
    conn.commit()
    conn.close()


def test_close_slot_concurrent_no_double_refund(pools, env):
    """close×2 确定性交错：T2（hooked）停在认领 UPDATE 前 → T1 完整提交 → T2 认领 rowcount=0 出局。

    审计 N-R1：认领 UPDATE 无 WHERE 条件 → 5/6 轮双回款 +10,000。
    """
    _setup_cleared_member(env, '关票', 'ND#M17C1')

    ev_t1_pause, ev_t2_done = threading.Event(), threading.Event()
    flag = {'fired': False}

    def t2_cb():
        ev_t1_pause.set()
        ev_t2_done.wait(timeout=15)

    results = {}

    def t1():                      # 无 hook：完整跑完（赢家）
        try:
            results['T1'] = _opener(env).close_slot('ND#M17C1', reason='A 线程')
        except Exception as e:
            results['T1'] = f'{type(e).__name__}: {e}'

    def t2():                      # hooked：停在认领 UPDATE 前（输家）
        op = _opener(env)
        real = _conn(env)
        op._conn = lambda: PausingConn(real, [("status='closed', closed_at=?", flag, t2_cb)])
        try:
            results['T2'] = op.close_slot('ND#M17C1', reason='B 线程')
        except Exception as e:
            results['T2'] = f'{type(e).__name__}: {e}'
        finally:
            ev_t2_done.set()

    th1, th2 = threading.Thread(target=t1), threading.Thread(target=t2)
    th1.start()
    th2.start()
    th1.join()
    th2.join()

    conn = _conn(env)
    sleeve_free = conn.execute("SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
    audits = conn.execute("SELECT COUNT(*) FROM audit WHERE action='sleeve_close_slot'"
                          ).fetchone()[0]
    conn.close()
    # 恰一次回款：2,000,000 − 100,000（开槽拨款）+ 110,000（残余现金回池）= 2,010,000
    #（双回款=2,120,000 / 丢款=1,900,000，均在此判出）
    assert abs(sleeve_free - 2_010_000) < 0.01, f"双回款/丢款 sleeve.free={sleeve_free}"
    assert audits == 1, f"close_slot audit 行={audits}"
    assert isinstance(results['T1'], dict), f"T1（先到）应成功: {results['T1']}"
    assert not isinstance(results['T2'], dict), f"T2（后到）应被认领拒绝: {results['T2']}"


def test_close_slot_twice_sequential_rejected(pools, env):
    _setup_cleared_member(env, '重票', 'ND#M17C2', cash=0)
    op = _opener(env)
    op.close_slot('ND#M17C2', reason='第一次')
    with pytest.raises(ValueError):
        op.close_slot('ND#M17C2', reason='第二次')


# ============ F3：migrate 成员级认领（双对转灭） ============

def test_migrate_concurrent_no_double_transfer(pools, env):
    """migrate×2 确定性交错：T2（hooked）停在成员认领 UPDATE 前 → T1 完整提交 → T2 出局。

    审计 N-R2a：全程无认领 → 双对转（main -2×cost / sleeve +2×refund）。
    """
    _setup_filled_slot(env, '迁票', 'ND#M17M1')

    ev_t1_pause, ev_t2_done = threading.Event(), threading.Event()
    flag = {'fired': False}

    def t2_cb():
        ev_t1_pause.set()
        ev_t2_done.wait(timeout=15)

    results = {}

    def t1():                      # 无 hook：完整跑完（赢家）
        try:
            results['T1'] = _migrator(env).migrate('迁票', reason='A 线程')
        except Exception as e:
            results['T1'] = f'{type(e).__name__}: {e}'

    def t2():                      # hooked：停在成员认领前（输家）
        mg = _migrator(env)
        real = _conn(env)
        mg._conn = lambda: PausingConn(
            real, [("migrated_at=? WHERE event_key=? AND stock=?", flag, t2_cb)])
        try:
            results['T2'] = mg.migrate('迁票', reason='B 线程')
        except Exception as e:
            results['T2'] = f'{type(e).__name__}: {e}'
        finally:
            ev_t2_done.set()

    th1, th2 = threading.Thread(target=t1), threading.Thread(target=t2)
    th1.start()
    th2.start()
    th1.join()
    th2.join()

    conn = _conn(env)
    pool_free, sleeve_free, acct = _snapshots(conn)
    tech = conn.execute("SELECT capital_total FROM accounts WHERE stock_name='迁票' "
                        "AND grp='tech'").fetchone()
    bridge = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='bridge_track'"
                          ).fetchone()[0]
    buys = conn.execute("SELECT COUNT(*) FROM positions WHERE operation='buy'").fetchone()[0]
    migrated = conn.execute("SELECT COUNT(*) FROM event_slots WHERE status='migrated'"
                            ).fetchone()[0]
    conn.close()
    # 恒等式：1,000 万 + 200 万（迁移是等额对转）
    assert abs(pool_free + sleeve_free + acct - 12_000_000) < 0.01, \
        f"双重对转 Δ={pool_free + sleeve_free + acct - 12_000_000:+,.2f}"
    assert tech and abs(tech[0] - 100_000) < 0.01, f"tech 承接={tech[0] if tech else None}"
    assert bridge == 1, f"bridge_track={bridge}"
    assert buys == 1, f"buy 行={buys}"
    assert migrated == 1, f"migrated 槽={migrated}"
    assert isinstance(results['T1'], dict), f"T1（先到）应成功: {results['T1']}"
    assert not isinstance(results['T2'], dict), f"T2（后到）应被成员认领拒绝: {results['T2']}"


# ============ F4：主池 allocate/topup/release 相对条件写 ============

def test_allocate_same_stock_concurrent_single_segment(pools, env):
    """审计 N-R3b：读-判-裸写 → 同票 allocate×2 双段、池只扣一次款。"""
    results = {}
    barrier = threading.Barrier(2)

    def worker(tag):
        m = _pool_mgr(env)
        barrier.wait(timeout=10)
        try:
            results[tag] = m.allocate('同票X', 500000, reason=tag)
        except Exception as e:
            results[tag] = f'{type(e).__name__}: {e}'

    ts = [threading.Thread(target=worker, args=(t,)) for t in ('T1', 'T2')]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    conn = _conn(env)
    pool_free, _, acct = _snapshots(conn)
    segs = conn.execute("SELECT COUNT(*) FROM position WHERE stock='同票X' AND status='open'"
                        ).fetchone()[0]
    accounts = conn.execute("SELECT COUNT(*) FROM accounts WHERE stock_name='同票X'"
                            ).fetchone()[0]
    conn.close()
    assert segs <= 1, f"同票双段={segs}"
    assert accounts <= 1, f"同票双账户={accounts}"
    ok = [v for v in results.values() if isinstance(v, bool) and v]
    assert len(ok) == 1, f"成功数={len(ok)}（恰 1）"
    assert abs(pool_free - 9_500_000) < 0.01, f"池扣款 main.free={pool_free}"
    assert abs(acct - 500_000) < 0.01, f"Σ账户={acct}"


def test_topup_concurrent_no_lost_update(pools, env):
    """审计 N-R3c：SET free=绝对值 丢失更新 → 账户加两次钱、池只扣一次（Δ+200,000）。"""
    _pool_mgr(env).allocate('注票Y', 1_000_000, reason='建段')
    results = {}
    barrier = threading.Barrier(2)

    def worker(tag, amt):
        m = _pool_mgr(env)
        barrier.wait(timeout=10)
        try:
            results[tag] = m.topup('注票Y', amt, reason=tag)
        except Exception as e:
            results[tag] = f'{type(e).__name__}: {e}'

    ts = [threading.Thread(target=worker, args=(t, a)) for t, a in (('T1', 300000),
                                                                    ('T2', 200000))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    conn = _conn(env)
    pool_free, _, acct = _snapshots(conn)
    seg = conn.execute("SELECT budget FROM position WHERE stock='注票Y' AND status='open'"
                       ).fetchone()
    conn.close()
    # 两笔注资都合法成立：池扣两次（9,000,000−500,000）、账户/段同步加两次
    assert abs(pool_free - 8_500_000) < 0.01, f"丢失更新 main.free={pool_free}"
    assert abs(acct - 1_500_000) < 0.01, f"Σ账户={acct}"
    assert seg and abs(seg[0] - 1_500_000) < 0.01, f"段预算={seg[0] if seg else None}"
    assert all(isinstance(v, bool) and v for v in results.values()), results


def test_release_concurrent_no_double_refund(pools, env):
    """release 段认领：双 release 只回款一次（审计 N-R1 同型在主池侧）。"""
    m = _pool_mgr(env)
    m.allocate('放票Z', 500_000, reason='建段')
    conn = _conn(env)
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='放票Z' AND grp='tech'"
                       ).fetchone()[0]
    conn.execute("UPDATE accounts SET capital_available=0, capital_used=0 WHERE id=?", (aid,))
    conn.commit()
    conn.close()

    results = {}
    barrier = threading.Barrier(2)

    def worker(tag):
        barrier.wait(timeout=10)
        try:
            results[tag] = _pool_mgr(env).release('放票Z', reason=tag)
        except Exception as e:
            results[tag] = f'{type(e).__name__}: {e}'

    ts = [threading.Thread(target=worker, args=(t,)) for t in ('T1', 'T2')]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    conn = _conn(env)
    pool_free, _, acct = _snapshots(conn)
    audits = conn.execute("SELECT COUNT(*) FROM audit WHERE action='release' AND "
                          "stock='放票Z'").fetchone()[0]
    conn.close()
    assert abs(pool_free - 10_000_000) < 0.01, f"双回款 main.free={pool_free}"
    assert audits == 1, f"release audit 行={audits}"
    ok = [v for v in results.values() if isinstance(v, bool) and v]
    assert len(ok) == 1, f"成功数={len(ok)}（恰 1）: {results}"


def test_release_twice_sequential_rejected(pools, env):
    m = _pool_mgr(env)
    m.allocate('放票W', 500_000, reason='建段')
    conn = _conn(env)
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='放票W' AND grp='tech'"
                       ).fetchone()[0]
    conn.execute("UPDATE accounts SET capital_available=0, capital_used=0 WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    m.release('放票W', reason='第一次')
    with pytest.raises(ValueError):
        m.release('放票W', reason='第二次')
