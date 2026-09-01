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


# ============ F1：幻影现金（迁移前已实现盈亏双算/双扣） ============

class _PI:
    """价格注入（密闭，零触网）。"""

    def __init__(self, p):
        self.current_price = p
        self.open_price = p
        self.pre_close = p


def _trader(env):
    from paper_trading_v2.trading import PaperTrader
    from paper_trading_v2.storage import SqlStorage
    return PaperTrader(storage=SqlStorage(env / 'master_pool.db'))


def _w_delta(env):
    """普适资金守恒：free(双池)+Σavail+ΣFIFO成本−Σ已实现盈亏 vs 常数 12,000,000。"""
    conn = _conn(env)
    pool_free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
    sleeve_free = conn.execute("SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
    avail = conn.execute("SELECT COALESCE(SUM(capital_available),0) FROM accounts").fetchone()[0]
    rpnl = 0.0
    for tbl in ('operations', 'operations_archive'):
        rpnl += conn.execute(
            f"SELECT COALESCE(SUM(COALESCE(amount,0)-COALESCE(cost,0)),0) FROM {tbl} "
            f"WHERE type='sell'").fetchone()[0] or 0.0
    fifo = 0.0
    from paper_trading_v2.sleeve_slots import account_remaining
    for (aid,) in conn.execute("SELECT DISTINCT account_id FROM positions").fetchall():
        fifo += account_remaining(conn, aid)[1]
    conn.close()
    return pool_free + sleeve_free + avail + fifo - rpnl - 12_000_000


@pytest.mark.parametrize('sell_price,expected_pnl', [(12.0, 40_000), (8.0, -40_000)])
def test_migrated_account_no_phantom_cash(pools, env, sell_price, expected_pnl):
    """phantom.py 三态：迁移前部分卖出（盈/亏）→ migrate → get_account available 必须=0。

    审计实测：随迁 operations 进重建公式 → 盈利双算 +40,000 / 亏损双扣 −40,000，
    传导 release close_value 污染主池（W_delta ±40,000）。
    """
    op = _opener(env)
    op.open_slot(['幻票'], budget=500_000, event_key='ND#PH', code_map={'幻票': 'sh1'})
    op.fill_pending(event_key='ND#PH', open_prices={'幻票': 10.0}, skip_conditions=True)
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(sell_price)
        _trader(env).sell_stock('幻票', quantity=20_000, note='TP1')
    _migrator(env).migrate('幻票', reason='V11')

    acct = _trader(env).get_account('幻票')
    assert acct.grp == 'tech'
    assert abs(acct.capital_pool.available) < 0.01, \
        f"幻影现金 {acct.capital_pool.available:+,.0f}（应=0；迁移前盈亏 {expected_pnl:+,.0f} 被双算）"
    assert abs(_w_delta(env)) < 0.01, f"W_delta={_w_delta(env):+,.2f}"


def test_migrated_account_available_tracks_post_migration_trading(pools, env):
    """迁移前有已实现盈亏 + 迁移后继续交易：available 恒 = total − Σ(open FIFO cost)
    + Σ(迁移后已实现盈亏)；旧全史公式会多出"迁移前盈亏"一项（幻影）。"""
    op = _opener(env)
    op.open_slot(['续票'], budget=400_000, event_key='ND#PS', code_map={'续票': 'sh1'})
    op.fill_pending(event_key='ND#PS', open_prices={'续票': 10.0}, atr={'续票': 0.5})
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(12.0)
        _trader(env).sell_stock('续票', quantity=13_333, note='TP1 迁移前部分卖出')
    _migrator(env).migrate('续票', reason='V11', code='sh1')
    _pool_mgr(env).topup('续票', 200_000, reason='承接注资', source='migrate')

    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(15.0)
        trader = _trader(env)
        trader.buy_stock('续票', amount=150_000, note='承接后加买')
        trader.sell_stock('续票', quantity=5_000, note='承接后减仓')

    acct = _trader(env).get_account('续票')
    conn = _conn(env)
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='续票' AND grp='tech'"
                       ).fetchone()[0]
    from paper_trading_v2.sleeve_slots import account_remaining
    qty, fifo_cost = account_remaining(conn, aid)
    rpnl = conn.execute("SELECT COALESCE(SUM(COALESCE(amount,0)-COALESCE(cost,0)),0) "
                        "FROM operations WHERE account_id=? AND type='sell' AND note "
                        "NOT LIKE '%段转%'", (aid,)).fetchone()[0] or 0.0
    conn.close()
    expected = acct.capital_pool.total - fifo_cost + rpnl
    assert qty > 0
    assert abs(acct.capital_pool.available - expected) < 1.0, \
        f"available={acct.capital_pool.available:,.2f} 应=total−FIFO成本+已实现盈亏={expected:,.2f}"
    assert abs(_w_delta(env)) < 0.01, f"W_delta={_w_delta(env):+,.2f}"


