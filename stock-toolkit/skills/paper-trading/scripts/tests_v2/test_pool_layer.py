"""新池层测试：MasterPoolManager / PoolManager / Watchlist"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from unittest.mock import patch


@pytest.fixture
def mpm(ws):
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(ws / 'master_pool.db')
    m.init_pool(10000000)
    return m


@pytest.fixture(autouse=True)
def no_network(ws):
    """mock init_account / get_account 避免网络验证"""
    from paper_trading_v2.master_pool import MasterPoolManager
    with patch('paper_trading_v2.trading.PaperTrader.init_account') as mi, \
         patch('paper_trading_v2.trading.PaperTrader.get_account') as mg:
        def _fake_init(stock_name, capital, stock_code=None, force=False):
            from paper_trading_v2.models import Account, CapitalPool
            from paper_trading_v2.storage import SqlStorage
            s = SqlStorage(ws / 'master_pool.db')
            acct = Account(stock_name=stock_name, stock_code=stock_code,
                           capital_pool=CapitalPool(total=capital, available=capital, used=0))
            s.save_account(acct)
            return acct
        def _fake_get(stock_name):
            from paper_trading_v2.storage import SqlStorage
            return SqlStorage(ws / 'master_pool.db').load_account(stock_name)
        mi.side_effect = _fake_init
        mg.side_effect = _fake_get
        yield


def test_init_show(mpm):
    d = mpm.show()
    assert d['total'] == 10000000
    assert d['free'] == 10000000
    assert d['open_segments'] == 0


def test_watchlist_tiers(ws):
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist(ws / 'master_pool.db')
    w.add('赛力斯', 'sh603527', strategy='L1', source='manual', reason='用户锁定')
    w.add('英维克', 'sz000301', strategy='L2', source='agent')
    w.add('铂科新材', 'sz300811', strategy='L3', source='agent')
    stocks = w.list()
    assert len(stocks) == 3
    assert {s['strategy'] for s in stocks} == {'L1', 'L2', 'L3'}
    with pytest.raises(ValueError):
        w.remove('赛力斯', source='agent')
    w.remove('英维克', source='agent')
    assert len(w.list()) == 2


def test_allocate_release_roundtrip(mpm, ws):
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('赛力斯', 'sh603527', strategy='L2', source='agent')
    mpm.allocate('赛力斯', 2000000, reason='右侧建仓')
    d = mpm.show()
    assert d['free'] == 8000000
    assert d['open_segments'] == 1
    mpm.release('赛力斯', reason='空仓释放')
    d = mpm.show()
    assert d['free'] == 10000000
    from paper_trading_v2.pool_manager import PoolManager
    assert PoolManager(ws / 'master_pool.db').in_cooldown('赛力斯') is True


def test_allocate_over_budget(mpm):
    with pytest.raises(ValueError, match="空闲不足"):
        mpm.allocate('某股', 99999999, reason='超支')


def test_allocate_slot_limit(mpm, ws):
    """非 L1 段位满 8 后拒绝新 allocate；L1 豁免"""
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist(ws / 'master_pool.db')
    for i in range(8):
        name = f'股{i}'
        w.add(name, f'sz{100000+i:06d}', strategy='L2', source='agent')
        mpm.allocate(name, 100000, reason=f'第{i+1}只')
    with pytest.raises(ValueError, match="段"):
        mpm.allocate('第九只', 100000, reason='满员')
    # L1 豁免
    w.add('L1股', 'sh600000', strategy='L1', source='manual')
    mpm.allocate('L1股', 1000000, reason='人工', source='manual')
    d = mpm.show()
    assert d['open_segments'] == 9


def test_topup(mpm, ws):
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('英维克', 'sz000301', strategy='L2', source='agent')
    mpm.allocate('英维克', 500000, reason='建仓')
    mpm.topup('英维克', 300000, reason='补弹药')
    d = mpm.show()
    assert d['free'] == 10000000 - 500000 - 300000
    from paper_trading_v2.storage import SqlStorage
    acct = SqlStorage(ws / 'master_pool.db').load_account('英维克')
    assert acct.capital_pool.total == 800000
    assert acct.capital_pool.available == 800000


def test_topup_enforces_cumulative_30pct(mpm, ws):
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('英维克', 'sz000301', strategy='L2', source='agent')
    mpm.allocate('英维克', 3000000, reason='建仓')          # 30% of 10M
    with pytest.raises(ValueError, match="30%"):
        mpm.topup('英维克', 1000000, reason='超累计')        # 30%+10% = 40%


def test_l1_release_requires_manual(mpm, ws):
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('赛力斯', 'sh603527', strategy='L1', source='manual')
    mpm.allocate('赛力斯', 1000000, reason='人工', source='manual')
    with pytest.raises(ValueError, match="L1"):
        mpm.release('赛力斯', reason='agent想释放', source='agent')
    mpm.release('赛力斯', reason='人工释放', source='manual')


def test_reallocate_after_release_resets_account(mpm, ws):
    """冷却期过后重新 allocate：账户重置为新预算 + 旧操作归档"""
    from paper_trading_v2.watchlist import Watchlist
    from paper_trading_v2.storage import SqlStorage
    Watchlist(ws / 'master_pool.db').add('赛力斯', 'sh603527', strategy='L2', source='agent')
    mpm.allocate('赛力斯', 1000000, reason='第一段')
    mpm.release('赛力斯', reason='释放')
    # 绕过冷却：把 cooldown_until 改到过去
    conn = mpm._conn()
    conn.execute("UPDATE position SET cooldown_until=? WHERE stock='赛力斯'",
                 ('2026-01-01T00:00:00',))
    conn.commit()
    conn.close()
    mpm.allocate('赛力斯', 800000, reason='第二段')
    acct = SqlStorage(ws / 'master_pool.db').load_account('赛力斯')
    assert acct.capital_pool.total == 800000
    assert acct.capital_pool.available == 800000
    ops = SqlStorage(ws / 'master_pool.db').load_operations('赛力斯')
    assert len(ops.operations) == 1 and ops.operations[0].type == 'init'
    conn = mpm._conn()
    archived = conn.execute("SELECT COUNT(*) c FROM operations_archive WHERE account_id="
                            "(SELECT id FROM accounts WHERE stock_name='赛力斯')").fetchone()['c']
    conn.close()
    assert archived >= 1


def test_allocate_blocked_during_cooldown(mpm, ws):
    """冷却期内禁止重新 allocate"""
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('英维克', 'sz000301', strategy='L2', source='agent')
    mpm.allocate('英维克', 500000, reason='建仓')
    mpm.release('英维克', reason='释放')
    with pytest.raises(ValueError, match="冷却"):
        mpm.allocate('英维克', 500000, reason='想追回')


def test_allocate_blocks_when_already_open(mpm, ws):
    """已有 open 段时禁止重复 allocate"""
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('科创新源', 'sz300731', strategy='L2', source='agent')
    mpm.allocate('科创新源', 500000, reason='建仓')
    with pytest.raises(ValueError, match="open 段"):
        mpm.allocate('科创新源', 500000, reason='重复分配')


def test_l1_downgrade_rejected(ws):
    from paper_trading_v2.watchlist import Watchlist
    w = Watchlist(ws / 'master_pool.db')
    w.add('赛力斯', 'sh603527', strategy='L1', source='manual', reason='用户锁定')
    with pytest.raises(ValueError, match="L1"):
        w.add('赛力斯', strategy='L2', source='agent', reason='agent想降级')
    # 人工可降级
    w.add('赛力斯', strategy='L2', source='manual', reason='人工降级')
    assert w.get('赛力斯')['strategy'] == 'L2'


def test_l1_allocate_requires_manual(mpm, ws):
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('赛力斯', 'sh603527', strategy='L1', source='manual')
    with pytest.raises(ValueError, match="L1"):
        mpm.allocate('赛力斯', 1000000, reason='agent想开L1仓')
    mpm.allocate('赛力斯', 1000000, reason='人工开仓', source='manual')


def test_show_reflects_realized_pnl(mpm, ws):
    """释放盈利段后 free > total，show 的 occupied 应为 0、realized_pnl 反映盈亏"""
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('英维克', 'sz000301', strategy='L2', source='agent')
    mpm.allocate('英维克', 2000000, reason='建仓')
    # 模拟盈利：账户 available 增 50 万（本应通过买卖产生）
    conn = mpm._conn()
    conn.execute("UPDATE accounts SET capital_available=capital_available+500000, "
                 "capital_total=capital_total+500000 WHERE stock_name='英维克'")
    conn.commit()
    conn.close()
    mpm.release('英维克', reason='盈利释放')
    d = mpm.show()
    assert d['free'] == 10000000 + 500000
    assert d['occupied'] == 0
    assert d['open_segments'] == 0
    assert abs(d['realized_pnl'] - 500000) < 1
