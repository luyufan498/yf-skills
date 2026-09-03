"""v12-patch D 段补洞回归锁（watch_scan/taskbus 侧，先红后绿）

- E1  --scope price：扫 event_slots status='pending_order' 挂单槽（不是 legacy 的
      fill_status='pending' AND status IN ('open','partial')），每槽 fetch_price_any
      取现价 → 四态输出行：带内→sleeve-order-fill / 破带→--reason band_break /
      超 TTL→--reason expired / 取价失败→明确失败行（不静默，不成交不弃单）
- E12 price scope 交易时段闸精确：交易日 + 9:30-11:30 / 13:00-14:57
      （不含 9:00-9:29 集合竞价、不含 14:58+ 收盘集合竞价）
- E6  保护链扫描承接 legacy check_price_triggers（全账户扫，含 strategy='NEWS'
      成员段）→ WATCH_ALERT 落 taskbus 供 C1 心跳消费
- E13 NEWS_CANDIDATE 去重放宽：同 event_key 仅 failed 记录 → 允许重检重发；
      done / 槽已开 仍不重发（防双开）
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

_REAL_DT = datetime
WS_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_watch_scan():
    import importlib.util
    path = os.path.abspath(os.path.join(
        WS_ROOT, '..', '..', '..', 'task-bus', 'scripts', 'watch_scan.py'))
    spec = importlib.util.spec_from_file_location('watch_scan_test_price', path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _db(ws):
    return ws / 'master_pool.db'


def _run(app, *args):
    from typer.testing import CliRunner
    return CliRunner().invoke(app, [str(a) for a in args])


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def set(self, dt):
        self.dt = dt


@pytest.fixture
def clock():
    return Clock(_REAL_DT(2026, 9, 3, 10, 0))


@pytest.fixture(autouse=True)
def no_network(ws):
    from unittest.mock import patch as _patch
    with _patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
                return_value=None):
        yield


@pytest.fixture
def env(ws, monkeypatch):
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', str(ws))
    return ws


@pytest.fixture
def sleeve_ready(env):
    from paper_trading_v2.cli import app
    _run(app, 'master-pool-init', '--amount', '10000000')
    _run(app, 'sleeve-pool-init', '--amount', '2000000')
    return env


@pytest.fixture
def ws_scan(env, monkeypatch, tmp_path, clock):
    """watch_scan 指向 tmp：POOL_DB/TASKS_DB/NEWS_DB=tmp，日历恒交易日，时钟注入。"""
    mod = _load_watch_scan()
    tasks = tmp_path / 'tasks' / 'tasks.db'
    monkeypatch.setattr(mod, 'POOL_DB', str(_db(env)))
    monkeypatch.setattr(mod, 'TASKS_DB', str(tasks))
    monkeypatch.setattr(mod, 'NEWS_DB', str(env / 'news.db'))
    monkeypatch.setattr(mod, 'is_trading_day', lambda d=None: True)

    class _DT(_REAL_DT):
        now = staticmethod(lambda: clock.dt)

    monkeypatch.setattr(mod, 'datetime', _DT)
    return mod, tasks


def _open_slot(ws, stock, key, code):
    from paper_trading_v2.cli import app
    r = _run(app, 'sleeve-open', stock, '--budget', 100000, '--event-key', key,
             '--code', code)
    assert r.exit_code == 0, r.output


def _place_slot(ws, stock, key, code, anchor=10.0):
    """真实 C 段链路造挂单槽：sleeve-open 开槽 → sleeve-order-place 挂单。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    _open_slot(ws, stock, key, code)
    ttl = next_session_close().isoformat(timespec='seconds')
    r = _run(app, 'sleeve-order-place', key, '--anchor', anchor, '--ttl', ttl)
    assert r.exit_code == 0, r.output
    return key


# ---------- E1：pending_order 槽四态扫描 ----------

