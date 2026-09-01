"""池感知（双 ledger）+ NEWS 档位 + 归档语义 + 闸门测试（sleeve-m1）

覆盖任务书必做项 2 的：
- STRATEGIES+NEWS 与 refresh_cadence 分支
- watchlist remove --archive（不动旧 removed 语义）
- MasterPoolManager 池感知参数化（pool='main' 默认=现行为；pool='sleeve' 路由 sleeve_ledger）
- 双 ledger 互不透支
- 20 段位上限与 NEWS position 互不侵占
- 闸门：grp=news 禁 conditions 写/buy/allocate/topup + shadow_log gate_violation
- _auto_release_on_clear 组分支（SLEEVE_ARCHIVE_ON_CLEAR flag 后置）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch

from paper_trading_v2.gate import GateViolation


@pytest.fixture(autouse=True)
def no_network(ws):
    """mock init_account / get_account 避免网络验证（与 test_pool_layer 同款）"""
    with patch('paper_trading_v2.trading.PaperTrader.init_account'), \
         patch('paper_trading_v2.trading.PaperTrader.get_account') as mg:
        def _fake_get(stock_name):
            from paper_trading_v2.storage import SqlStorage
            return SqlStorage(ws / 'master_pool.db').load_account(stock_name)
        mg.side_effect = _fake_get
        yield


def _db(ws):
    return ws / 'master_pool.db'


def _sql(conn, q, args=()):
    return conn.execute(q, args).fetchall()


# ---------- watchlist ----------

def test_strategies_include_news():
    from paper_trading_v2.watchlist import STRATEGIES
    assert 'NEWS' in STRATEGIES and {'L1', 'L2', 'L3'} <= set(STRATEGIES)


def test_news_strategy_cadence_and_unchanged_l1_l2_l3(ws):
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist(_db(ws))
    w.add('技术票', 'sh600000', strategy='L2')
    w.add('观察票', 'sh600001', strategy='L3')
    w.add('消息票', 'sh600002', strategy='NEWS', event_key='ND#293', news_kind='policy')
    rows = {r['stock']: r for r in w.list()}
    assert rows['技术票']['refresh_cadence'] == 'daily'      # 旧行为不变
    assert rows['观察票']['refresh_cadence'] == 'event'      # 旧行为不变
    assert rows['消息票']['refresh_cadence'] == 'event'      # NEWS(event)
    assert rows['消息票']['event_key'] == 'ND#293'           # pool 落 event_key
    log = [r for r in w.log(stock='消息票') if r['action'] == 'add'][0]
    assert log['event_key'] == 'ND#293' and log['news_kind'] == 'policy'   # watchlog 落列


def test_remove_old_semantics_untouched_vs_archive(ws):
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist(_db(ws))
    w.add('旧路径票', 'sh600003', strategy='L2')
    w.add('归档票', 'sh600004', strategy='NEWS', event_key='ND#1', news_kind='policy')
    w.remove('旧路径票', source='agent', reason='僵尸')
    w.remove('归档票', source='agent', reason='清仓归档', archive=True)
    old = w.get('旧路径票')
    arch = w.get('归档票')
    assert old['pool_status'] == 'removed' and old['archived_at'] is None   # 旧语义原样
    assert arch['pool_status'] == 'archived' and arch['archived_at']        # 新路径
    log = {r['action'] for r in w.log(stock='归档票')}
    assert 'archive' in log


# ---------- 双 ledger 池感知 ----------

@pytest.fixture
def mpm2(ws):
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(_db(ws))
    m.init_pool(10000000)                      # 主池（默认 main）
    # M1.8/R1：sleeve init=从主池配对划拨 2M，主池 base 10M→8M（下文 main 断言同口径）
    m.init_pool(2000000, pool='sleeve')        # 消息池
    return m


# M1.8/R1 后的主池 base（10M 注入 − 2M 划拨给消息池）
MAIN_BASE = 8_000_000


def test_sleeve_ledger_init_and_show(mpm2):
    d = mpm2.show(pool='sleeve')
    assert d['total'] == 2000000 and d['free'] == 2000000
    d_main = mpm2.show()                       # 默认 main：主池已被划拨扣减
    assert d_main['total'] == MAIN_BASE


def test_double_ledger_no_overdraft_either_direction(mpm2, ws):
    """sleeve 花完不影响 main free；main 花完不影响 sleeve free。"""
    mpm2.allocate('sleeve成员', 1500000, reason='事件等权', pool='sleeve', grp='news')
    assert mpm2.show(pool='sleeve')['free'] == 500000
    assert mpm2.show()['free'] == MAIN_BASE                    # 主池分文未动
    with pytest.raises(ValueError, match='空闲不足'):
        mpm2.allocate('sleeve成员2', 600000, reason='超支', pool='sleeve', grp='news')
    # main 方向
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(_db(ws)).add('技术票', 'sh600000', strategy='L2')
    mpm2.allocate('技术票', 2000000, reason='建仓')             # ≤30%×主池（8M×30%=2.4M）
    assert mpm2.show()['free'] == MAIN_BASE - 2000000
    assert mpm2.show(pool='sleeve')['free'] == 500000          # 消息池分文未动


def test_sleeve_position_rows_marked_news_and_excluded_from_main_cap(mpm2, ws):
    """sleeve 成员的 position 行 strategy='NEWS'；主池 20 段位与主池统计不计 NEWS 行。"""
    for i in range(3):
        mpm2.allocate(f'成员{i}', 100000, reason='事件', pool='sleeve', grp='news')
    conn = mpm2._conn()
    news_rows = conn.execute("SELECT COUNT(*) FROM position WHERE strategy='NEWS' "
                             "AND status='open'").fetchone()[0]
    conn.close()
    assert news_rows == 3
    assert mpm2.show()['open_segments'] == 0                   # 主池统计不含 NEWS 行
    assert mpm2.show()['occupied'] == 0
    assert mpm2.show(pool='sleeve')['open_segments'] == 3      # 消息池视角=3 成员段
    from paper_trading_v2.pool_manager import PoolManager
    assert PoolManager(_db(ws)).is_agent_slot_available() is True   # 20 段位不被侵占


def test_main_pool_accounting_excludes_news_on_release(mpm2, ws):
    """sleeve release 回款进 sleeve_ledger 而非 pool_ledger（资金守恒）。"""
    mpm2.allocate('成员A', 500000, reason='事件', pool='sleeve', grp='news')
    conn = mpm2._conn()
    conn.execute("UPDATE position SET cash=600000, budget=600000 "
                 "WHERE stock='成员A' AND status='open'")       # 模拟盈利
    conn.commit(); conn.close()
    mpm2.release('成员A', reason='清仓', pool='sleeve')
    assert mpm2.show(pool='sleeve')['free'] == 2000000 + 100000
    assert mpm2.show()['free'] == MAIN_BASE                      # 主池不受影响


# ---------- 闸门（grp×命令矩阵）----------

@pytest.fixture
def sleeve_member(mpm2, ws):
    """已开槽的 sleeve 成员（grp=news 账户 + NEWS position + open 槽）。"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    op = SleeveOpener(_db(ws))
    op.open_slot(['成员X'], budget=300000, event_key='ND#101',
                 news_kind='price_cycle', reason='测试开槽')
    return op


