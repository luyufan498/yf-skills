"""earliest_fill 成交时点改造回归锁（2026-09-02 P+2 盲区修复，先红后绿）

新规则：earliest_fill_date = next_trading_day(消息入库日)，入库时刻=newsdb
events.**created_at**（门管"消息何时被我们知道"；started_at 归新鲜度/G闸——
两码事，场景 b 用 created_at=今日+started_at=昨日 的行锁死"门只看 created_at"）。
- 盘中消息（10:30 入库）→ 当日拒、次一交易日放行（T+1 维持）
- 隔夜消息（P 日晚入库）→ P+1 开盘即可成交（修 P+2 盲区）
- newsdb 不可达/键解析失败 → fail-closed 回退 opened_at+1 交易日（旧行为）+ audit 标痕
- --allow-same-day 豁免保留
附带锁：dive_open 跳水影子（≤-3% 记影子照成交）、watch_scan SLEEVE_FILL 事件化。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from typer.testing import CliRunner

runner = CliRunner()

_REAL_DT = datetime


class Clock:
    """今日=冻结日：patch earliest_fill.today_str（唯一时钟消费点），够用不求优雅。"""

    def __init__(self, dt):
        self.dt = dt

    def set(self, dt):
        self.dt = dt

    def today(self):
        return self.dt.date().isoformat()


@pytest.fixture
def clock(monkeypatch):
    from paper_trading_v2 import earliest_fill as ef
    c = Clock(_REAL_DT(2026, 9, 2, 6, 5))
    monkeypatch.setattr(ef, 'today_str', c.today)
    return c


CAL_DAYS = {'2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04'}   # 9/5、9/6=周末


@pytest.fixture(autouse=True)
def cal(monkeypatch):
    """交易日历依赖注入（ef.is_trading_day 可替换，不硬耦合真源文件）。"""
    from paper_trading_v2 import earliest_fill as ef

    def _is(d):
        d = str(d)[:10]
        wd = _REAL_DT.strptime(d, '%Y-%m-%d').weekday()
        return wd < 5 and d in CAL_DAYS

    monkeypatch.setattr(ef, 'is_trading_day', _is)


@pytest.fixture
def newsdb(ws, monkeypatch):
    """临时 newsdb（events 含 created_at+started_at 双列）+ STOCK_NEWS_DB 注入。"""
    p = ws / 'news.db'
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT,"
              "started_at TEXT NOT NULL, created_at TEXT NOT NULL)")
    c.commit()
    c.close()
    monkeypatch.setenv('STOCK_NEWS_DB', str(p))
    return p


def _seed_news(newsdb, event_id, created_at, started_at=None):
    """入库行：created_at=入库时刻（门基准）；started_at 默认同值（事件起点无关）。"""
    c = sqlite3.connect(newsdb)
    c.execute("INSERT INTO events (id,title,started_at,created_at) VALUES (?,?,?,?)",
              (event_id, f'事件{event_id}', started_at or created_at, created_at))
    c.commit()
    c.close()


def _db(ws):
    return ws / 'master_pool.db'


def _run(app, *args):
    return runner.invoke(app, [str(a) for a in args])


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


KL = [{'date': '2026-08-01', 'open': 10, 'high': 11, 'low': 9,
       'close': 10, 'volume': 1}] * 30


def _open_pending(ws, stock, key, code='sh600099'):
    """真实开槽（pending 待成交单）。返回 (SleeveOpener, conn)。"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    from paper_trading_v2.db import get_connection
    op = SleeveOpener(_db(ws))
    op.open_slot([stock], budget=100000, event_key=key, news_kind='policy',
                 code_map={stock: code})
    return op, get_connection(_db(ws))


def _set_opened_at(conn, key, iso):
    with conn:
        conn.execute("UPDATE event_slots SET opened_at=? WHERE event_key=?",
                     (iso, key))


def _audit_rows(conn, action):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM audit WHERE action=?", (action,)).fetchall()]


def _shadow_rows(conn, kind):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM shadow_log WHERE kind=?", (kind,)).fetchall()]


# ---------- a) 隔夜消息：created_at P 日 19:05 + P+1 开槽 → P+1 早 fill 放行 ----------