def test_price_scope_four_state_lines(sleeve_ready, ws_scan, monkeypatch):
    """E1 四态：带内→fill 行（带检测价）；破带→band_break 行；超 TTL→expired 行
    （现价在带内也必须过期优先）；取价失败→明确失败行，不带成交/弃单命令。
    legacy 槽（open+fill_status='pending' 未挂单）不进本扫描。"""
    mod, tasks = ws_scan
    _place_slot(sleeve_ready, '带内票', 'ND#801', 'sh600801')
    _place_slot(sleeve_ready, '破带票', 'ND#802', 'sh600802')
    _place_slot(sleeve_ready, '过期票', 'ND#803', 'sh600803')
    _place_slot(sleeve_ready, '无价票', 'ND#804', 'sh600804')
    _open_slot(sleeve_ready, '旧槽票', 'ND#805', 'sh600805')   # legacy pending，无挂单
    conn = sqlite3.connect(_db(sleeve_ready))
    with conn:
        conn.execute("UPDATE event_slots SET order_ttl='2026-09-03T09:30:00' "
                     "WHERE event_key='ND#803'")   # 已过（时钟 10:00）——TTL 优先于带内价
    conn.close()
    prices = {'sh600801': 10.2, 'sh600802': 9.1, 'sh600803': 10.2}
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: prices.get(code))
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    lines = mod.check_price_orders()
    fill = next(l for l in lines if 'ND#801' in l)
    assert 'sleeve-order-fill' in fill and '--price 10.20' in fill, fill
    brk = next(l for l in lines if 'ND#802' in l)
    assert 'sleeve-order-expire' in brk and '--reason band_break' in brk, brk
    exp = next(l for l in lines if 'ND#803' in l)
    assert 'sleeve-order-expire' in exp and '--reason expired' in exp, exp
    fail = next(l for l in lines if 'ND#804' in l)
    assert '取价失败' in fail, fail
    assert 'sleeve-order-fill' not in fail and 'sleeve-order-expire' not in fail, fail
    assert not any('ND#805' in l for l in lines), lines        # legacy 槽不进 price 扫


def test_price_scope_above_band_silent(sleeve_ready, ws_scan, monkeypatch):
    """现价 > band_max：未触带未破带（挂单等回落），无动作不输出——TTL 到期再走
    expired（E1 契约四态之外的静默态，防每拍空唤醒）。"""
    mod, tasks = ws_scan
    _place_slot(sleeve_ready, '高高票', 'ND#807', 'sh600807')
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 12.0)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    assert mod.check_price_orders() == []


def test_price_scope_band_missing_fail_closed(sleeve_ready, ws_scan, monkeypatch):
    """pending_order 槽 band 缺失（异常态）→ fail-closed 行，不成交不弃单。"""
    mod, tasks = ws_scan
    _place_slot(sleeve_ready, '缺带票', 'ND#808', 'sh600808')
    conn = sqlite3.connect(_db(sleeve_ready))
    with conn:
        conn.execute("UPDATE event_slots SET band_min=NULL, band_max=NULL "
                     "WHERE event_key='ND#808'")
    conn.close()
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 10.2)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    lines = mod.check_price_orders()
    assert any('ND#808' in l and 'fail-closed' in l for l in lines), lines
    assert not any('ND#808' in l and 'sleeve-order-' in l for l in lines), lines


# ---------- E12：price scope 交易时段闸 ----------

def test_price_scope_gate_blocked_times(sleeve_ready, ws_scan, clock, monkeypatch):
    """E12：9:00-9:29 集合竞价 / 11:45 午休 / 14:58+ 收盘集合竞价 / 盘后 → 不扫描
    （集合竞价伪价不进挂单检测）。"""
    mod, tasks = ws_scan
    _place_slot(sleeve_ready, '闸票', 'ND#810', 'sh600810')
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 10.2)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    for hh, mm in [(9, 0), (9, 29), (11, 45), (14, 58), (15, 1), (20, 0)]:
        clock.set(_REAL_DT(2026, 9, 3, hh, mm))
        assert mod.check_price_orders() == [], (hh, mm)


