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
    mpm.allocate('L1股', 1000000, reason='人工')
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


def test_l1_release_requires_manual(mpm, ws):
    from paper_trading_v2.watchlist import Watchlist
    Watchlist(ws / 'master_pool.db').add('赛力斯', 'sh603527', strategy='L1', source='manual')
    mpm.allocate('赛力斯', 1000000, reason='人工')
    with pytest.raises(ValueError, match="L1"):
        mpm.release('赛力斯', reason='agent想释放', source='agent')
    mpm.release('赛力斯', reason='人工释放', source='manual')
