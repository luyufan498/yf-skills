"""分红现金入账测试（2026-09-07 drift 根因修复）：摊本 + 收现同步。

场景：持仓股现金分红 → ExRightHandler 写 exright_dividend（tc=-dividend）摊薄成本
→ credit_dividend 把分红现金入对应池 free（audit 'dividend'）→ reconcile drift 恒 0。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from unittest.mock import patch


def _mk_pool(ws, db_path):
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(db_path)
    m.init_pool(10_000_000)
    # 消息池 ledger（sleeve_ledger id=1，总额 200 万）
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO sleeve_ledger (id, total, free, updated_at) "
                 "VALUES (1, 2000000, 2000000, '2026-09-07T00:00:00')")
    conn.commit()
    conn.close()
    return m


def _mk_seg(db_path, stock, strategy='L1', code=None, qty=600, px=10.0):
    """建 open 技术段 + 一笔 buy（v11 native 直写 trades，pool 不联动——单测只验收现侧）。"""
    from paper_trading_v2.storage import SqlStorage
    s = SqlStorage(db_path)
    from paper_trading_v2.models import Account, CapitalPool
    acct = Account(stock_name=stock, stock_code=code or '600000',
                   capital_pool=CapitalPool(total=10000, available=0, used=10000))
    s.save_account(acct)                      # 建 open 段（固定 L1）
    conn = s._conn()
    with conn:
        conn.execute("UPDATE position SET strategy=? WHERE stock=? AND status='open'",
                     (strategy, stock))
        seg = conn.execute("SELECT id FROM position WHERE stock=? AND status='open'",
                           (stock,)).fetchone()
        conn.execute(
            "INSERT INTO trades (account_id, seq, operation, quantity, price, "
            "total_cost, timestamp) VALUES (?,0,'buy',?,?,?,?)",
            (seg['id'], qty, px, qty * px, '2026-09-01T10:00:00'))
    conn.close()
    return s


def test_credit_dividend_tech_segment(ws, db_path):
    """技术段（L1）分红 → 主池 free +分红 + audit dividend。"""
    _mk_pool(ws, db_path)
    s = _mk_seg(db_path, '分众传媒', strategy='L1')
    assert s.credit_dividend('分众传媒', 301.80, '2026-09-04 10派0.5元') is True
    conn = s._conn()
    free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
    assert free == 10_000_000 + 301.80, f'主池 free 应增 301.80，实得 {free}'
    a = conn.execute("SELECT * FROM audit WHERE action='dividend'").fetchone()
    assert a and a['stock'] == '分众传媒' and abs(a['amount'] - 301.80) < 1e-9
    assert a['free_before'] == 10_000_000 and a['free_after'] == 10_000_000 + 301.80
    conn.close()


def test_credit_dividend_news_segment(ws, db_path):
    """NEWS 成员段分红 → 消息池 sleeve_ledger free +分红（不误入主池）。"""
    _mk_pool(ws, db_path)
    s = _mk_seg(db_path, '中恒电气', strategy='NEWS')
    assert s.credit_dividend('中恒电气', 50.0, '10派0.5元') is True
    conn = s._conn()
    sf = conn.execute("SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
    pf = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
    assert sf == 2_000_000 + 50.0 and pf == 10_000_000, f'sleeve {sf} / 主池 {pf}'
    conn.close()


def test_credit_dividend_closed_segment_skipped(ws, db_path):
    """closed 段（已 release 结算）→ 静默 False 不入账。"""
    _mk_pool(ws, db_path)
    s = _mk_seg(db_path, '沃森生物', strategy='L1')
    conn = s._conn()
    with conn:
        conn.execute("UPDATE position SET status='closed' WHERE stock='沃森生物'")
    conn.close()
    assert s.credit_dividend('沃森生物', 100.0) is False
    conn = s._conn()
    assert conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0] == 10_000_000
    assert conn.execute("SELECT COUNT(*) FROM audit WHERE action='dividend'").fetchone()[0] == 0
    conn.close()


def test_credit_dividend_zero_amount(ws, db_path):
    """amount<=0 → False（送股/无分红事件不触发收现）。"""
    _mk_pool(ws, db_path)
    s = _mk_seg(db_path, '分众传媒', strategy='L1')
    assert s.credit_dividend('分众传媒', 0) is False
    assert s.credit_dividend('分众传媒', -5) is False
    conn = s._conn()
    assert conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0] == 10_000_000
    conn.close()


def test_handler_integration_dividend_credited(ws, db_path):
    """端到端：handler 应用 10派0.5 分红 → 摊本 + 主池收现 + exright_applied 记档。"""
    _mk_pool(ws, db_path)
    from paper_trading_v2.storage import SqlStorage
    s = SqlStorage(db_path)
    from paper_trading_v2.models import Account, CapitalPool
    acct = Account(stock_name='分众传媒', stock_code='002027',
                   capital_pool=CapitalPool(total=6000, available=0, used=6000))
    s.save_account(acct)
    conn = s._conn()
    with conn:
        conn.execute("UPDATE position SET strategy='L1' WHERE stock='分众传媒'")
        seg = conn.execute("SELECT id FROM position WHERE stock='分众传媒'").fetchone()
        conn.execute(
            "INSERT INTO trades (account_id, seq, operation, quantity, price, "
            "total_cost, timestamp) VALUES (?,0,'buy',600,10.0,6000,'2026-09-01T10:00:00')",
            (seg['id'],))
    conn.close()

    trader = _FakeTrader(s)
    from paper_trading_v2.exright_handler import ExRightHandler
    from paper_trading_v2.exright_cache import ExRightCache
    h = ExRightHandler(trader, _FakeCache())
    # account 需带 exright_applied/positions——走 storage.load_account 水合
    acct2 = s.load_account('分众传媒')
    acct2.stock_code = '002027'
    with patch.object(ExRightHandler, '_migrate_account', lambda self, a: None):
        changed, msg = h.check_and_apply('分众传媒', acct2)
    assert changed, msg
    conn = s._conn()
    # 摊本：trades 有 exright_dividend tc=-30（600股×0.05）
    row = conn.execute("SELECT total_cost FROM trades WHERE operation='exright_dividend' "
                       "AND account_id=(SELECT id FROM position WHERE stock='分众传媒')").fetchone()
    assert row and abs(row['total_cost'] + 30.0) < 1e-9, f'摊本行缺失: {row}'
    # 收现：主池 +30
    free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
    assert free == 10_000_000 + 30.0, f'主池应 +30，实得 {free}'
    # 记档
    ex = conn.execute("SELECT * FROM exright_applied WHERE account_id="
                      "(SELECT id FROM position WHERE stock='分众传媒') "
                      "ORDER BY rowid DESC LIMIT 1").fetchone()
    assert ex and '分红' in ex['reason']
    conn.close()


class _FakeTrader:
    """最小 trader 桩：storage 真实、其余查询返回固定剩余持仓。"""
    def __init__(self, storage):
        self.storage = storage

    def get_remaining_position(self, account):
        return 600, 6000.0


class _FakeCache:
    """返回一个未应用分红事件：cqr=2026-09-04, 10派0.5元（送转 0）。"""
    def get_events(self, code):
        return [{'cqr': '2026-09-04', 'djr': '2026-09-03', 'fhcontent': '10派0.5元',
                 'bonus_per_10': 0.5, 'split_per_10': 0}]