def test_price_scope_gate_open_windows(sleeve_ready, ws_scan, clock, monkeypatch):
    """E12：9:30-11:30 / 13:00-14:57 开闸（端点含）。"""
    mod, tasks = ws_scan
    _place_slot(sleeve_ready, '闸票', 'ND#810', 'sh600810')
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 10.2)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    for hh, mm in [(9, 30), (10, 0), (11, 30), (13, 0), (14, 57)]:
        clock.set(_REAL_DT(2026, 9, 3, hh, mm))
        lines = mod.check_price_orders()
        assert any('ND#810' in l for l in lines), (hh, mm, lines)


def test_price_scope_gate_non_trading_day(sleeve_ready, ws_scan, clock, monkeypatch):
    """E12：非交易日（周末/节假日）→ 不扫描。"""
    mod, tasks = ws_scan
    _place_slot(sleeve_ready, '闸票', 'ND#810', 'sh600810')
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 10.2)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    monkeypatch.setattr(mod, 'is_trading_day', lambda d=None: False)
    clock.set(_REAL_DT(2026, 9, 3, 10, 0))
    assert mod.check_price_orders() == []


# ---------- E6：保护链扫描（含 NEWS 成员段） ----------

def test_price_scope_protection_chain_news_segment(sleeve_ready, ws_scan, monkeypatch):
    """E6：NEWS 成员段 active 保护条件价格穿越 → 触发行 + WATCH_ALERT 落 taskbus
    （承接 legacy check_price_triggers 全账户扫——成交后保护链归属不悬空）。"""
    mod, tasks = ws_scan
    _open_slot(sleeve_ready, '保护票', 'ND#806', 'sh600806')
    conn = sqlite3.connect(_db(sleeve_ready))
    pid = conn.execute("SELECT id FROM position WHERE stock='保护票'").fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO conditions (account_id, type, name, action, price, status, "
            "category, is_event) VALUES (?,?,?,?,?,?,?,?)",
            (pid, 'cost_protection', '成本保护-5%', '跌破成本保护线清仓', 9.0,
             'active', 'hard', 0))
    conn.close()
    monkeypatch.setattr(mod, 'fetch_price', lambda code: 8.5)
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: None)
    lines = mod.check_price_triggers()
    assert any('保护票' in l and 'SELL' in l for l in lines), lines
    tconn = sqlite3.connect(tasks)
    n = tconn.execute("SELECT COUNT(*) FROM task_events WHERE type='WATCH_ALERT' "
                      "AND entity='保护票'").fetchone()[0]
    tconn.close()
    assert n == 1, n


# ---------- E13：NEWS_CANDIDATE 去重放宽（failed 重发） ----------