# ============ F2：保护链只写不可读（conditions_manager 裸名字寻址） ============

def _cm(env):
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.storage import SqlStorage
    return ConditionsManager(storage=SqlStorage(env / 'master_pool.db'))


def test_migrated_ticket_conditions_readable_and_triggerable(pools, env):
    """迁移票挂线→改价破位→check_triggers 能触发（审计站6c/6e：load_conditions 裸名字
    寻址命中 news 历史壳 → 迁移票保护链只写不可读、check_triggers 破位返空）。"""
    op = _opener(env)
    op.open_slot(['护票'], budget=400_000, event_key='ND#PC', code_map={'护票': 'sh1'})
    op.fill_pending(event_key='ND#PC', open_prices={'护票': 10.0}, atr={'护票': 0.5})
    _migrator(env).migrate('护票', reason='V11', code='sh1')

    rec = _cm(env).load_conditions('护票')
    assert rec is not None and rec.conditions, \
        "load_conditions 裸名字寻址命中 news 历史壳 → 迁移票保护链读不到"
    assert {'cost_protection', 'trailing_stop'} <= set(rec.conditions)
    assert abs(rec.conditions['cost_protection'].price - 9.0) < 0.01

    breaches = _cm(env).check_triggers('护票', current_price=8.5)
    assert breaches, "现价 8.5 已破 9.0 保护线但 check_triggers 返回空（感知断链）"

    # save 路径：改价必须写回 tech 账户，不得写进/清空 news 壳
    rec.conditions['cost_protection'].price = 8.8
    _cm(env).save_conditions(rec)
    conn = _conn(env)
    aid_tech = conn.execute("SELECT id FROM accounts WHERE stock_name='护票' AND grp='tech'"
                            ).fetchone()[0]
    aid_news = conn.execute("SELECT id FROM accounts WHERE stock_name='护票' AND grp='news'"
                            ).fetchone()[0]
    tech_cp = conn.execute("SELECT price FROM conditions WHERE account_id=? AND "
                           "type='cost_protection'", (aid_tech,)).fetchone()
    news_conds = conn.execute("SELECT COUNT(*) FROM conditions WHERE account_id=?",
                              (aid_news,)).fetchone()[0]
    conn.close()
    assert tech_cp and abs(tech_cp[0] - 8.8) < 0.01, f"改价未写回 tech: {tech_cp}"
    assert news_conds == 0, f"news 壳被误写 {news_conds} 条条件"


def test_buy_after_migrate_reanchors_protection_on_tech(pools, env):
    """buy 后重锚写对账户（审计站6d：_sync_conditions_after_buy 被 news 壳吞掉，
    cost_protection 锚死在迁移前原成本 9.0）。"""
    op = _opener(env)
    op.open_slot(['锚票'], budget=400_000, event_key='ND#PA', code_map={'锚票': 'sh1'})
    op.fill_pending(event_key='ND#PA', open_prices={'锚票': 10.0}, atr={'锚票': 0.5})
    _migrator(env).migrate('锚票', reason='V11', code='sh1')
    _pool_mgr(env).topup('锚票', 200_000, reason='承接注资', source='migrate')

    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(15.0)
        _trader(env).buy_stock('锚票', amount=150_000, note='承接后加买')

    conn = _conn(env)
    aid_tech = conn.execute("SELECT id FROM accounts WHERE stock_name='锚票' AND grp='tech'"
                            ).fetchone()[0]
    from paper_trading_v2.sleeve_slots import account_remaining
    qty, fifo_cost = account_remaining(conn, aid_tech)
    news_conds = conn.execute(
        "SELECT COUNT(*) FROM conditions WHERE account_id=(SELECT id FROM accounts "
        "WHERE stock_name='锚票' AND grp='news')").fetchone()[0]
    tech_cp = conn.execute("SELECT price FROM conditions WHERE account_id=? AND "
                           "type='cost_protection'", (aid_tech,)).fetchone()
    conn.close()
    avg_cost = fifo_cost / qty
    expected = round(avg_cost * (1 - 0.03), 2)       # 刚买入=建仓缓冲期（BUILD_BUFFER=3%）
    assert avg_cost > 10.0, f"加买后加权成本={avg_cost}（应含 15 元新买档）"
    assert tech_cp and abs(tech_cp[0] - expected) < 0.01, \
        f"加买后保本锁未重锚：{tech_cp[0] if tech_cp else None}（应={expected}，锚死 9.0=原成本）"
    assert news_conds == 0