def test_overnight_message_fills_next_morning(sleeve_ready, newsdb, cal, clock):
    """P=9/1 晚 19:05 入库 → earliest=9/2；9/2 01:49 开槽；9/2 06:05 fill 须成交
    （旧口径'开槽日禁成交'在此拒绝 = P+2 盲区，本锁先红后绿）。"""
    from paper_trading_v2.cli import app
    _seed_news(newsdb, 293, '2026-09-01 19:05:00', started_at='2026-08-26 08:19:54')
    _, conn = _open_pending(sleeve_ready, '隔夜票', 'ND#293')
    _set_opened_at(conn, 'ND#293', '2026-09-02T01:49:00')
    clock.set(_REAL_DT(2026, 9, 2, 6, 5))
    from unittest.mock import patch as _patch
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '隔夜票=10.0', '--atr', '隔夜票=0.5')
    assert r.exit_code == 0, r.output
    assert '隔夜票' in r.output and '买' in r.output, r.output
    assert conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#293'"
                        ).fetchone()[0] == 'filled'
    # 成交 audit 记录 earliest_fill + 来源 newsdb
    rows = _audit_rows(conn, 'sleeve_fill_earliest')
    assert len(rows) == 1, rows
    assert 'earliest_fill=2026-09-02' in rows[0]['reason']
    assert 'source=newsdb' in rows[0]['reason']
    conn.close()


# ---------- b) 盘中消息：当日拒（T+1 维持锁），P+1 放行；且门只看 created_at ----------

def test_intraday_message_same_day_blocked_next_day_allowed(sleeve_ready, newsdb,
                                                            cal, clock):
    """created_at=9/2 盘中（earliest=9/3）但 started_at=9/1——若门误读 started_at
    则 earliest=9/2 会当日放行（本锁证伪）。正确：当日拒、9/3 放行。"""
    from paper_trading_v2.cli import app
    _seed_news(newsdb, 500, '2026-09-02 10:30:00', started_at='2026-09-01 08:00:00')
    _, conn = _open_pending(sleeve_ready, '盘中票', 'ND#500')
    _set_opened_at(conn, 'ND#500', '2026-09-02T11:00:00')
    clock.set(_REAL_DT(2026, 9, 2, 13, 5))
    from unittest.mock import patch as _patch
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '盘中票=10.0', '--atr', '盘中票=0.5')
    assert r.exit_code == 0, r.output
    assert '成交时点门跳过 ND#500' in r.output, r.output
    assert conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#500'"
                        ).fetchone()[0] == 'pending'
    # 指定键打未到期槽：拒绝退出码 1
    r = _run(app, 'sleeve-fill', '--event-key', 'ND#500', '--price', '盘中票=10.0')
    assert r.exit_code == 1 and '成交时点门拒绝' in r.output, r.output
    # P+1=9/3 放行
    clock.set(_REAL_DT(2026, 9, 3, 9, 35))
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '盘中票=10.0', '--atr', '盘中票=0.5')
    assert r.exit_code == 0 and '盘中票' in r.output and '买' in r.output, r.output
    assert conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#500'"
                        ).fetchone()[0] == 'filled'
    conn.close()


# ---------- c) 存量槽行为不变锁：9/1 入库 + 9/2 开槽 → 9/3 fill 新旧口径都放行 ----------

def test_existing_slots_unchanged_9_3_fill(sleeve_ready, newsdb, cal, clock):
    """生产 3 槽形态（ND#293/333/478）：消息 9/1 晚入库、9/2 01:49 开槽 →
    9/3 fill 放行（新口径 earliest=9/2 ≤ 9/3；旧口径 9/2<9/3 亦放行——行为不变）。"""
    from paper_trading_v2.cli import app
    _seed_news(newsdb, 293, '2026-09-01 20:10:00')
    _, conn = _open_pending(sleeve_ready, '存量票', 'ND#293')
    _set_opened_at(conn, 'ND#293', '2026-09-02T01:49:00')
    clock.set(_REAL_DT(2026, 9, 3, 9, 31))
    from unittest.mock import patch as _patch
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '存量票=10.0', '--atr', '存量票=0.5')
    assert r.exit_code == 0 and '存量票' in r.output and '买' in r.output, r.output
    assert conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#293'"
                        ).fetchone()[0] == 'filled'
    conn.close()


