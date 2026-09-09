"""C 腿（msg-expiry-scan）窗口口径测试（2026-09-09 晚审 0c 误计修复）。

复现：生益科技成交 2026-09-03 10:10:11；同日盘前 08:42:59 入库的开槽论点事件
（ND#561）旧版被计入"买后新催化"，新版按完整时间戳比较应剔除；成交后 10:49:50
的事件（ND#575）应保留；槽自身论据事件（exclude_ids）应剔除。
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from paper_trading_v2.pulse_check import _newsdb_events_since  # noqa: E402

CODE = 'sh600183'
BUY_TS = '2026-09-03T10:10:11.041394'


def _mk_news_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, started_at TEXT, "
              "importance INTEGER)")
    c.execute("CREATE TABLE event_stock (event_id INTEGER, stock_code TEXT)")
    rows = [
        (478, '电子布提价（开槽论点）', '2026-09-01 08:09:20', 4),
        (561, '富时罗素纳入（成交当日盘前）', '2026-09-03 08:42:59', 4),
        (333, '槽自身论据事件', '2026-09-03 10:00:00', 4),
        (575, '成交后产业链传闻', '2026-09-03 10:49:50', 4),
        (576, '成交后但低重要度', '2026-09-03 11:00:00', 3),
    ]
    c.executemany("INSERT INTO events VALUES (?,?,?,?)", rows)
    c.executemany("INSERT INTO event_stock VALUES (?,?)",
                  [(r[0], CODE) for r in rows])
    c.commit()
    c.close()
    return path


def test_c_leg_excludes_same_day_pre_fill_event():
    db = _mk_news_db()
    try:
        evs = _newsdb_events_since(CODE, BUY_TS, db_path=db)
        ids = [e['id'] for e in evs]
        assert ids == [575], ids          # 561（盘前）/333（论据）/478（更早）/576（imp3）全剔除
    finally:
        os.unlink(db)


def test_c_leg_exclude_slot_own_events():
    db = _mk_news_db()
    try:
        evs = _newsdb_events_since(CODE, BUY_TS, exclude_ids=(575,), db_path=db)
        assert evs == [], evs
    finally:
        os.unlink(db)


def test_c_leg_date_only_would_overcount():
    """回归护栏：证明"纯日期"口径会多算（旧 bug 的形态）。"""
    db = _mk_news_db()
    try:
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        n_old = c.execute(
            "SELECT COUNT(*) FROM events e JOIN event_stock es ON es.event_id=e.id "
            "WHERE es.stock_code=? AND e.importance>=4 AND e.started_at > ?",
            (CODE, BUY_TS[:10])).fetchone()[0]
        c.close()
        assert n_old == 3, n_old        # 561 + 333 + 575
        assert len(_newsdb_events_since(CODE, BUY_TS, db_path=db)) == 1
    finally:
        os.unlink(db)


def test_c_leg_missing_db_returns_none():
    assert _newsdb_events_since(CODE, BUY_TS, db_path='/tmp/__no_such_news.db__') is None
