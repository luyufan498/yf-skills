"""实体跟踪：stock / industry 的 upsert。"""

import sqlite3
from news_database.db import connect, init_db
from news_database import storage


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def test_upsert_stock_creates_then_updates(db_path):
    conn = _conn(db_path)
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯",
                         industry="新能源汽车", is_watchlist=1, priority=5)
    row = conn.execute("SELECT * FROM stocks WHERE code='601127.SH'").fetchone()
    assert row["name"] == "赛力斯"
    assert row["is_watchlist"] == 1
    # 再次 upsert 更新，不重复
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯",
                         industry="新能源汽车", is_watchlist=0, priority=2)
    rows = conn.execute("SELECT COUNT(*) c FROM stocks WHERE code='601127.SH'").fetchone()
    assert rows["c"] == 1
    assert conn.execute("SELECT priority FROM stocks WHERE code='601127.SH'").fetchone()["priority"] == 2
    conn.close()


def test_upsert_industry_returns_id(db_path):
    conn = _conn(db_path)
    i1 = storage.upsert_industry(conn, "光模块")
    i2 = storage.upsert_industry(conn, "光模块")   # 幂等
    assert i1 == i2
    assert conn.execute("SELECT COUNT(*) c FROM industries").fetchone()["c"] == 1
    conn.close()


def test_get_stock_missing_returns_none(db_path):
    conn = _conn(db_path)
    assert storage.get_stock(conn, "000000.SZ") is None
    conn.close()


def test_get_industry_by_name(db_path):
    conn = _conn(db_path)
    storage.upsert_industry(conn, "光模块")
    row = storage.get_industry_by_name(conn, "光模块")
    assert row is not None and row["name"] == "光模块"
    conn.close()
