"""schema 完整性：建库后所有表/列存在。"""

import sqlite3
from news_database.db import connect, init_db


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