@pytest.fixture
def scan_newsdb(ws, monkeypatch):
    """newsdb 最小三表（events/messages/event_stock）+ env 注入。"""
    p = ws / 'news.db'
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT,
            importance INTEGER, entity_type TEXT, status TEXT, created_at TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, event_id INTEGER,
            signal_direction TEXT);
        CREATE TABLE event_stock (id INTEGER PRIMARY KEY, event_id INTEGER,
            stock_code TEXT, relevance REAL);
    """)
    c.commit()
    c.close()
    monkeypatch.setenv('STOCK_NEWS_DB', str(p))
    return p


def _seed_news_event(newsdb, event_id):
    """imp=5 open bullish 事件（created_at=真此刻，24h 窗口内）。"""
    c = sqlite3.connect(newsdb)
    with c:
        c.execute("INSERT INTO events (id,title,importance,entity_type,status,"
                  "created_at) VALUES (?,?,?,?,?,?)",
                  (event_id, f'事件{event_id}', 5, 'stock', 'open',
                   _REAL_DT.now().strftime('%Y-%m-%d %H:%M:%S')))
        c.execute("INSERT INTO messages (event_id, signal_direction) "
                  "VALUES (?, 'bullish')", (event_id,))
        c.execute("INSERT INTO event_stock (event_id, stock_code, relevance) "
                  "VALUES (?,?,1)", (event_id, 'sh600900'))
    c.close()


def _cand_row(conn, key, status):
    with conn:
        conn.execute(
            "INSERT INTO task_events (type, entity, source, priority, payload, status) "
            "VALUES ('NEWS_CANDIDATE', ?, 'watch-scan-news', 1, ?, ?)",
            (key, json.dumps({"event_key": key}, ensure_ascii=False), status))


def _cand_count(tasks, key):
    c = sqlite3.connect(tasks)
    try:
        return c.execute("SELECT COUNT(*) FROM task_events WHERE type='NEWS_CANDIDATE' "
                         "AND entity=?", (key,)).fetchone()[0]
    finally:
        c.close()


def test_news_candidate_failed_allows_reemit(sleeve_ready, ws_scan, scan_newsdb,
                                             monkeypatch):
    """E13：同 event_key 仅有 failed 记录 → 放行重检重发（新 NEWS_CANDIDATE 入队），
    kv 留痕不封死（旧逻辑任意状态都拦 → 消费失败永久封死一条消息链路）。"""
    mod, tasks = ws_scan
    _seed_news_event(scan_newsdb, 910)
    mod._ensure_task_table()
    conn = sqlite3.connect(tasks)
    _cand_row(conn, 'ND#910', 'failed')
    conn.close()
    kv = mod._kv_get(mod.NEWS_SCAN_STATE_KEY)
    kv['emitted'] = ['ND#910']
    mod._kv_set(mod.NEWS_SCAN_STATE_KEY, kv)
    assert mod._news_already_emitted('ND#910', {'ND#910'}) is False
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 10.0)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    out = mod.check_news_events()
    assert _cand_count(tasks, 'ND#910') == 2, out     # failed 行保留 + 新 pending 行


def test_news_candidate_done_or_slotted_not_reemitted(sleeve_ready, ws_scan,
                                                       scan_newsdb, monkeypatch):
    """E13 反向：done 记录 → 不重发；槽已开（event_key 入池）→ 不重发（防双开）。"""
    mod, tasks = ws_scan
    _seed_news_event(scan_newsdb, 911)
    _seed_news_event(scan_newsdb, 912)
    mod._ensure_task_table()
    conn = sqlite3.connect(tasks)
    _cand_row(conn, 'ND#911', 'done')
    conn.close()
    _open_slot(sleeve_ready, '槽票', 'ND#912', 'sh600912')
    assert mod._news_already_emitted('ND#911', set()) is True
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 10.0)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    mod.check_news_events()
    assert _cand_count(tasks, 'ND#911') == 1          # done → 不新增
    assert _cand_count(tasks, 'ND#912') == 0          # 槽已开 → 不检出


# ---------- scope 分流 ----------

def test_scope_price_dispatch(sleeve_ready, ws_scan, monkeypatch, capsys):
    """--scope price → run_price_scope：E1 [PRICE-ORDER] 行 + E6 保护链行同拍输出。"""
    mod, tasks = ws_scan
    _place_slot(sleeve_ready, '带内票', 'ND#801', 'sh600801')
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: 10.2)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    monkeypatch.setattr(sys, 'argv', ['watch_scan.py', '--scope', 'price'])
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert '[PRICE-ORDER]' in out and 'sleeve-order-fill' in out, out


def test_scope_price_idle(sleeve_ready, ws_scan, monkeypatch, capsys):
    """无挂单槽且无条件触发 → IDLE（稳定睡眠）。"""
    mod, tasks = ws_scan
    monkeypatch.setattr(mod, 'fetch_price_any', lambda code: None)
    monkeypatch.setattr(mod, 'fetch_price', lambda code: None)
    monkeypatch.setattr(sys, 'argv', ['watch_scan.py', '--scope', 'price'])
    rc = mod.main()
    out = capsys.readouterr().out.strip()
    assert rc == 0 and out == 'IDLE', out


def test_scope_invalid_still_rejected(sleeve_ready, ws_scan, monkeypatch, capsys):
    mod, tasks = ws_scan
    monkeypatch.setattr(sys, 'argv', ['watch_scan.py', '--scope', 'bogus'])
    rc = mod.main()
    assert rc == 2
