"""schema 完整性：建库后所有表/列存在。"""

import sqlite3
from news_database import storage
from news_database.db import connect, init_db


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def _table_names(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_init_creates_all_tables(db_path):
    conn = connect(db_path)
    init_db(conn)
    tables = _table_names(conn)
    expected = {"stocks", "industries", "events", "messages",
                "event_stock", "event_industry", "relations", "refresh_requests"}
    assert expected <= tables
    conn.close()


def test_events_columns(db_path):
    conn = connect(db_path)
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    assert {"id", "title", "entity_type", "entity_id", "time_sensitivity",
            "importance", "status", "latest_summary", "started_at", "updated_at",
            "resolved_at", "msg_count"} <= cols
    conn.close()


def test_messages_columns(db_path):
    conn = connect(db_path)
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    assert {"id", "event_id", "title", "summary", "url", "source",
            "occurred_at", "fetched_at", "importance", "keywords",
            "embedding", "ts_updated"} <= cols
    conn.close()


def test_refresh_requests_columns(db_path):
    conn = connect(db_path)
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(refresh_requests)")}
    assert {"id", "stock_code", "signal_text", "reason", "priority",
            "created_at", "status"} <= cols
    conn.close()


def test_fts_index_created(db_path):
    conn = connect(db_path)
    init_db(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'messages_fts%'")}
    assert "messages_fts" in names
    conn.close()


def test_industry_aliases_table_and_index(db_path):
    conn = connect(db_path)
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(industry_aliases)")}
    assert {"industry_id", "alias_name"} <= cols
    # 别名查询走索引（索引存在于 sqlite_master）
    idx = {r[1] for r in conn.execute("PRAGMA index_list(industry_aliases)")}
    assert "idx_industry_aliases_name" in idx
    conn.close()


def test_scan_log_table(db_path):
    conn = connect(db_path)
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_log)")}
    assert {"scope_type", "scope_id", "last_scan"} <= cols
    conn.close()


def test_stocks_market_cap_column(db_path):
    conn = connect(db_path)
    init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stocks)")}
    assert "market_cap" in cols
    conn.close()


def test_messages_have_confidence_columns(db_path):
    conn = _conn(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
    assert "source_type" in cols
    assert "confidence" in cols
    conn.close()


def test_messages_confidence_defaults(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "测试事件", entity_type="stock")
    storage.add_message(conn, eid, "测试消息")
    row = conn.execute("SELECT source_type, confidence FROM messages").fetchone()
    assert row["source_type"] == "media"
    assert row["confidence"] == 4
    conn.close()


def test_old_db_migrates_confidence_columns(db_path):
    """旧库（无置信度列）init_db 后应自动补列。"""
    import sqlite3 as _s
    raw = _s.connect(db_path)
    # 建一个旧版 messages 表（无 source_type/confidence）
    raw.executescript("""
        DROP TABLE IF EXISTS messages;
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            url TEXT, source TEXT, occurred_at TEXT,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            importance INTEGER NOT NULL DEFAULT 3, keywords TEXT,
            embedding BLOB, ts_updated TEXT
        );
        INSERT INTO messages (event_id, title) VALUES (1, '旧消息');
    """)
    raw.commit()
    raw.close()
    # 重新 init_db 应补列
    conn = _conn(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
    assert "source_type" in cols
    assert "confidence" in cols
    row = conn.execute("SELECT source_type, confidence FROM messages").fetchone()
    assert row["source_type"] == "media"
    assert row["confidence"] == 4
    conn.close()


def test_deepdive_requests_table(db_path):
    conn = _conn(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(deepdive_requests)")]
    assert "target_type" in cols
    assert "target_id" in cols
    assert "status" in cols
    idx = {r[1] for r in conn.execute("PRAGMA index_list(deepdive_requests)")}
    assert "idx_deepdive_status" in idx
    conn.close()
