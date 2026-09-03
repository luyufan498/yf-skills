"""v12 补洞回归锁（2026-09-03，先红后绿）——双模型对抗审计 E2/E3/E4/E5/E9/E10/E11

契约（plans/v12-news-order-20260903「双模型对抗审计节」+ CC 任务书 cc-v12-patch-task.md）：
- E2  expire(含 expire_due) 成功转 pending_rejudge → 同事务 INSERT NEWS_REJUDGE 到 tasks.db
      （payload 含 event_key/code/reason/expired_at/note，source='sleeve-order'）
- E3  fill 行情 fail-closed 防线：一字板（high==low≠昨收）拒 / 报价陈旧（>5min 或非今日）拒 /
      停牌标记拒 / 取不到快照拒——宁可不成交不脏成交
- E4  rejudge keep 次数帽（2 次，第 3 次强制 close）+ 漂移限制（新锚偏离原锚 >±5% 拒 keep）；
      event_slots.rejudge_count 列（migrate v12 幂等，keep 每次 +1）
- E5  TTL 真源=挂单时刻后第一个交易节收盘（上午 11:30 / 下午 15:00）：next_session_close
      纯函数锁规则（周五晚→周一 11:30；周一 10:00→11:30；周一 14:00→15:00；节假日跳过）；
      place/keep 校验 ttl 合法否则 fail-closed 拒
- E9  expire(reason=band_break) 必须核价：现价 ≥ band_min 拒（未破带不可弃单），
      确 < band_min 才允许；取不到价 fail-closed 拒
- E10 keep 未带 ttl 自动推 next_session_close（不靠 agent 自觉）
- E11 多成员槽分价：各成员按各自 code 取各自现价成交（各自带内判定），不得单价注入全成员；
      单成员路径不变；某成员取价失败 → 该成员跳过下拍重试（与 fill_pending 部分成交同语义）
隔离：pytest 临时 workspace + STOCK_TASKS_DB=tmp（conftest 护栏），行情全 mock，零触网，
生产库零接触。
"""
import sys, os, json, sqlite3
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


def _tasks_db(ws=None):
    """本测试的 tasks.db（conftest 护栏注入的 STOCK_TASKS_DB）。"""
    return os.environ['STOCK_TASKS_DB']


def _run(app, *args):
    return runner.invoke(app, [str(a) for a in args])


def _iso(dt):
    return dt.isoformat(timespec='seconds')


@pytest.fixture(autouse=True)
def no_network(ws):
    """密闭：现价触网全 mock（keep 无 --anchor 的 fail-closed 路径在此变 None）。"""
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


def _quote(px=10.0, pre_close=10.0, high=None, low=None, volume='100000',
           when=None, code='sh600000', name='测试票'):
    """行情快照 mock 件（E3 防线的四态：正常/一字板/停牌/陈旧）。"""
    from paper_trading_v2.models import StockInfo
    now = when or datetime.now()
    return StockInfo(code=code, name=name, current_price=px, pre_close=pre_close,
                     open_price=px, high=high if high is not None else px + 0.2,
                     low=low if low is not None else px - 0.2, volume=volume,
                     date=now.strftime('%Y-%m-%d'), time=now.strftime('%H:%M:%S'),
                     source='tencent')


# ---------- 开槽 / 挂单 helper（ttl 一律按 E5 真源推导） ----------

def _open(ws, stocks=('测试票',), budget=100000, key='ND#900',
          codes=('sh600000',)):
    from paper_trading_v2.cli import app
    args = ['sleeve-open', *stocks, '--budget', budget, '--event-key', key]
    for c in codes:
        args += ['--code', c]
    r = _run(app, *args)
    assert r.exit_code == 0, r.output
    return key


