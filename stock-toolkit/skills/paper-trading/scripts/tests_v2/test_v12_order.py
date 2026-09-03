"""v12 消息挂单链路回归锁（2026-09-03，先红后绿）

契约（.hermes/plans/v12-news-order-20260903.md「契约-ptrade2 侧」）：
- 槽状态机新增 pending_order（挂单中）/ pending_rejudge（弃单待重判），两态占坑保预算
- slot 记录 band_min/band_max/anchor_price/order_ttl/order_id（存量行默认 NULL）
- sleeve-order-place：band=[0.95,1.05]×anchor，槽转 pending_order
- sleeve-order-fill：检测价带内成交→复用 sleeve_fill 成交逻辑建成员段；
  fail-closed：带外拒、无 band 拒、TTL 已过拒——一律不建段
- sleeve-order-expire：expired|band_break → pending_rejudge，预算/槽/坑全保留
- sleeve-order-rejudge：keep=当时现价为新 anchor 刷新 band 重挂；close=关槽回款+信号行清理
- 旧 sleeve-fill（开盘价路径）不得触碰 pending_order/pending_rejudge 槽
- TTL 到期判定：expire --reason expired 只在 order_ttl 已过时合法；band_break 随时可用
隔离：pytest 临时 workspace，价格/ATR 全 mock，零触网，生产库零接触。
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime, timedelta

import pytest
from unittest.mock import patch
from typer.testing import CliRunner

from paper_trading_v2.db import get_connection, migrate_db

runner = CliRunner()


def _db(ws):
    return ws / 'master_pool.db'


def _conn(ws):
    c = get_connection(_db(ws))
    migrate_db(c)
    return c


def _run(app, *args):
    return runner.invoke(app, [str(a) for a in args])


def _iso(dt):
    return dt.isoformat(timespec='seconds')


@pytest.fixture(autouse=True)
def no_network(ws):
    """密闭：价格抓取全 mock（keep 无 --anchor 时的现价触网在此变 None=fail-closed）。"""
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


def _open(ws, stock='测试票', budget=100000, key='ND#900'):
    from paper_trading_v2.cli import app
    r = _run(app, 'sleeve-open', stock, '--budget', budget, '--event-key', key,
             '--code', 'sh600000')
    assert r.exit_code == 0, r.output
    return key


def _quote(px=10.0, high=None, low=None, pre_close=10.0, volume='100000'):
    """E3 行情防线后的 fill 测试件：新鲜带内快照（可变造一字板/停牌）。"""
    from paper_trading_v2.models import StockInfo
    now = datetime.now()
    return StockInfo(code='sh600000', name='测试票', current_price=px,
                     pre_close=pre_close, open_price=px,
                     high=high if high is not None else px + 0.2,
                     low=low if low is not None else px - 0.2, volume=volume,
                     date=now.strftime('%Y-%m-%d'), time=now.strftime('%H:%M:%S'),
                     source='tencent')


def _set_ttl(ws, key='ND#900', dt=None):
    """直改槽 order_ttl（E5 起 place 只收未来交易节收盘，过期场景走 DB 改写模拟）。"""
    c = _conn(ws)
    with c:
        c.execute("UPDATE event_slots SET order_ttl=? WHERE event_key=?",
                  (_iso(dt), key))
    c.close()


def _place(ws, key='ND#900', anchor=10.0, ttl_min=None):
    """挂单：ttl=next_session_close（E5 TTL 真源）；ttl_min 非 None → 挂单后把
    order_ttl 直改到过去（模拟"挂了但已到期"，expire 测试前置）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    ttl = next_session_close().isoformat(timespec='seconds')
    r = _run(app, 'sleeve-order-place', key, '--anchor', anchor, '--ttl', ttl)
    assert r.exit_code == 0, r.output
    if ttl_min is not None:
        _set_ttl(ws, key, datetime.now() + timedelta(minutes=ttl_min))
    return ttl


def _slot(ws, key='ND#900'):
    c = _conn(ws)
    try:
        return c.execute("SELECT * FROM event_slots WHERE event_key=?", (key,)).fetchone()
    finally:
        c.close()


def _free(ws):
    c = _conn(ws)
    try:
        return c.execute("SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
    finally:
        c.close()


def _trades(ws, stock='测试票'):
    c = _conn(ws)
    try:
        seg = c.execute("SELECT id FROM position WHERE stock=? AND status='open' "
                        "AND strategy='NEWS' ORDER BY id DESC", (stock,)).fetchone()
        if not seg:
            return []
        return [dict(r) for r in c.execute(
            "SELECT * FROM trades WHERE account_id=? ORDER BY seq", (seg[0],)).fetchall()]
    finally:
        c.close()


def _cash(ws, stock='测试票'):
    c = _conn(ws)
    try:
        seg = c.execute("SELECT cash FROM position WHERE stock=? AND status='open' "
                        "AND strategy='NEWS' ORDER BY id DESC", (stock,)).fetchone()
        return (seg[0] or 0.0) if seg else None
    finally:
        c.close()


# ============ schema：v11 挂单列 + 存量行兼容 ============

def test_v12_slot_order_columns_exist(pools, env):
    """event_slots 有 band_min/band_max/anchor_price/order_ttl/order_id 列。"""
    c = _conn(env)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(event_slots)").fetchall()}
        assert {'band_min', 'band_max', 'anchor_price', 'order_ttl', 'order_id'} <= cols
    finally:
        c.close()


def test_v12_existing_rows_default_null(pools, env):
    """兼容存量行：旧式直插的槽迁移后挂单列为 NULL，状态不动。"""
    c = _conn(env)
    with c:
        c.execute("INSERT INTO event_slots (event_key, status, opened_at, budget) "
                  "VALUES ('LEGACY#1','open','2026-09-01T09:00:00',1000)")
    c = _conn(env)   # 重开触发 migrate_db 幂等
    try:
        r = c.execute("SELECT status, band_min, band_max, anchor_price, order_ttl, "
                      "order_id FROM event_slots WHERE event_key='LEGACY#1'").fetchone()
        assert r['status'] == 'open'
        assert (r['band_min'], r['band_max'], r['anchor_price'],
                r['order_ttl'], r['order_id']) == (None, None, None, None, None)
    finally:
        c.close()


# ============ place：band 与 pending_order 态 ============

def test_place_sets_band_and_pending_order(pools, env):
    """place：band=[0.95,1.05]×anchor，槽转 pending_order，order_id/ttl 落列，坑保留。"""
    from paper_trading_v2.sleeve_slots import active_slot_count
    _open(env)
    ttl = _place(env, anchor=10.0)
    s = _slot(env)
    assert s['status'] == 'pending_order'
    assert s['fill_status'] == 'pending'          # 仍待成交，旧 fill 认领口径不丢
    assert s['band_min'] == pytest.approx(9.5)
    assert s['band_max'] == pytest.approx(10.5)
    assert s['anchor_price'] == pytest.approx(10.0)
    assert s['order_ttl'] == ttl
    assert s['order_id']
    c = _conn(env)
    try:
        assert active_slot_count(c) == 1          # pending_order 占坑
    finally:
        c.close()
    assert _free(env) == pytest.approx(1900000)   # 开槽拨款在账，place 不动资金


def test_place_rejects_bad_anchor_and_double_place(pools, env):
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    _open(env)
    r = _run(app, 'sleeve-order-place', 'ND#900', '--anchor', '0',
             '--ttl', _iso(datetime.now() + timedelta(minutes=60)))
    assert r.exit_code == 1
    _place(env, anchor=10.0)
    r = _run(app, 'sleeve-order-place', 'ND#900', '--anchor', '11.0',
             '--ttl', next_session_close().isoformat(timespec='seconds'))
    assert r.exit_code == 1                       # 已挂单不得重复挂
    assert _slot(env)['anchor_price'] == pytest.approx(10.0)


# ============ fill：带内成交 / 带外 fail-closed ============

def test_fill_low_band_boundary_creates_member_position(pools, env):
    """0.95 边界（含）成交：检测价建成员段（trades/段现金/保护三件套）。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=9.5)):
        r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '9.5', '--atr', '0.5')
    assert r.exit_code == 0, r.output
    tr = _trades(env)
    assert len(tr) == 1 and tr[0]['operation'] == 'buy'
    assert tr[0]['price'] == pytest.approx(9.5)
    assert tr[0]['quantity'] == int(100000 / 9.5)
    s = _slot(env)
    assert s['status'] == 'open'                  # 成交回主流状态机
    assert s['fill_status'] == 'filled'
    assert _cash(env) == pytest.approx(100000 - tr[0]['quantity'] * 9.5)
    c = _conn(env)
    try:
        seg = c.execute("SELECT id FROM position WHERE stock='测试票' AND "
                        "status='open' AND strategy='NEWS'").fetchone()
        n_cond = c.execute("SELECT COUNT(*) FROM conditions WHERE account_id=? "
                           "AND status='active'", (seg[0],)).fetchone()[0]
        assert n_cond == 2                        # 成本保护+移动止损（既有三件套口径）
    finally:
        c.close()


def test_fill_high_band_boundary_inclusive(pools, env):
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=10.5)):
        r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.5', '--atr', '0.5')
    assert r.exit_code == 0, r.output
    assert len(_trades(env)) == 1


@pytest.mark.parametrize('bad_price', ['9.49', '10.51', '-1', '0'])
def test_fill_outside_band_rejected_no_segment(pools, env, bad_price):
    """fail-closed：带外/脏价 → 拒，不建段、槽不变、段现金分文不动。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', bad_price, '--atr', '0.5')
    assert r.exit_code == 1
    assert _trades(env) == []
    s = _slot(env)
    assert s['status'] == 'pending_order'
    assert s['fill_status'] == 'pending'
    assert _cash(env) == pytest.approx(100000)


def test_fill_requires_pending_order_state(pools, env):
    """未挂单（open/pending 旧态）槽不得走检测价路径。"""
    from paper_trading_v2.cli import app
    _open(env)
    r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 1
    assert _trades(env) == []


def test_fill_after_ttl_rejected(pools, env):
    """TTL 已过的挂单 fail-closed：拒绝成交，须先 expire 弃单。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0, ttl_min=-1)          # ttl 已在过去
    r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 1
    assert _trades(env) == []


def test_legacy_fill_skips_order_slots(pools, env):
    """旧 sleeve-fill（开盘价路径，--allow-same-day）不得成交 pending_order 槽。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    r = _run(app, 'sleeve-fill', '--event-key', 'ND#900', '--allow-same-day',
             '--price', '测试票=9.8', '--atr', '0.5')
    assert r.exit_code == 0, r.output
    assert _trades(env) == []                     # 旧路径没碰挂单槽
    assert _slot(env)['status'] == 'pending_order'


# ============ expire：转 pending_rejudge，预算保留 ============

def test_expire_expired_preserves_budget_slot_and_slot_count(pools, env):
    """TTL 过期弃单：槽转 pending_rejudge，预算/段现金/坑全保留，零回款。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_slots import active_slot_count
    _open(env)
    _place(env, anchor=10.0, ttl_min=-5)          # TTL 已过
    r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired')
    assert r.exit_code == 0, r.output
    s = _slot(env)
    assert s['status'] == 'pending_rejudge'
    assert s['budget'] == pytest.approx(100000)   # 预算保留不关槽
    assert _free(env) == pytest.approx(1900000)   # 未回款（预算冻结在槽）
    assert _cash(env) == pytest.approx(100000)    # 段现金冻结
    c = _conn(env)
    try:
        assert active_slot_count(c) == 1          # 仍占坑
    finally:
        c.close()


def test_expire_band_break_allowed_while_ttl_live(pools, env):
    """E9：band_break 核价通过（现价 9.3 < band_min 9.5）——破带证据在位才允许弃单。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=9.3)):
        r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'band_break')
    assert r.exit_code == 0, r.output
    assert _slot(env)['status'] == 'pending_rejudge'


def test_expire_expired_requires_ttl_due(pools, env):
    """TTL 未到期判定：expired 理由被拒（fail-closed），槽不动。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0, ttl_min=60)
    r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired')
    assert r.exit_code == 1
    assert _slot(env)['status'] == 'pending_order'


def test_expire_due_batch(pools, env):
    """心跳批量：expire_due 只收 TTL 已过挂单，未到期槽不动。"""
    from paper_trading_v2.sleeve_order import SleeveOrder
    _open(env, stock='测试票', key='ND#901')
    _open(env, stock='另一票', budget=50000, key='ND#902')
    _place(env, key='ND#901', ttl_min=-10)
    _place(env, key='ND#902', ttl_min=120)
    done = SleeveOrder(_db(env)).expire_due()
    assert done == ['ND#901']
    assert _slot(env, 'ND#901')['status'] == 'pending_rejudge'
    assert _slot(env, 'ND#902')['status'] == 'pending_order'


# ============ rejudge：keep 刷新重挂 / close 关槽回款 ============

def test_rejudge_keep_refreshes_band_and_replaces(pools, env):
    """keep：带内新锚（-4%，E4 漂移帽内）刷新 band，槽回 pending_order，旧带作废。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    _open(env)
    _place(env, anchor=10.0, ttl_min=-5)
    r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired')
    assert r.exit_code == 0
    new_ttl = next_session_close().isoformat(timespec='seconds')
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep',
             '--anchor', '9.6', '--ttl', new_ttl)
    assert r.exit_code == 0, r.output
    s = _slot(env)
    assert s['status'] == 'pending_order'
    assert s['band_min'] == pytest.approx(9.12)
    assert s['band_max'] == pytest.approx(10.08)
    assert s['anchor_price'] == pytest.approx(9.6)
    assert s['order_ttl'] == new_ttl
    # 旧带价已不可成交（10.5 在新带 [9.12,10.08] 外）；新带 10.08 边界可成交
    r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.5', '--atr', '0.5')
    assert r.exit_code == 1 and _trades(env) == []
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=10.08)):
        r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.08', '--atr', '0.5')
    assert r.exit_code == 0, r.output
    assert len(_trades(env)) == 1


def test_rejudge_keep_without_price_fail_closed(pools, env):
    """keep 拿不到现价（mock 无行情）→ fail-closed，槽保持 pending_rejudge。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0, ttl_min=-5)
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason',
                'expired').exit_code == 0
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep',
             '--ttl', _iso(datetime.now() + timedelta(minutes=60)))
    assert r.exit_code == 1
    assert _slot(env)['status'] == 'pending_rejudge'


def test_rejudge_close_refunds_and_archives_signal(pools, env):
    """close：复用 close-slot 关槽回款 + NEWS 信号行清理（archived）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_slots import active_slot_count
    _open(env)
    _place(env, anchor=10.0, ttl_min=-5)
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason',
                'expired').exit_code == 0
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--close')
    assert r.exit_code == 0, r.output
    s = _slot(env)
    assert s['status'] == 'closed'
    assert _free(env) == pytest.approx(2000000)   # 预算全额回消息池
    c = _conn(env)
    try:
        assert active_slot_count(c) == 0          # 坑释放
        p = c.execute("SELECT pool_status FROM pool WHERE stock='测试票'").fetchone()
        assert p and p['pool_status'] == 'archived'   # 信号行清理
        n_seg = c.execute("SELECT COUNT(*) FROM position WHERE stock='测试票' "
                          "AND status='open' AND strategy='NEWS'").fetchone()[0]
        assert n_seg == 0
    finally:
        c.close()


def test_rejudge_requires_pending_rejudge_state(pools, env):
    """重判只在 pending_rejudge 态合法：挂单中/开槽态直接 rejudge 拒绝。"""
    from paper_trading_v2.cli import app
    _open(env)
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--close')
    assert r.exit_code == 1                       # open 态不可重判
    _place(env, anchor=10.0)
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep', '--anchor', '10.0',
             '--ttl', _iso(datetime.now() + timedelta(minutes=60)))
    assert r.exit_code == 1                       # pending_order 态不可重判
    assert _slot(env)['status'] == 'pending_order'


def test_order_lifecycle_full_money_identity(pools, env):
    """全链闭环：开槽拨款→挂单→过期→重判 close 回款，消息池分文不差。"""
    from paper_trading_v2.cli import app
    _open(env, budget=300000)
    assert _free(env) == pytest.approx(1700000)
    _place(env, anchor=20.0, ttl_min=-5)
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason',
                'expired').exit_code == 0
    assert _free(env) == pytest.approx(1700000)   # 弃单零挪动
    assert _run(app, 'sleeve-order-rejudge', 'ND#900', '--close').exit_code == 0
    assert _free(env) == pytest.approx(2000000)   # 全额回款
