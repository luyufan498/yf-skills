"""M1.6 账户层退役回归测试（U5 新增不变量 + U7 卫生项）

- 段现金恒等式：cash + FIFO成本 − realized == budget（段即账户后的核心不变量）
- trades 归属完整性：无孤儿流水（account_id 恒指向存活段）
- 同票双段并存（L1+NEWS）：sell/topup/conditions 寻址零歧义
- v9 迁移（合成 v8 库）：段现金逐分对账、资金恒等式前后相等、accounts_old 保留、
  兼容视图可写（INSTEAD OF 触发器）
- U7.1 活跃同型线唯一化 / U7.2 清仓僵尸归档 / U7.5 reconcile 对账 / pool_ledger CHECK
隔离：pytest 临时 workspace，价格/ATR 全 mock，零触网，生产库零接触。
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch

from paper_trading_v2.db import get_connection, migrate_db

from tests_v2.v9_helpers import (
    news_seg_id, tech_seg_id, seg_cash, set_seg_cash, seg_budget, acct_total,
    money_label_sum, insert_buy, insert_sell, make_manual_segment,
)


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


def _conn(env):
    return get_connection(env / 'master_pool.db')


def _trader(env):
    from paper_trading_v2.trading import PaperTrader
    from paper_trading_v2.storage import SqlStorage
    return PaperTrader(storage=SqlStorage(env / 'master_pool.db'))


def _PI(price):
    from paper_trading_v2.models import StockInfo
    return StockInfo(code='sh1', name='x', current_price=price, pre_close=price)


# ============ 段现金恒等式（U5 新增核心不变量） ============

def test_segment_cash_identity_through_full_lifecycle(pools, env):
    """allocate → buy → 部分卖出 → topup 后（v11 重铸）：信封段物理占用 ≤ 承诺
    （cash+FIFO−realized ≤ budget，权利非负）、段 cash 不留存、池逐分对账
    （free = base − 拨付 + 回款——拨付/回款各只记一次=零双计的强锁）。"""
    m = pools
    m.allocate('恒票', 500_000, reason='建段', code='sh1')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('恒票', quantity=20_000, note='建仓')       # 20 万（全额拨付）
        _trader(env).sell_stock('恒票', quantity=5_000, note='TP1')        # 5 万回笼回池
    m.topup('恒票', 100_000, reason='段内注资')
    conn = _conn(env)
    seg = tech_seg_id(conn, '恒票')
    seg_row = conn.execute("SELECT budget, cash, realized_pnl FROM position WHERE id=?",
                           (seg,)).fetchone()
    free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
    from paper_trading_v2.sleeve_slots import account_remaining
    qty, fifo_cost = account_remaining(conn, seg)
    conn.close()
    ident = seg_row['cash'] + fifo_cost - (seg_row['realized_pnl'] or 0.0)
    assert abs(fifo_cost - 150_000) < 0.01 and qty == 15_000
    # v11 信封不变量：物理占用 ≤ 承诺（旧等式 cash+FIFO−realized==budget 是
    # allocate 预支现金时代的装配恒等式；信封制下预算=权利，物理=成交额）
    assert ident <= seg_row['budget'] + 0.01, f"信封透支：{ident} > {seg_row['budget']}"
    assert abs(ident - 150_000) < 0.01, f"物理占用应=净成交额 15 万，实={ident}"
    assert abs(seg_row['cash'] or 0) < 0.01, f"段 cash 不留存：{seg_row['cash']}"
    # 池逐分：base 8M − 拨付 20 万 + 回款 5 万（拨付/回款双漏=此锁红）
    assert abs(free - (8_000_000 - 200_000 + 50_000)) < 0.01, f"free={free}"


def test_segment_cash_identity_all_open_segments(pools, env):
    """多段并存（tech L1 + sleeve NEWS + merge 段）后逐段核对。

    v11 双口径：NEWS 段=旧恒等式 cash+FIFO−realized==budget（红线不动）；
    v11 信封段=物理占用 ≤ 承诺 + cash 不留存（拨付即消耗/回款归池）。"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    from paper_trading_v2.sleeve_slots import account_remaining
    from paper_trading_v2.master_pool import MasterPoolManager
    m = pools
    m.allocate('恒A', 300_000, reason='建段', code='sh1')
    op = SleeveOpener(env / 'master_pool.db')
    op.open_slot(['恒B', '恒C'], budget=300_000, event_key='ND#M16A',
                 code_map={'恒B': 'sh1', '恒C': 'sh1'})
    op.fill_pending(event_key='ND#M16A', open_prices={'恒B': 10.0, '恒C': 20.0},
                    skip_conditions=True)
    conn = _conn(env)
    bad = []
    for seg in conn.execute("SELECT id, stock, strategy, budget, cash, realized_pnl, "
                            "source FROM position WHERE status='open'").fetchall():
        _, fifo_cost = account_remaining(conn, seg['id'])
        ident = (seg['cash'] or 0.0) + fifo_cost - (seg['realized_pnl'] or 0.0)
        base = seg['budget'] or 0.0
        if MasterPoolManager._is_v11_native(conn, seg, seg['stock']):
            if ident > base + 0.01 or (seg['cash'] or 0) > 0.01:
                bad.append((seg['stock'], 'v11-envelope', ident, base))
        elif abs(ident - base) > 0.01:
            bad.append((seg['stock'], ident, base))
    conn.close()
    assert not bad, f"段恒等式（v9 旧段等式 / v11 信封不等式）破坏：{bad}"


