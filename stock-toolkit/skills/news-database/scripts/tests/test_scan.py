"""扫描状态管理：记录每类实体的上次扫描时间，供时效性调度。"""

from news_database.db import connect, init_db
from news_database import scan


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def test_get_last_scan_default_none(db_path):
    conn = _conn(db_path)
    assert scan.get_last_scan(conn, "stock", "601127.SH") is None
    conn.close()


def test_set_then_get_last_scan(db_path):
    conn = _conn(db_path)
    scan.set_last_scan(conn, "stock", "601127.SH")
    ts = scan.get_last_scan(conn, "stock", "601127.SH")
    assert ts is not None
    conn.close()


def test_scan_due_by_sensitivity(db_path):
    conn = _conn(db_path)
    # 从未扫描 → 到期
    assert scan.scan_due(conn, "market", "global", interval_hours=8) is True
    # 刚扫描 → 未到期
    scan.set_last_scan(conn, "market", "global")
    assert scan.scan_due(conn, "market", "global", interval_hours=8) is False
    conn.close()