def _place(ws, key='ND#900', anchor=10.0, ttl=None, past_min=None):
    """挂单：ttl 缺省=next_session_close（E5 真源）；past_min 非 None 时挂单后把
    order_ttl 直改到过去（模拟"挂了但已到期"，place 本身拒收过去 ttl）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    ttl = ttl or next_session_close().isoformat(timespec='seconds')
    r = _run(app, 'sleeve-order-place', key, '--anchor', anchor, '--ttl', ttl)
    assert r.exit_code == 0, r.output
    if past_min is not None:
        _set_ttl(ws, key, datetime.now() + timedelta(minutes=past_min))
    return ttl


def _set_ttl(ws, key='ND#900', dt=None):
    """直改槽 order_ttl（expire 测试前置：place 只收未来交易节收盘）。"""
    c = _conn(ws)
    with c:
        c.execute("UPDATE event_slots SET order_ttl=? WHERE event_key=?",
                  (_iso(dt), key))
    c.close()


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


def _seg_trade(ws, stock):
    """成员段（strategy=NEWS open）的首笔成交行。"""
    c = _conn(ws)
    try:
        seg = c.execute("SELECT id FROM position WHERE stock=? AND status='open' "
                        "AND strategy='NEWS' ORDER BY id DESC", (stock,)).fetchone()
        if not seg:
            return None
        return c.execute("SELECT * FROM trades WHERE account_id=? ORDER BY seq",
                         (seg[0],)).fetchone()
    finally:
        c.close()


def _rejudge_rows(ws=None):
    """tasks.db 的 NEWS_REJUDGE 行（库/表尚未创建 = 零事件，不算错）。"""
    if not os.path.exists(_tasks_db(ws)):
        return []
    t = sqlite3.connect(_tasks_db(ws))
    t.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in t.execute(
            "SELECT * FROM task_events WHERE type='NEWS_REJUDGE' ORDER BY id").fetchall()]
    except sqlite3.OperationalError:
        return []                            # 库在但表未建（从未发射过）= 零事件
    finally:
        t.close()


# ============ E5：next_session_close 纯函数（TTL 真源） ============

def test_e5_next_session_close_friday_evening_to_monday():
    """周五 20:00 挂 → 下周一 11:30。"""
    from paper_trading_v2.sleeve_order import next_session_close
    r = next_session_close(datetime(2026, 9, 4, 20, 0, 0))   # 周五（交易日）晚间
    assert r == datetime(2026, 9, 7, 11, 30, 0)


def test_e5_next_session_close_monday_morning_same_session():
    """周一 10:00 挂 → 当节（上午）11:30。"""
    from paper_trading_v2.sleeve_order import next_session_close
    assert next_session_close(datetime(2026, 9, 7, 10, 0, 0)) == \
        datetime(2026, 9, 7, 11, 30, 0)


def test_e5_next_session_close_monday_afternoon():
    """周一 14:00 挂 → 当日 15:00。"""
    from paper_trading_v2.sleeve_order import next_session_close
    assert next_session_close(datetime(2026, 9, 7, 14, 0, 0)) == \
        datetime(2026, 9, 7, 15, 0, 0)


def test_e5_next_session_close_skips_national_day_holidays():
    """节假日跳过：9/30（周三，交易日）20:00 → 国庆 10/1-10/7 全跳 → 10/8（周四）11:30。"""
    from paper_trading_v2.sleeve_order import next_session_close
    assert next_session_close(datetime(2026, 9, 30, 20, 0, 0)) == \
        datetime(2026, 10, 8, 11, 30, 0)


def test_e5_next_session_close_weekend():
    """周六 10:00 → 周一 11:30。"""
    from paper_trading_v2.sleeve_order import next_session_close
    assert next_session_close(datetime(2026, 9, 5, 10, 0, 0)) == \
        datetime(2026, 9, 7, 11, 30, 0)


def test_e5_next_session_close_holiday_friday():
    """端午休市周五（6/19）盘中 → 下一个交易日周一（6/22）11:30。"""
    from paper_trading_v2.sleeve_order import next_session_close
    assert next_session_close(datetime(2026, 6, 19, 10, 0, 0)) == \
        datetime(2026, 6, 22, 11, 30, 0)


def test_e5_next_session_close_exactly_at_morning_close_is_strictly_after():
    """恰在 11:30 整挂单 → 上午节收盘不算"之后"，下一节 15:00。"""
    from paper_trading_v2.sleeve_order import next_session_close
    assert next_session_close(datetime(2026, 9, 7, 11, 30, 0)) == \
        datetime(2026, 9, 7, 15, 0, 0)


# ============ E5：place/keep 的 ttl 校验（fail-closed） ============

def test_e5_place_rejects_non_session_close_ttl(pools, env):
    """ttl 不是未来交易节收盘（如 now+60min）→ 拒，槽不动。"""
    from paper_trading_v2.cli import app
    _open(env)
    bad = (datetime.now() + timedelta(minutes=60)).isoformat(timespec='seconds')
    r = _run(app, 'sleeve-order-place', 'ND#900', '--anchor', '10.0', '--ttl', bad)
    assert r.exit_code == 1
    s = _slot(env)
    assert s['status'] == 'open' and s['order_id'] is None    # 未挂上


def test_e5_place_accepts_next_session_close_ttl(pools, env):
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    _open(env)
    ttl = next_session_close().isoformat(timespec='seconds')
    r = _run(app, 'sleeve-order-place', 'ND#900', '--anchor', '10.0', '--ttl', ttl)
    assert r.exit_code == 0, r.output
    assert _slot(env)['order_ttl'] == ttl


def test_e10_rejudge_keep_without_ttl_auto_derives(pools, env):
    """E10：keep 未带 --ttl → 自动推下一交易节收盘，不拒。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    _open(env)
    _place(env, anchor=10.0, past_min=-5)
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason',
                'expired').exit_code == 0
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep', '--anchor', '10.2')
    assert r.exit_code == 0, r.output
    s = _slot(env)
    assert s['status'] == 'pending_order'
    assert s['order_ttl'] == next_session_close().isoformat(timespec='seconds')