# ============ trades 归属完整性（无孤儿流水） ============

def test_trades_no_orphans_after_lifecycle(pools, env):
    """全生命周期后：每条 trades/operations/conditions 行的 account_id 恒指向存活段。"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    from paper_trading_v2.sleeve_migrate import SleeveMigrator
    m = pools
    m.allocate('孤A', 500_000, reason='建段', code='sh1')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('孤A', quantity=10_000, note='建仓')
        _trader(env).sell_stock('孤A', quantity=10_000, note='清仓')   # 触发自动 release
    op = SleeveOpener(env / 'master_pool.db')
    op.open_slot(['孤B'], budget=100_000, event_key='ND#M16B', code_map={'孤B': 'sh1'})
    op.fill_pending(event_key='ND#M16B', open_prices={'孤B': 10.0}, skip_conditions=True)
    SleeveMigrator(env / 'master_pool.db').migrate('孤B', reason='V11')
    conn = _conn(env)
    orphans = {}
    for t in ('trades', 'operations', 'conditions', 'exright_applied'):
        n = conn.execute(f"SELECT COUNT(*) FROM {t} t2 WHERE NOT EXISTS "
                         "(SELECT 1 FROM position p WHERE p.id=t2.account_id)").fetchone()[0]
        orphans[t] = n
    conn.close()
    assert all(v == 0 for v in orphans.values()), f"孤儿流水：{orphans}"


# ============ 同票双段并存：寻址零歧义（U5 验收项） ============

def test_dual_segment_addressing_sell_topup_conditions(pools, env):
    """同票 L1 段 + NEWS 段并存：sell 落 tech 段、sleeve topup 落 NEWS 段、
    conditions 落 resolve 段——互不串段。"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    from paper_trading_v2.sleeve_slots import account_remaining
    m = pools
    m.allocate('双段票', 500_000, reason='技术组建段', code='sh1')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(10.0)
        _trader(env).buy_stock('双段票', quantity=10_000, note='技术组买入')
    op = SleeveOpener(env / 'master_pool.db')
    # force=True：本测试正主=同票双段并存寻义零歧义（U5），双段并存是前置态；
    # 第四.8 升级为默认拒绝后，force 造前置态不伤原意图（寻址断言全部保留）
    op.open_slot(['双段票'], budget=100_000, event_key='ND#M16C', code_map={'双段票': 'sh1'},
                 force=True)
    op.fill_pending(event_key='ND#M16C', open_prices={'双段票': 10.0}, skip_conditions=True)
    conn = _conn(env)
    seg_tech = tech_seg_id(conn, '双段票')
    seg_news = news_seg_id(conn, '双段票')
    assert seg_tech and seg_news and seg_tech != seg_news
    # 两个段各自持有独立 FIFO 流水
    q_t, c_t = account_remaining(conn, seg_tech)
    q_n, c_n = account_remaining(conn, seg_news)
    conn.close()
    assert q_t == 10_000 and abs(c_t - 100_000) < 0.01
    assert q_n == 10_000 and abs(c_n - 100_000) < 0.01

    # sleeve topup → 只落 NEWS 段
    m.topup('双段票', 50_000, reason='sleeve 注资', pool='sleeve')
    conn = _conn(env)
    assert seg_budget(conn, seg_news) == 150_000
    assert seg_budget(conn, seg_tech) == 500_000
    assert seg_cash(conn, seg_news) == 50_000        # fill 已耗 10 万，注资 5 万进段现金
    # sell（默认寻址=非 NEWS open 段）→ 只动 tech 段
    conn.close()
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price') as mp:
        mp.return_value = _PI(11.0)
        _trader(env).sell_stock('双段票', quantity=4_000, note='技术组减仓')
    conn = _conn(env)
    q_t2, _ = account_remaining(conn, seg_tech)
    q_n2, _ = account_remaining(conn, seg_news)
    # conditions 写路径落 resolve 段（tech）
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions import Condition, ConditionsRecord
    cm = ConditionsManager(storage=SqlStorage(env / 'master_pool.db'))
    rec = cm.load_conditions('双段票')
    assert rec is not None
    rec.conditions['trailing_stop'] = Condition(
        id='t1', type='trailing_stop', name='移动止损', price=8.5, action='减仓50%',
        category='hard', status='active')
    cm.save_conditions(rec)
    n_cond_tech = conn.execute("SELECT COUNT(*) FROM conditions WHERE account_id=?",
                               (seg_tech,)).fetchone()[0]
    n_cond_news = conn.execute("SELECT COUNT(*) FROM conditions WHERE account_id=?",
                               (seg_news,)).fetchone()[0]
    conn.close()
    assert q_t2 == 6_000 and q_n2 == 10_000, "sell 必须只动 tech 段"
    assert n_cond_tech >= 1 and n_cond_news == 0, "conditions 必须落 resolve 段（tech）"