def test_gate_blocks_conditions_write_on_news_account(sleeve_member, ws):
    from paper_trading_v2.gate import enforce
    with pytest.raises(GateViolation, match='消息组'):
        enforce('成员X', 'conditions_write')
    conn = sqlite3_connect(_db(ws))
    n = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='gate_violation'"
                     ).fetchone()[0]
    conn.close()
    assert n >= 1


def test_gate_blocks_buy_and_topup_on_news_account(sleeve_member):
    from paper_trading_v2.gate import enforce
    with pytest.raises(GateViolation):
        enforce('成员X', 'buy')
    with pytest.raises(GateViolation):
        enforce('成员X', 'topup')


def test_gate_blocks_main_allocate_on_sleeve_member(mpm2, sleeve_member):
    with pytest.raises(GateViolation, match='迁移桥'):
        mpm2.allocate('成员X', 100000, reason='绕过 sleeve')


def test_gate_blocks_topup_on_migrated_slot(mpm2, ws, sleeve_member):
    """迁移后加仓锁：pool.event_key → migrated 槽 topup_locked=1 → topup 拒绝。"""
    from paper_trading_v2.sleeve_migrate import SleeveMigrator
    from paper_trading_v2.gate import enforce
    # 成员X 买入持仓后迁移到主仓
    from paper_trading_v2.sleeve_open import SleeveOpener
    SleeveOpener(_db(ws)).fill_pending(event_key='ND#101',
                                       open_prices={'成员X': 10.0},
                                       skip_conditions=True)
    SleeveMigrator(_db(ws)).migrate('成员X', reason='V11 资格')
    conn = mpm2._conn()
    locked = conn.execute("SELECT topup_locked FROM event_slots WHERE event_key='ND#101'"
                          ).fetchone()[0]
    strat = conn.execute("SELECT strategy FROM pool WHERE stock='成员X'").fetchone()[0]
    conn.close()
    assert locked == 1
    assert strat == 'L1'
    with pytest.raises(GateViolation, match='加仓锁'):
        enforce('成员X', 'topup')


def test_gate_allows_tech_group_normal_ops(mpm2, ws):
    """技术组不受影响：L2 票 allocate/topup 正常。"""
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(_db(ws)).add('纯技术票', 'sh600009', strategy='L2')
    mpm2.allocate('纯技术票', 100000, reason='建仓')
    mpm2.topup('纯技术票', 50000, reason='补弹药')       # 不抛闸门异常
    from paper_trading_v2.gate import enforce
    enforce('纯技术票', 'conditions_write')              # 不抛