# ============ E2：expire 同事务发 NEWS_REJUDGE ============

def test_e2_expire_emits_news_rejudge(pools, env):
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0, past_min=-5)
    r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired')
    assert r.exit_code == 0, r.output
    rows = _rejudge_rows(env)
    assert len(rows) == 1
    row = rows[0]
    assert row['entity'] == 'ND#900'
    assert row['source'] == 'sleeve-order'
    p = json.loads(row['payload'])
    assert p['event_key'] == 'ND#900'
    assert p['code'] == 'sh600000'                  # 首成员代码（消费方取价用）
    assert p['reason'] == 'expired'
    assert p.get('expired_at')
    assert 'note' in p


def test_e2_rejected_expire_emits_nothing(pools, env):
    """fail-closed：未到期 expired 弃单被拒 → 不得有 NEWS_REJUDGE（防假信号）。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)                        # ttl=未来交易节收盘
    r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired')
    assert r.exit_code == 1
    assert _rejudge_rows(env) == []


def test_e2_expire_due_emits_one_rejudge_per_slot(pools, env):
    from paper_trading_v2.sleeve_order import SleeveOrder
    _open(env, stocks=('测试票',), budget=100000, key='ND#901')
    _open(env, stocks=('另一票',), budget=50000, key='ND#902')
    _place(env, key='ND#901', anchor=10.0, past_min=-10)
    _place(env, key='ND#902', anchor=20.0, past_min=-10)
    done = SleeveOrder(_db(env)).expire_due()
    assert sorted(done) == ['ND#901', 'ND#902']
    rows = _rejudge_rows(env)
    assert sorted(r['entity'] for r in rows) == ['ND#901', 'ND#902']
    assert all(r['source'] == 'sleeve-order' for r in rows)


def test_e2_band_break_expire_emits_rejudge_with_reason(pools, env):
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=9.3)):
        r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'band_break')
    assert r.exit_code == 0, r.output
    rows = _rejudge_rows(env)
    assert len(rows) == 1 and json.loads(rows[0]['payload'])['reason'] == 'band_break'


# ============ E3：fill 行情防线（宁可不成交不脏成交） ============

def test_e3_fill_rejects_when_no_quote_snapshot(pools, env):
    """取不到行情快照 → 拒，不建段（fail-closed）。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=None):
        r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 1
    assert _seg_trade(env, '测试票') is None


def test_e3_fill_rejects_limit_board(pools, env):
    """一字板（当日 high==low 且 ≠昨收）→ 拒：买不到的票不按检测价成交。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=10.0, high=10.0, low=10.0, pre_close=9.1)):
        r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 1
    assert _seg_trade(env, '测试票') is None
    assert _slot(env)['status'] == 'pending_order'


def test_e3_fill_rejects_suspended(pools, env):
    """显式停牌标记（volume=0）→ 拒。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=10.0, volume='0')):
        r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 1
    assert _seg_trade(env, '测试票') is None


def test_e3_fill_rejects_stale_quote(pools, env):
    """报价时间戳陈旧（非今日 / >5 分钟前）→ 拒。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    stale_day = _quote(px=10.0, when=datetime.now() - timedelta(days=1))
    stale_min = _quote(px=10.0, when=datetime.now() - timedelta(minutes=6))
    for q in (stale_day, stale_min):
        with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
                   return_value=q):
            r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.0', '--atr', '0.5')
        assert r.exit_code == 1, f"{q.date} {q.time} 应判陈旧拒单"
        assert _seg_trade(env, '测试票') is None


def test_e3_fill_fresh_quote_in_band_fills_normally(pools, env):
    """正常新鲜带内报价 → 照常成交（mock 真实行情源路径，E3 不误伤）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.models import StockInfo
    _open(env)
    _place(env, anchor=10.0)
    fresh = _quote(px=10.0)
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=fresh):
        r = _run(app, 'sleeve-order-fill', 'ND#900', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 0, r.output
    tr = _seg_trade(env, '测试票')
    assert tr is not None and tr['price'] == pytest.approx(10.0)
    assert _slot(env)['status'] == 'open'


# ============ E9：band_break 核价（未破带不可弃单） ============

def test_e9_band_break_with_in_band_price_rejected(pools, env):
    """现价 ≥ band_min（未破带）→ band_break 拒，槽保持挂单。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)                        # band [9.5, 10.5]
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=10.0)):
        r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'band_break')
    assert r.exit_code == 1
    assert _slot(env)['status'] == 'pending_order'


def test_e9_band_break_below_band_min_allowed(pools, env):
    """现价确 < band_min → band_break 通过，槽转 pending_rejudge。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=_quote(px=9.3)):
        r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'band_break')
    assert r.exit_code == 0, r.output
    assert _slot(env)['status'] == 'pending_rejudge'


def test_e9_band_break_without_price_fail_closed(pools, env):
    """取不到现价 → band_break 拒（fail-closed，不得凭空弃单）。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0)
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               return_value=None):
        r = _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'band_break')
    assert r.exit_code == 1
    assert _slot(env)['status'] == 'pending_order'


# ============ E4：keep 次数帽 + 漂移限制 ============

def test_e4_rejudge_count_column_and_migrate_idempotent(pools, env):
    """rejudge_count 列存在、存量行默认 0、migrate 幂等（重开连接不加错）。"""
    c = _conn(env)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(event_slots)").fetchall()}
        assert 'rejudge_count' in cols
        assert c.execute("SELECT version FROM schema_meta").fetchone()[0] == 12
    finally:
        c.close()
    with get_connection(_db(env)) as c0:
        c0.execute("INSERT INTO event_slots (event_key, status, opened_at, budget) "
                   "VALUES ('LEGACY#1','open','2026-09-01T09:00:00',1000)")
    c = _conn(env)                                  # 重开触发 migrate（幂等）
    try:
        assert c.execute("SELECT rejudge_count FROM event_slots "
                         "WHERE event_key='LEGACY#1'").fetchone()[0] == 0
    finally:
        c.close()


def test_e4_keep_increments_count(pools, env):
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0, past_min=-5)
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired').exit_code == 0
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep', '--anchor', '10.2')
    assert r.exit_code == 0, r.output
    assert _slot(env)['rejudge_count'] == 1


def test_e4_third_keep_forces_close(pools, env):
    """keep 已达 2 次 → 第 3 次 keep 强制 close（返回 close 语义 + 超限注记）。"""
    from paper_trading_v2.cli import app
    _open(env, budget=100000)
    _place(env, anchor=10.0, past_min=-5)
    for i, anchor in enumerate(('10.1', '10.2'), start=1):
        # keep 重挂后 ttl 回到未来交易节收盘——expire 前须再拨回过去（模拟到期）
        _set_ttl(env, 'ND#900', datetime.now() + timedelta(minutes=-5))
        assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason',
                    'expired').exit_code == 0
        r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep', '--anchor', anchor)
        assert r.exit_code == 0, r.output
        assert _slot(env)['rejudge_count'] == i
    # 第 3 次 keep：帽触发 → 强制 close
    _set_ttl(env, 'ND#900', datetime.now() + timedelta(minutes=-5))
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired').exit_code == 0
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep', '--anchor', '10.3')
    assert r.exit_code == 0, r.output
    assert '重判超限' in r.output
    s = _slot(env)
    assert s['status'] == 'closed'
    assert s['rejudge_count'] == 2                  # 帽内次数，不再递增
    assert _free(env) == pytest.approx(2000000)     # 关槽回款


def test_e4_keep_drift_beyond_5pct_rejected(pools, env):
    """新 anchor 相对原 anchor 漂移 >±5%（带无交集）→ 拒 keep，只能 close。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0, past_min=-5)
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired').exit_code == 0
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep', '--anchor', '12.0')
    assert r.exit_code == 1
    s = _slot(env)
    assert s['status'] == 'pending_rejudge'         # 槽不动
    assert s['rejudge_count'] == 0
    assert s['anchor_price'] == pytest.approx(10.0)  # 旧带保留