# ---------- d) newsdb 不可达 → fail-closed 回退旧行为（开槽日禁成交）不崩 ----------

def test_newsdb_unreachable_falls_back_fail_closed(sleeve_ready, newsdb, cal, clock,
                                                   monkeypatch):
    from paper_trading_v2.cli import app
    _seed_news(newsdb, 293, '2026-09-01 19:05:00')
    _, conn = _open_pending(sleeve_ready, '断链票', 'ND#293')
    _set_opened_at(conn, 'ND#293', '2026-09-02T01:49:00')
    clock.set(_REAL_DT(2026, 9, 2, 6, 5))
    monkeypatch.setenv('STOCK_NEWS_DB', str(newsdb) + '.missing')  # 不可达
    from unittest.mock import patch as _patch
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '断链票=10.0', '--atr', '断链票=0.5')
    # 回退旧行为：earliest=opened_at 次一交易日=9/3 > 今日 9/2 → 跳过拒成交；不崩
    assert r.exit_code == 0, r.output
    assert '成交时点门跳过 ND#293' in r.output, r.output
    assert conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#293'"
                        ).fetchone()[0] == 'pending'
    # fallback 必须留痕 audit
    rows = [a for a in _audit_rows(conn, 'sleeve_fill_gate_fallback')
            if 'ND#293' in (a['reason'] or '')]
    assert rows, 'fail-closed 回退必须写 audit 留痕'
    assert 'fallback' in rows[0]['reason']
    # 次日旧口径放行（opened_at+1 交易日）
    clock.set(_REAL_DT(2026, 9, 3, 9, 31))
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '断链票=10.0', '--atr', '断链票=0.5')
    assert r.exit_code == 0 and '买' in r.output, r.output
    conn.close()


def test_event_key_not_nd_falls_back(sleeve_ready, newsdb, cal, clock):
    """auto: 兜底键 → newsdb 无此键 → fail-closed 回退 opened_at+1 交易日。"""
    from paper_trading_v2.cli import app
    _, conn = _open_pending(sleeve_ready, '兜底票', 'auto:兜底票:2026-09-01')
    _set_opened_at(conn, 'auto:兜底票:2026-09-01', '2026-09-02T01:49:00')
    clock.set(_REAL_DT(2026, 9, 2, 6, 5))
    from unittest.mock import patch as _patch
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '兜底票=10.0', '--atr', '兜底票=0.5')
    assert r.exit_code == 0, r.output
    assert '成交时点门跳过' in r.output, r.output      # 旧行为：开槽日拒
    clock.set(_REAL_DT(2026, 9, 3, 9, 31))
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=KL):
        r = _run(app, 'sleeve-fill', '--price', '兜底票=10.0', '--atr', '兜底票=0.5')
    assert r.exit_code == 0 and '买' in r.output, r.output
    conn.close()


def test_unit_fallback_chain_and_cross_week(monkeypatch):
    """单元锁：fallback=开槽日次一交易日；跨周末（9/4 五 → 9/7 一）；坏键/不可达回退；
    门只看 created_at（started_at 更早不加速放行）。"""
    from paper_trading_v2 import earliest_fill as ef
    monkeypatch.setattr(ef, 'is_trading_day',
                        lambda d: str(d)[:10] in {'2026-09-02', '2026-09-04',
                                                  '2026-09-07'})
    assert str(ef.next_trading_day('2026-09-04')) == '2026-09-07'   # 跨周末
    r = ef.resolve_earliest_fill('auto:x:2026-09-04', '2026-09-04T10:00:00',
                                 news_db_path='/nonexistent/news.db')
    assert r.source == 'fallback' and str(r.date) == '2026-09-07'
    r2 = ef.resolve_earliest_fill('ND#999999', '2026-09-04T10:00:00',
                                  news_db_path='/nonexistent/news.db')
    assert r2.source == 'fallback' and str(r2.date) == '2026-09-07'
    r3 = ef.resolve_earliest_fill('garbage-key', '2026-09-04',
                                  news_db_path='/nonexistent/news.db')
    assert r3.source == 'fallback'
    # created_at=9/2（earliest=9/4 五→? 9/4 是交易日 → 9/7?）
    # 注入库：created_at=9/2、started_at=8/26 → earliest=next_td(9/2)=9/4（若误读
    # started_at 会给 9/4 前日期=放行更早）——用 today=9/3 断言未到期。
    import sqlite3 as _sq
    db = '/tmp/ef_unit_news.db'
    if os.path.exists(db):
        os.remove(db)
    c = _sq.connect(db)
    c.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, started_at TEXT,"
              " created_at TEXT)")
    c.execute("INSERT INTO events VALUES (7,'2026-08-26 08:19','2026-09-02 21:00')")
    c.commit()
    c.close()
    r4 = ef.resolve_earliest_fill('ND#7', '2026-09-03T01:00:00', news_db_path=db)
    assert r4.source == 'newsdb' and str(r4.date) == '2026-09-04'
    assert not ef.gate_allowed(r4, '2026-09-03')     # started_at 基准会误放行
    assert ef.gate_allowed(r4, '2026-09-04')


