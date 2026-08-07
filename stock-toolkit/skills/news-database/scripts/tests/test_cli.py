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


def test_industry_aliases_add_list(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["industry-aliases", "add", "光模块", "--alias", "光模块行业"])
    assert r.exit_code == 0
    lst = runner.invoke(app, ["industry-aliases", "list", "光模块"])
    assert "光模块行业" in lst.stdout


def test_industry_hierarchy_set_parent(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["industry-hierarchy", "set-parent", "AI算力", "--parent", "计算机设备"])
    assert r.exit_code == 0


def test_industry_relate(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["industry-relate", "计算机设备/AI算力", "--to", "精密温控节能设备/数据中心液冷", "--strength", "60"])
    assert r.exit_code == 0


def test_save_multi_industry(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, [
        "save", "--new-event", "--title", "液冷与AI算力共振",
        "--entity-type", "industry", "--summary", "双行业事件",
        "--industry", "精密温控节能设备/数据中心液冷,计算机设备/AI算力",
    ])
    assert r.exit_code == 0, r.stdout
    # 两个行业都能查到
    q1 = runner.invoke(app, ["query-industry", "精密温控节能设备/数据中心液冷"])
    q2 = runner.invoke(app, ["query-industry", "计算机设备/AI算力"])
    assert "液冷与AI算力共振" in q1.stdout
    assert "液冷与AI算力共振" in q2.stdout


def test_query_industry_not_found_suggests(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    runner.invoke(app, ["industry-aliases", "add", "光模块", "--alias", "光模块行业"])
    r = runner.invoke(app, ["query-industry", "光模块行业"])
    assert r.exit_code == 0
    # 未命中真实行业时给出候选提示
    r2 = runner.invoke(app, ["query-industry", "不存在的行业"])
    assert r2.exit_code == 0
    assert "未找到" in r2.stdout


def test_query_industry_suggestion_shows_relations(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    runner.invoke(app, ["industry-relate", "计算机设备/AI算力",
                        "--to", "精密温控节能设备/数据中心液冷", "--strength", "60"])
    # "算力" 无精确行业/别名（relate 只建全名行业），命中候选提示
    r = runner.invoke(app, ["query-industry", "算力"])
    assert r.exit_code == 0
    assert "相关" in r.stdout and "精密温控节能设备/数据中心液冷" in r.stdout
    assert "s60" in r.stdout


def test_query_industry_exists_but_no_events(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    runner.invoke(app, ["industry-aliases", "add", "光模块", "--alias", "光模块行业"])
    # 行业存在但无事件 → 应打印（无事件）而非走候选提示
    r = runner.invoke(app, ["query-industry", "光模块"])
    assert r.exit_code == 0
    assert "（无事件）" in r.stdout
    assert "未找到" not in r.stdout


def test_industry_aliases_list_no_phantom(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    # list 不存在的行业：不应创建幻影行业，也不应报错
    r = runner.invoke(app, ["industry-aliases", "list", "不存在的行业"])
    assert r.exit_code == 0
    assert "未找到行业" in r.stdout
    lst = runner.invoke(app, ["industry-aliases", "list"])
    assert "不存在的行业" not in lst.stdout


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