def test_e4_keep_within_5pct_drift_allowed(pools, env):
    """带内小幅重锚（+3%）→ keep 通过。"""
    from paper_trading_v2.cli import app
    _open(env)
    _place(env, anchor=10.0, past_min=-5)
    assert _run(app, 'sleeve-order-expire', 'ND#900', '--reason', 'expired').exit_code == 0
    r = _run(app, 'sleeve-order-rejudge', 'ND#900', '--keep', '--anchor', '10.3')
    assert r.exit_code == 0, r.output
    s = _slot(env)
    assert s['anchor_price'] == pytest.approx(10.3)
    assert s['band_min'] == pytest.approx(9.785)
    assert s['band_max'] == pytest.approx(10.815)


# ============ E11：多成员槽分价（各自检测价，不得单价注入全成员） ============

def test_e11_multi_member_each_fills_at_own_price(pools, env):
    """双成员各自价成交：成员 A@9.8、成员 B@10.2（mock 两价），不得同一价。"""
    from paper_trading_v2.cli import app
    _open(env, stocks=('测试票', '次票'), budget=200000, key='ND#910',
          codes=('sh600000', 'sz000001'))
    _place(env, key='ND#910', anchor=10.0)
    quotes = {'sh600000': _quote(px=9.8, code='sh600000', name='测试票'),
              'sz000001': _quote(px=10.2, code='sz000001', name='次票')}
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               side_effect=lambda code: quotes.get(code)):
        r = _run(app, 'sleeve-order-fill', 'ND#910', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 0, r.output
    ta, tb = _seg_trade(env, '测试票'), _seg_trade(env, '次票')
    assert ta is not None and ta['price'] == pytest.approx(9.8)
    assert tb is not None and tb['price'] == pytest.approx(10.2)
    assert _slot(env, 'ND#910')['status'] == 'open'


def test_e11_multi_member_quote_fail_skips_that_member(pools, env):
    """某成员取价失败 → 该成员跳过下拍重试（另一成员照常成交），槽保持挂单态。"""
    from paper_trading_v2.cli import app
    _open(env, stocks=('测试票', '次票'), budget=200000, key='ND#911',
          codes=('sh600000', 'sz000001'))
    _place(env, key='ND#911', anchor=10.0)
    quotes = {'sh600000': _quote(px=9.9, code='sh600000', name='测试票'),
              'sz000001': None}                     # B 取不到价
    with patch('paper_trading_v2.sleeve_order.SleeveOrder._fetch_quote',
               side_effect=lambda code: quotes.get(code)):
        r = _run(app, 'sleeve-order-fill', 'ND#911', '--price', '10.0', '--atr', '0.5')
    assert r.exit_code == 1                        # 有成员未成交 → 非全绿
    ta, tb = _seg_trade(env, '测试票'), _seg_trade(env, '次票')
    assert ta is not None and ta['price'] == pytest.approx(9.9)
    assert tb is None                              # B 未建段，等下一拍
    assert _slot(env, 'ND#911')['status'] == 'pending_order'
