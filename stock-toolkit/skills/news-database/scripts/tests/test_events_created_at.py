"""events.created_at 入库时刻真源迁移回归锁（2026-09-02，先红后绿）

背景：earliest_fill 成交门读 events.created_at（"消息何时被我们知道"），但生产库
是旧 schema 无此列，storage.py INSERT 也不显式写。目标：任意库（新旧）经
news_database.db.connect() 后 created_at 可用——
- 旧库：ALTER ADD COLUMN TEXT DEFAULT ''（SQLite 禁非常量默认，实测）+ 历史回填
  COALESCE(started_at, updated_at, now)（能追到的最早时间戳；旧事件均早于任何
  成交门 → 门对其自动失效，可接受）
- 触发器 ai_events_created_at：新事件自动盖入库戳（只补 NULL/''，不覆写显式值）
- 幂等：重复 connect() 不报错、不重复回填
"""
import sqlite3
from datetime import datetime

import pytest

from news_database.db import connect, init_db

# 旧 schema（生产实形：无 created_at 列）
OLD_EVENTS_DDL = """
CREATE TABLE events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    entity_id         INTEGER,
    info_type         TEXT NOT NULL DEFAULT 'news',
    time_sensitivity  TEXT NOT NULL DEFAULT 'medium',
    importance        INTEGER NOT NULL DEFAULT 3,
    status            TEXT NOT NULL DEFAULT 'open',
    latest_summary    TEXT,
    started_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    resolved_at       TEXT,
    msg_count         INTEGER NOT NULL DEFAULT 0
)
"""


def _make_old_db(path, rows=(('旧事件A', '2026-08-01 09:00:00', '2026-08-02 10:00:00'),
                             ('旧事件B', '2026-08-10 08:30:00', '2026-08-10 08:30:00'))):
    """手建旧 schema 库（无 created_at），塞历史行。"""
    conn = sqlite3.connect(path)
    conn.execute(OLD_EVENTS_DDL)
    conn.executemany(
        "INSERT INTO events (title, entity_type, started_at, updated_at) "
        "VALUES (?, 'market', ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def _cols(conn, table='events'):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# ---------- a) 旧库迁移主锁：connect() 后列存在 + 历史行回填非空（红→绿） ----------


def test_legacy_db_gets_created_at_backfilled(db_path):
    _make_old_db(db_path)
    conn = connect(db_path)
    try:
        assert 'created_at' in _cols(conn), "connect() 必须幂等迁移出 created_at 列"
        rows = conn.execute(
            "SELECT started_at, created_at FROM events ORDER BY id").fetchall()
        assert len(rows) == 2
        for r in rows:
            assert r['created_at'] and r['created_at'] != '', \
                f"历史行必须回填非空: {dict(r)}"
        # 回填语义 = COALESCE(started_at, updated_at, now)：能追到的最早时间戳
        assert rows[0]['created_at'] == '2026-08-01 09:00:00'
        assert rows[1]['created_at'] == '2026-08-10 08:30:00'
    finally:
        conn.close()


# ---------- b) 新事件自动盖入库戳（分钟级）：迁移库 + 全新库两路径 ----------


def test_new_insert_gets_created_at_stamp_migrated(db_path):
    _make_old_db(db_path)
    conn = connect(db_path)          # 迁移 + 触发器
    conn.close()
    conn = connect(db_path)          # 重复 connect 触发器仍在（IF NOT EXISTS）
    try:
        conn.execute("INSERT INTO events (title, entity_type) "
                     "VALUES ('新事件', 'market')")   # 不显式写 created_at
        conn.commit()
        got = conn.execute("SELECT created_at FROM events WHERE title='新事件'"
                           ).fetchone()[0]
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        assert got and (got or '').startswith(now), \
            f"新事件 created_at 应=当下(分钟级): got={got!r} now={now!r}"
    finally:
        conn.close()


def test_fresh_db_init_and_storage_create_event_stamp(db_path):
    """全新库 connect()+init_db() → storage.create_event 路径也自动有戳。"""
    conn = connect(db_path)
    try:
        init_db(conn)
        assert 'created_at' in _cols(conn), "全新库 SCHEMA DDL 必须带 created_at"
        from news_database.storage import create_event
        eid = create_event(conn, '存储层事件', entity_type='market')
        got = conn.execute("SELECT created_at FROM events WHERE id=?",
                           (eid,)).fetchone()[0]
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        assert got and got.startswith(now), \
            f"storage.create_event 须经触发器盖戳: got={got!r} now={now!r}"
    finally:
        conn.close()


# ---------- c) 幂等：重复 connect() 不报错、不重复回填、不改动已定值 ----------


def test_repeat_connect_is_idempotent(db_path):
    _make_old_db(db_path)
    conn = connect(db_path)
    conn.execute("UPDATE events SET created_at='2020-01-01 00:00:00' "
                 "WHERE title='旧事件A'")
    conn.commit()
    conn.close()
    for _ in range(3):                       # 重复 connect 不得报错
        conn = connect(db_path)
        conn.close()
    conn = connect(db_path)
    try:
        assert _cols(conn).count('created_at') == 1
        a = conn.execute("SELECT created_at FROM events WHERE title='旧事件A'"
                         ).fetchone()[0]
        b = conn.execute("SELECT created_at FROM events WHERE title='旧事件B'"
                         ).fetchone()[0]
        assert a == '2020-01-01 00:00:00', "重复 connect 不得重刷已定值（不重复回填）"
        assert b == '2026-08-10 08:30:00'
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


# ---------- d) 触发器只补空：显式非空 created_at 不被覆写（只测 INSERT 路径） ----------


def test_trigger_does_not_overwrite_explicit_created_at(db_path):
    _make_old_db(db_path)
    conn = connect(db_path)
    try:
        conn.execute("INSERT INTO events (title, entity_type, created_at) "
                     "VALUES ('显式戳事件', 'market', '2020-05-05 10:00:00')")
        conn.execute("INSERT INTO events (title, entity_type, created_at) "
                     "VALUES ('空串事件', 'market', '')")   # 空串 → 触发器补当下
        conn.commit()
        got = conn.execute("SELECT created_at FROM events WHERE title='显式戳事件'"
                           ).fetchone()[0]
        assert got == '2020-05-05 10:00:00', f"显式非空值不得被触发器覆写: {got!r}"
        got2 = conn.execute("SELECT created_at FROM events WHERE title='空串事件'"
                            ).fetchone()[0]
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        assert got2 and got2.startswith(now), f"'' 应被触发器补当下: {got2!r}"
    finally:
        conn.close()