# ============ v9 迁移（合成 v8 库） ============

def _build_v8_db(db):
    """合成一个 v8 形态库：2 账户（1 活户带持仓 + 1 死壳带历史）+ 池。"""
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    from paper_trading_v2.db import SCHEMA_DDL, V3_DDL, V4_DDL
    conn.executescript("CREATE TABLE schema_meta (version INTEGER NOT NULL, migrated_at TEXT);"
                       "INSERT INTO schema_meta VALUES (8,'x');")
    conn.executescript(SCHEMA_DDL)
    conn.executescript(V3_DDL)
    conn.executescript(V4_DDL)
    conn.execute("INSERT INTO pool_ledger (id, total, free, updated_at) "
                 "VALUES (1, 10000000, 9500000, 'x')")
    # 活户：预算 50 万，买 1 万股 @10，段 open budget=50 万
    conn.execute("INSERT INTO accounts (id, stock_name, stock_code, capital_total, "
                 "capital_available, capital_used, fifo_index, fifo_offset) "
                 "VALUES (1, '活票', 'sh1', 500000, 400000, 100000, 0, 0)")
    conn.execute("INSERT INTO position (id, stock, code, strategy, status, budget, "
                 "topup_total, opened_at) VALUES (11, '活票', 'sh1', 'L1', 'open', "
                 "500000, 0, 't')")
    conn.execute("INSERT INTO positions (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost) VALUES (1, 0, 'buy', 'sh1', 10000, 10.0, 100000)")
    conn.execute("INSERT INTO operations (account_id, seq, type, capital) "
                 "VALUES (1, 0, 'init', 500000)")
    # 死壳：资金 0，历史买卖相抵（FIFO 残余 0），配最后归档段
    conn.execute("INSERT INTO accounts (id, stock_name, stock_code, capital_total, "
                 "capital_available, capital_used, fifo_index, fifo_offset) "
                 "VALUES (2, '壳票', 'sh2', 0, 0, 0, -1, 0)")
    conn.execute("INSERT INTO position (id, stock, code, strategy, status, budget, "
                 "topup_total, opened_at, closed_at) VALUES (12, '壳票', 'sh2', 'L2', "
                 "'closed', 300000, 0, 't', 't')")
    conn.execute("INSERT INTO positions (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost) VALUES (2, 0, 'buy', 'sh2', 1000, 10.0, 10000)")
    conn.execute("INSERT INTO positions (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost) VALUES (2, 1, 'sell', 'sh2', 1000, 12.0, 10000)")
    conn.commit()
    conn.close()
    return {1: 11, 2: 12}