# ---------- _auto_release_on_clear 组分支（flag 后置）----------

def _clear_position(ws, stock, value):
    """把账户模拟成已清仓（available=value，无持仓）。"""
    from paper_trading_v2.storage import SqlStorage
    s = SqlStorage(_db(ws))
    acct = s.load_account(stock)
    acct.positions = []
    acct.capital_pool.available = value
    acct.capital_pool.used = 0
    s.save_account(acct)


def test_auto_release_flag_off_keeps_old_behavior(mpm2, ws, monkeypatch):
    """flag 关（默认）：tech 清仓 → 主池 release + 档位降回 L2（旧行为逐条一致）。"""
    from paper_trading_v2.watchlist import Watchlist
    from paper_trading_v2.trading import PaperTrader
    Watchlist(_db(ws)).add('旧语义票', 'sh600010', strategy='L2')
    mpm2.allocate('旧语义票', 200000, reason='建仓')
    _clear_position(ws, '旧语义票', 180000)
    monkeypatch.delenv('SLEEVE_ARCHIVE_ON_CLEAR', raising=False)
    PaperTrader()._auto_release_on_clear('旧语义票')
    conn = mpm2._conn()
    seg = conn.execute("SELECT status, realized_pnl FROM position WHERE stock='旧语义票'"
                       ).fetchone()
    pool_row = conn.execute("SELECT strategy, pool_status FROM pool WHERE stock='旧语义票'"
                            ).fetchone()
    d = mpm2.show()
    conn.close()
    assert seg['status'] == 'closed'
    assert pool_row['strategy'] == 'L2'                    # 旧语义：降回 L2
    assert pool_row['pool_status'] == 'active'
    assert abs(d['free'] - (MAIN_BASE - 200000 + 180000)) < 1


def test_auto_release_flag_on_tech_archives_not_downgrade(mpm2, ws, monkeypatch):
    """flag 开：tech 清仓 → release 回资金 + archived 终态，不再降 L2。"""
    from paper_trading_v2.watchlist import Watchlist
    from paper_trading_v2.trading import PaperTrader
    Watchlist(_db(ws)).add('归档技术票', 'sh600011', strategy='L2')
    mpm2.allocate('归档技术票', 200000, reason='建仓')
    _clear_position(ws, '归档技术票', 250000)
    monkeypatch.setenv('SLEEVE_ARCHIVE_ON_CLEAR', '1')
    PaperTrader()._auto_release_on_clear('归档技术票')
    conn = mpm2._conn()
    pool_row = conn.execute("SELECT strategy, pool_status, archived_at FROM pool "
                            "WHERE stock='归档技术票'").fetchone()
    seg = conn.execute("SELECT status FROM position WHERE stock='归档技术票'").fetchone()
    conn.close()
    assert seg['status'] == 'closed'
    assert pool_row['pool_status'] == 'archived' and pool_row['archived_at']
    assert pool_row['strategy'] == 'L1'                    # allocate 升的 L1 不被强制降档（archive 路径不动 strategy）


def test_auto_release_news_member_returns_to_sleeve_ledger(mpm2, ws, monkeypatch):
    """news 成员清仓：资金回 sleeve_ledger + 槽 closed + 不触发主池 release。"""
    from paper_trading_v2.trading import PaperTrader
    sleeve_member = None
    from paper_trading_v2.sleeve_open import SleeveOpener
    SleeveOpener(_db(ws)).open_slot(['成员Y'], budget=200000, event_key='ND#77',
                                    news_kind='policy', reason='测试')
    _clear_position(ws, '成员Y', 240000)
    monkeypatch.setenv('SLEEVE_ARCHIVE_ON_CLEAR', '1')
    PaperTrader()._auto_release_on_clear('成员Y')
    d = mpm2.show(pool='sleeve')
    assert abs(d['free'] - (2000000 - 200000 + 240000)) < 1     # 回款进消息池
    assert mpm2.show()['free'] == MAIN_BASE                      # 主池分文未动
    conn = mpm2._conn()
    slot = conn.execute("SELECT status, realized FROM event_slots WHERE event_key='ND#77'"
                        ).fetchone()
    seg = conn.execute("SELECT status FROM position WHERE stock='成员Y'").fetchone()
    n_main_audit = conn.execute("SELECT COUNT(*) FROM audit WHERE stock='成员Y' "
                                "AND action='release'").fetchone()[0]
    conn.close()
    assert slot['status'] == 'closed'
    assert abs(slot['realized'] - 40000) < 1                    # 槽对账累计已实现
    assert seg['status'] == 'closed'
    assert n_main_audit == 0                                    # 未误触主池 release


import sqlite3


def sqlite3_connect(db):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn
