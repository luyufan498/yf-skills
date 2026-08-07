"""扫描状态管理：记录每类实体的上次扫描时间，供时效性调度。"""

from news_database.db import connect, init_db
from news_database import scan, storage


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


def test_scan_status_cli(tmp_path, monkeypatch):
    """scan-status 命令读/写扫描时间。"""
    from typer.testing import CliRunner
    from news_database.cli import app
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner = CliRunner()
    runner.invoke(app, ["init"])
    # set
    r = runner.invoke(app, ["scan-status", "set", "stock", "601127.SH"])
    assert r.exit_code == 0
    # get
    g = runner.invoke(app, ["scan-status", "get", "stock", "601127.SH"])
    assert g.exit_code == 0
    assert "601127.SH" in g.stdout
    # 未扫描的返回空
    g2 = runner.invoke(app, ["scan-status", "get", "stock", "000000.SZ"])
    assert g2.exit_code == 0
    assert "未扫描" in g2.stdout


def test_list_scan_scopes(db_path):
    conn = _conn(db_path)
    storage.upsert_stock(conn, "601127.SH", "赛力斯", is_watchlist=1, priority=5)
    storage.upsert_stock(conn, "300521.SZ", "爱司凯", is_watchlist=0, priority=2)
    storage.upsert_industry(conn, "光模块")
    scan.set_last_scan(conn, "stock", "601127.SH")
    scopes = scan.list_scan_scopes(conn)
    # 应包含：watchlist 股票优先 + 非watchlist股票 + 行业 + 全局
    codes = [s["scope_id"] for s in scopes if s["scope_type"] == "stock"]
    assert "601127.SH" in codes and "300521.SZ" in codes
    inds = [s["scope_id"] for s in scopes if s["scope_type"] == "industry"]
    assert len(inds) >= 1
    types = {s["scope_type"] for s in scopes}
    assert "market" in types and "policy" in types  # 全局 scope
    # watchlist 优先
    first_stock = next(s for s in scopes if s["scope_type"] == "stock")
    assert first_stock["is_watchlist"] == 1
    # 601127 有 last_scan，300521 没有
    s601 = next(s for s in scopes if s["scope_id"] == "601127.SH")
    s300 = next(s for s in scopes if s["scope_id"] == "300521.SZ")
    assert s601["last_scan"] is not None
    assert s300["last_scan"] is None
    conn.close()


def test_scan_list_cli(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from news_database.cli import app
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner = CliRunner()
    runner.invoke(app, ["init"])
    runner.invoke(app, ["track", "601127.SH", "--name", "赛力斯", "--watchlist"])
    r = runner.invoke(app, ["scan-list"])
    assert r.exit_code == 0
    assert "601127.SH" in r.stdout
    assert "market" in r.stdout or "market/global" in r.stdout