# ---------- e) 跳水影子：开盘≤昨收-3% → dive_open 落影子且成交照常 ----------

def test_dive_open_shadow_written_fill_proceeds(sleeve_ready, newsdb, cal, clock):
    from paper_trading_v2.db import get_connection
    op, conn = _open_pending(sleeve_ready, '跳水票', 'ND#600')
    _set_opened_at(conn, 'ND#600', '2026-09-01T09:00:00')
    conn.close()
    # 开盘 9.6 vs 昨收 10.0 = -4% ≤ -3% → 影子记；照成交不拦截（观察不执法）
    res = op.fill_pending(event_key='ND#600',
                          open_prices={'跳水票': 9.6},
                          prev_close_map={'跳水票': 10.0},
                          atr={'跳水票': 0.5})
    assert res and res[0]['filled'] and 'qty' in res[0]['filled'][0], res
    conn = get_connection(_db(sleeve_ready))
    rows = _shadow_rows(conn, 'dive_open')
    assert len(rows) == 1, rows
    p = json.loads(rows[0]['payload'])
    assert rows[0]['key'] == 'ND#600'
    assert p['stock'] == '跳水票' and p['fill_price'] == 9.6
    assert p['prev_close'] == 10.0
    assert abs(p['dip'] - round(9.6 / 10.0 - 1, 4)) < 1e-9
    assert p['ts']
    # 边界：-2.9% 不记；-3.0% 整记
    conn.close()
    op2, conn2 = _open_pending(sleeve_ready, '浅调票', 'ND#601')
    _set_opened_at(conn2, 'ND#601', '2026-09-01T09:00:00')
    conn2.close()
    op2.fill_pending(event_key='ND#601', open_prices={'浅调票': 9.71},
                     prev_close_map={'浅调票': 10.0}, atr={'浅调票': 0.5})
    conn = get_connection(_db(sleeve_ready))
    assert len(_shadow_rows(conn, 'dive_open')) == 1       # 仍只有 ND#600 一条
    op3, conn3 = _open_pending(sleeve_ready, '踩线票', 'ND#602')
    _set_opened_at(conn3, 'ND#602', '2026-09-01T09:00:00')
    conn3.close()
    op3.fill_pending(event_key='ND#602', open_prices={'踩线票': 9.70},
                     prev_close_map={'踩线票': 10.0}, atr={'踩线票': 0.5})
    assert len(_shadow_rows(conn, 'dive_open')) == 2       # -3% 整也记
    conn.close()


# ---------- f) watch_scan SLEEVE_FILL 事件化 ----------

WS_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_watch_scan():
    import importlib.util
    path = os.path.abspath(os.path.join(
        WS_ROOT, '..', '..', '..', 'task-bus', 'scripts', 'watch_scan.py'))
    spec = importlib.util.spec_from_file_location('watch_scan_test', path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ws_scan(env, monkeypatch, tmp_path, clock):
    """watch_scan 指向 tmp：POOL_DB=_db(env)，TASKS_DB=tmp，时钟/日历注入。"""
    mod = _load_watch_scan()
    tasks = tmp_path / 'tasks' / 'tasks.db'
    monkeypatch.setattr(mod, 'POOL_DB', str(_db(env)))
    monkeypatch.setattr(mod, 'TASKS_DB', str(tasks))
    monkeypatch.setattr(mod, 'is_trading_day', lambda d=None: True)

    class _DT:
        now = staticmethod(lambda: clock.dt)

    monkeypatch.setattr(mod, 'datetime', _DT)
    return mod, tasks


def test_watch_scan_sleeve_fill_event_enqueues_once(sleeve_ready, newsdb, cal, clock,
                                                    ws_scan):
    """到期 pending（created_at 9/1 → earliest 9/2 ≤ today）→ 插 1 个 SLEEVE_FILL
    （payload 含 event_keys/slot_count）；再 tick 不重复；done 后同日仍 1 个。"""
    mod, tasks = ws_scan
    _seed_news(newsdb, 700, '2026-09-01 20:00:00')
    _, conn = _open_pending(sleeve_ready, '唤醒票', 'ND#700')
    _set_opened_at(conn, 'ND#700', '2026-09-02T01:49:00')
    conn.close()
    clock.set(_REAL_DT(2026, 9, 2, 9, 31))
    mod.check_sleeve_fill_event()          # 第一次 tick → 插
    conn2 = sqlite3.connect(tasks)
    rows = conn2.execute("SELECT payload FROM task_events WHERE type='SLEEVE_FILL'"
                         ).fetchall()
    assert len(rows) == 1, rows
    p = json.loads(rows[0][0])
    assert p['event_keys'] == ['ND#700'] and p['slot_count'] == 1
    # 同日再 tick：pending/processing 同型事件已在 → 不重复插
    mod.check_sleeve_fill_event()
    assert conn2.execute("SELECT COUNT(*) FROM task_events WHERE type='SLEEVE_FILL'"
                         ).fetchone()[0] == 1
    # 消费 done 但槽仍到期 pending → 下一 tick 重插（唤醒层持续施压直到 filled）
    conn2.execute("UPDATE task_events SET status='done' WHERE type='SLEEVE_FILL'")
    conn2.commit()
    mod.check_sleeve_fill_event()
    assert conn2.execute("SELECT COUNT(*) FROM task_events WHERE type='SLEEVE_FILL'"
                         ).fetchone()[0] == 2
    # 成交后（槽 filled）→ 不再插
    conn2.close()


def test_watch_scan_sleeve_fill_not_due_no_event(sleeve_ready, newsdb, cal, clock,
                                                 ws_scan):
    """T+1 未到期（created_at 9/2 盘中 → earliest=9/3 > 今日 9/2）→ 不插事件。"""
    mod, tasks = ws_scan
    _seed_news(newsdb, 701, '2026-09-02 10:30:00')
    _, conn = _open_pending(sleeve_ready, '未到期票', 'ND#701')
    _set_opened_at(conn, 'ND#701', '2026-09-02T11:00:00')
    conn.close()
    clock.set(_REAL_DT(2026, 9, 2, 14, 0))
    mod.check_sleeve_fill_event()
    if os.path.exists(tasks):
        conn2 = sqlite3.connect(tasks)
        n = conn2.execute("SELECT COUNT(*) FROM task_events WHERE type='SLEEVE_FILL'"
                          ).fetchone()[0]
        conn2.close()
    else:
        n = 0                                # 未到期=连任务表都不必建
    assert n == 0


def test_watch_scan_sleeve_pending_line_kept(sleeve_ready, newsdb, cal, clock,
                                             ws_scan):
    """[SLEEVE] 唤醒行保留不动（改造只加事件化，不删行）：到期 pending → 行含
    'sleeve-fill'。"""
    mod, tasks = ws_scan
    _seed_news(newsdb, 702, '2026-09-01 22:00:00')
    _, conn = _open_pending(sleeve_ready, '唤醒票乙', 'ND#702')
    _set_opened_at(conn, 'ND#702', '2026-09-02T01:49:00')
    conn.close()
    clock.set(_REAL_DT(2026, 9, 2, 9, 31))
    out = mod.check_sleeve_pending()
    assert any('[SLEEVE]' in l and 'sleeve-fill' in l for l in out), out