def test_v9_migration_synthetic_v8_db(ws):
    """合成 v8 库 → v9：段现金逐分对账、资金恒等式前后逐分相等、accounts_old 保留、
    兼容视图可写、FK 零违规。"""
    db = ws / 'master_pool.db'
    mapping = _build_v8_db(db)
    old = sqlite3.connect(str(db))
    old.row_factory = sqlite3.Row
    before_money = old.execute("SELECT COALESCE(SUM(capital_available),0) FROM accounts"
                               ).fetchone()[0]
    before_free = old.execute("SELECT free FROM pool_ledger").fetchone()[0]
    before_positions = old.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    old.close()

    conn = get_connection(db)
    migrate_db(conn)
    after_money = conn.execute("SELECT COALESCE(SUM(cash),0) FROM position").fetchone()[0]
    after_free = conn.execute("SELECT free FROM pool_ledger").fetchone()[0]
    after_positions = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    # 逐分对账
    seg_live = conn.execute("SELECT budget, cash, fifo_index FROM position WHERE id=11"
                            ).fetchone()
    assert seg_live['cash'] == 400_000 and seg_live['budget'] == 500_000
    assert seg_live['fifo_index'] == 0
    seg_shell = conn.execute("SELECT cash FROM position WHERE id=12").fetchone()
    assert seg_shell['cash'] == 0
    # trades 行随迁（account_id=段 id）
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=11").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=12").fetchone()[0] == 2
    # accounts 退役（2026-09-04 v10 债清理后无 U1 兼容视图——迁移不再建 positions 视图）
    assert conn.execute("SELECT COUNT(*) FROM accounts_old").fetchone()[0] == 2
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert 'positions' not in views
    # 新代码直写 trades（垫片已清：视图/触发器路径不再存在）
    conn.execute("INSERT INTO trades (account_id, seq, operation, quantity) "
                 "VALUES (11, 9, 'buy', 1)")
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE seq=9").fetchone()[0] == 1
    conn.execute("DELETE FROM trades WHERE seq=9")
    # FK 零违规
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
    # 恒等式前后逐分相等
    assert after_money == before_money
    assert after_free == before_free
    assert after_positions == before_positions


# ============ U7 卫生项 ============

def test_u71_conditions_dedupe_active_same_type(pools, env):
    """U7.1：同型 active 线唯一化——save_conditions 落库前收敛为一条（保留价高者）。"""
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions import Condition, ConditionsRecord
    m = pools
    m.allocate('凯票', 300_000, reason='建段', code='sh1')
    cm = ConditionsManager(storage=SqlStorage(env / 'master_pool.db'))
    rec = ConditionsRecord(stock_name='凯票', conditions={
        't_low': Condition(id='tl', type='trailing_stop', name='低垂线', price=90.0,
                           action='减仓50%', category='hard', status='active'),
        't_high': Condition(id='th', type='trailing_stop', name='高线', price=110.0,
                            action='减仓50%', category='hard', status='active'),
    })
    cm.save_conditions(rec)
    conn = _conn(env)
    seg = tech_seg_id(conn, '凯票')
    rows = conn.execute("SELECT price, status FROM conditions WHERE account_id=? AND "
                        "type='trailing_stop'", (seg,)).fetchall()
    conn.close()
    active = [r['price'] for r in rows if r['status'] == 'active']
    assert active == [110.0], f"同型 active 线应唯一且保留价高者：{active}"


def test_u72_zombie_conditions_archived_on_closed_segments(env, ws):
    """U7.2：清仓段（资金已结算）的 active/suspended 条件线 → 迁移时归档。"""
    db = ws / 'master_pool.db'
    _build_v8_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO conditions (account_id, type, name, price, action, category, "
                 "status) VALUES (2, 'take_profit_1', '僵尸tp1', 50.0, '清仓', 'hard', 'active')")
    conn.commit()
    conn.close()
    conn = get_connection(db)
    migrate_db(conn)
    rows = conn.execute("SELECT status FROM conditions WHERE name='僵尸tp1'").fetchall()
    conn.close()
    assert [r['status'] for r in rows] == ['archived']


def test_u75_reconcile_reports_identity(pools, env, capsys):
    """U7.5：reconcile 对账——恒等式在容差内 ✅；制造超差 → ⚠️ 告警（只报不拦）。"""
    from paper_trading_v2.cli import app
    from typer.testing import CliRunner
    runner = CliRunner()
    m = pools
    m.allocate('对票', 500_000, reason='建段', code='sh1')
    r = runner.invoke(app, ["reconcile", "--detail"])
    assert r.exit_code == 0
    assert "✅ 恒等式在容差内" in r.output
    assert "段现金恒等式" in r.output
    # 制造超差：凭空抽走池资金（模拟账外流出 50 万 > 容差）
    conn = _conn(env)
    conn.execute("UPDATE pool_ledger SET free=free-500000 WHERE id=1")
    conn.commit()
    conn.close()
    r2 = runner.invoke(app, ["reconcile"])
    assert r2.exit_code == 0
    assert "超差告警" in r2.output


def test_v9_pool_ledger_check_free_nonnegative(env):
    """M17-D12 v9 窗口项：pool_ledger CHECK(free>=0)——负余额写入即崩（账本错账即崩）。"""
    db = env / 'master_pool.db'
    conn = get_connection(db)
    migrate_db(conn)
    conn.execute("INSERT INTO pool_ledger (id, total, free, updated_at) "
                 "VALUES (1, 10000000, 10000000, 't')")
    with pytest.raises(Exception):
        conn.execute("UPDATE pool_ledger SET free=-1 WHERE id=1")
    conn.rollback()
    conn.close()
