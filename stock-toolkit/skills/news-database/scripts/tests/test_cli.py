"""CLI 端到端（通过 CliRunner + tmp db）。"""

from typer.testing import CliRunner
from news_database.cli import app

runner = CliRunner()


def test_init_command(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 0
    assert db.exists()
    # 幂等
    res2 = runner.invoke(app, ["init"])
    assert res2.exit_code == 0


def test_track_then_query_stock(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["track", "601127.SH", "--name", "赛力斯",
                            "--industry", "新能源汽车", "--watchlist"])
    assert r.exit_code == 0
    q = runner.invoke(app, ["query-stock", "601127.SH"])
    assert q.exit_code == 0


def test_save_new_event_then_lookup(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, [
        "save", "--new-event",
        "--title", "光模块景气上行",
        "--entity-type", "industry",
        "--summary", "龙头涨价10%",
        "--importance", "4",
        "--keywords", "光模块,涨价",
    ])
    assert r.exit_code == 0, r.stdout
    assert "事件" in r.stdout
    look = runner.invoke(app, ["lookup", "光模块涨价"])
    assert look.exit_code == 0
    assert "光模块景气上行" in look.stdout


def test_refresh_request_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, [
        "request-refresh", "赛力斯",
        "--signal", "放量急跌5%，关注二次探底",
        "--reason", "放量急跌",
        "--priority", "3",
    ])
    assert r.exit_code == 0
    lst = runner.invoke(app, ["refresh-requests", "--status", "pending"])
    assert "赛力斯" in lst.stdout
    ack = runner.invoke(app, ["ack-refresh", "1"])
    assert ack.exit_code == 0
    lst2 = runner.invoke(app, ["refresh-requests", "--status", "pending"])
    assert "赛力斯" not in lst2.stdout


def test_event_missing_exits_1(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["event", "999"])
    assert r.exit_code == 1


def test_save_requires_exactly_one_event_flag(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    # 既没 --event 也没 --new-event
    r = runner.invoke(app, ["save", "--title", "X"])
    assert r.exit_code == 2
    # --event 指向不存在的事件
    r2 = runner.invoke(app, ["save", "--title", "X", "--event", "999"])
    assert r2.exit_code == 3


def test_update_event_missing_exits_1(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["update-event", "999", "--latest-summary", "boo"])
    assert r.exit_code == 1


def test_ack_refresh_missing_exits_1(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["ack-refresh", "999"])
    assert r.exit_code == 1
