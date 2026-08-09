"""异动→刷新请求队列。"""

from news_database.db import connect, init_db
from news_database import storage


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def test_create_and_list_pending(db_path):
    conn = _conn(db_path)
    rid = storage.create_refresh_request(
        conn, "赛力斯",
        signal="7/13 业绩预亏后跌停，今日放量急跌5%，关注二次探底",
        reason="放量急跌", priority=3)
    assert rid > 0
    pending = storage.list_refresh_requests(conn, status="pending")
    assert len(pending) == 1
    assert pending[0]["signal_text"].startswith("7/13")
    conn.close()


def test_ack_marks_done(db_path):
    conn = _conn(db_path)
    rid = storage.create_refresh_request(conn, "赛力斯", signal="异动", reason="放量急跌")
    storage.ack_refresh_request(conn, rid)
    assert storage.list_refresh_requests(conn, status="pending") == []
    done = storage.list_refresh_requests(conn, status="done")
    assert len(done) == 1 and done[0]["id"] == rid
    conn.close()


def test_priority_ordering(db_path):
    conn = _conn(db_path)
    storage.create_refresh_request(conn, "A", signal="低", priority=1)
    storage.create_refresh_request(conn, "B", signal="高", priority=5)
    pending = storage.list_refresh_requests(conn, status="pending")
    assert [p["stock_code"] for p in pending] == ["B", "A"]
    conn.close()


def test_deepdive_crud(db_path):
    conn = _conn(db_path)
    storage.create_deepdive_request(conn, "stock", "601127.SH", reason="论坛疑似重组流言")
    storage.create_deepdive_request(conn, "event", "5", reason="预亏讨论", priority=5)
    pending = storage.list_deepdive_requests(conn, status="pending")
    assert len(pending) == 2
    assert pending[0]["target_type"] == "event"  # priority 5 在前
    assert pending[0]["target_id"] == "5"
    n = storage.ack_deepdive_request(conn, pending[0]["id"])
    assert n == 1
    remain = storage.list_deepdive_requests(conn, status="pending")
    assert len(remain) == 1
    conn.close()


def test_deepdive_ack_nonexistent(db_path):
    conn = _conn(db_path)
    n = storage.ack_deepdive_request(conn, 999)
    assert n == 0
    conn.close()
