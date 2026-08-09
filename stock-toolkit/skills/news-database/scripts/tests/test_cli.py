"""CLI 端到端（通过 CliRunner + tmp db）。"""

from typer.testing import CliRunner
from news_database import storage
from news_database.cli import app
from news_database.db import connect, init_db

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


def test_industry_aliases_list_resolves_alias(tmp_path, monkeypatch):
    """list <别名> 应解析到规范行业，而不是报未找到。"""
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    runner.invoke(app, ["industry-aliases", "add", "光模块", "--alias", "光模块行业"])
    lst = runner.invoke(app, ["industry-aliases", "list", "光模块行业"])
    assert lst.exit_code == 0
    assert "光模块" in lst.stdout
    assert "未找到" not in lst.stdout


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


def test_save_invalid_entity_type(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    # 非法 entity-type（含 'macro'）应报错 exit 2，不写库
    for bad in ("badtype", "macro"):
        r = runner.invoke(app, [
            "save", "--new-event", "--title", "X",
            "--entity-type", bad, "--summary", "s",
        ])
        assert r.exit_code == 2, f"entity-type={bad} 应拒绝"
        assert "--entity-type 必须是" in r.stdout
    # 合法 entity-type 正常
    r = runner.invoke(app, [
        "save", "--new-event", "--title", "X",
        "--entity-type", "policy", "--summary", "s",
    ])
    assert r.exit_code == 0, r.stdout


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


def test_track_with_market_cap(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["track", "601127.SH", "--name", "赛力斯", "--industry", "新能源汽车", "--market-cap", "994.85"])
    assert r.exit_code == 0
    q = runner.invoke(app, ["query-stock", "601127.SH"])
    assert q.exit_code == 0
    assert "994.85" in q.stdout or "市值" in q.stdout


def test_cli_save_with_confidence(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["save", "--new-event", "--title", "事件",
                            "--entity-type", "stock", "--summary", "流言",
                            "--source-type", "rumor", "--confidence", "1"])
    assert r.exit_code == 0, r.stdout
    conn = connect(db)
    row = conn.execute("SELECT source_type, confidence FROM messages").fetchone()
    assert row["source_type"] == "rumor"
    assert row["confidence"] == 1
    conn.close()


def test_cli_save_confidence_defaults(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["save", "--new-event", "--title", "公告",
                            "--entity-type", "stock", "--summary", "官方",
                            "--source-type", "official"])
    assert r.exit_code == 0, r.stdout
    conn = connect(db)
    row = conn.execute("SELECT confidence FROM messages").fetchone()
    assert row["confidence"] == 5
    conn.close()


def test_cli_deepdive_request(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["request-deepdive", "stock", "601127.SH",
                            "--reason", "重组流言"])
    assert r.exit_code == 0, r.stdout
    conn = connect(db)
    row = conn.execute("SELECT * FROM deepdive_requests").fetchone()
    assert row["target_type"] == "stock"
    assert row["target_id"] == "601127.SH"
    conn.close()


def test_cli_deepdive_requests_list(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    conn = connect(db)
    storage.create_deepdive_request(conn, "stock", "601127.SH")
    storage.create_deepdive_request(conn, "event", "5", priority=5)
    conn.close()
    r = runner.invoke(app, ["deepdive-requests", "--status", "pending"])
    assert r.exit_code == 0, r.stdout
    assert "event" in r.stdout
    assert "601127.SH" in r.stdout


def test_cli_ack_deepdive(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    conn = connect(db)
    rid = storage.create_deepdive_request(conn, "stock", "601127.SH")
    conn.close()
    r = runner.invoke(app, ["ack-deepdive", str(rid)])
    assert r.exit_code == 0, r.stdout
    conn = connect(db)
    row = conn.execute("SELECT status FROM deepdive_requests WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "done"
    conn.close()


def test_cli_ack_deepdive_nonexistent(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["ack-deepdive", "999"])
    assert r.exit_code == 1


def test_cli_save_invalid_source_type(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["save", "--new-event", "--title", "t",
                            "--entity-type", "stock", "--source-type", "typo"])
    assert r.exit_code == 2
    assert "必须是" in r.output


def test_cli_save_out_of_range_confidence(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["save", "--new-event", "--title", "t",
                            "--entity-type", "stock", "--confidence", "9"])
    assert r.exit_code == 2
    assert "confidence" in r.output


def test_cli_save_invalid_message_type(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["save", "--new-event", "--title", "t",
                            "--entity-type", "stock", "--message-type", "typo"])
    assert r.exit_code == 2
    assert "message-type" in r.output


def test_cli_request_deepdive_invalid_target(tmp_path, monkeypatch):
    db = tmp_path / "news.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])
    r = runner.invoke(app, ["request-deepdive", "bogus", "123"])
    assert r.exit_code == 2
    assert "target_type" in r.output


def test_cli_query_stock_include_low_conf(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    conn = connect(db)
    init_db(conn)
    eid = storage.create_event(conn, "赛力斯事件", entity_type="stock", importance=4)
    storage.link_event_stock(conn, eid, "601127.SH")
    storage.add_message(conn, eid, "官方公告", source_type="official")
    storage.add_message(conn, eid, "论坛流言", source_type="rumor")
    conn.close()
    # 默认应显示事件（有官方消息）
    r = runner.invoke(app, ["query-stock", "601127.SH"])
    assert r.exit_code == 0
    assert "赛力斯事件" in r.output
    # --include-low-confidence：低置信度消息（论坛流言）也显示
    r2 = runner.invoke(app, ["query-stock", "601127.SH", "--include-low-confidence"])
    assert r2.exit_code == 0
    assert "论坛流言" in r2.output


def test_cli_query_stock_low_conf_filtered(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    conn = connect(db)
    init_db(conn)
    eid = storage.create_event(conn, "流言事件", entity_type="stock", importance=4)
    storage.link_event_stock(conn, eid, "601127.SH")
    storage.add_message(conn, eid, "论坛流言", source_type="rumor", confidence=1)
    conn.close()
    # 默认：只有低置信度消息的事件不显示
    r = runner.invoke(app, ["query-stock", "601127.SH"])
    assert r.exit_code == 0
    assert "流言事件" not in r.output
    # --include-low-confidence：显示
    r2 = runner.invoke(app, ["query-stock", "601127.SH", "--include-low-confidence"])
    assert r2.exit_code == 0
    assert "流言事件" in r2.output


def test_cli_query_stock_shows_confidence_tag(tmp_path, monkeypatch):
    """消息级置信度标签在查询输出中可见：official→[官方·…]，rumor→[流言·…]。"""
    db = tmp_path / "t.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    conn = connect(db)
    init_db(conn)
    eid = storage.create_event(conn, "赛力斯事件", entity_type="stock", importance=4)
    storage.link_event_stock(conn, eid, "601127.SH")
    storage.add_message(conn, eid, "官方公告", source_type="official")
    storage.add_message(conn, eid, "论坛流言", source_type="rumor")
    conn.close()
    r = runner.invoke(app, ["query-stock", "601127.SH", "--include-low-confidence"])
    assert r.exit_code == 0
    assert "[官方·其他]" in r.output      # official 消息带 [官方·…] 标签
    assert "[流言·其他]" in r.output      # rumor 消息带 [流言·…] 标签


def test_cli_save_with_message_type(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    r = runner.invoke(app, ["save", "--new-event", "--title", "预亏",
                            "--entity-type", "stock", "--message-type", "financial_report"])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    row = conn.execute("SELECT message_type FROM messages").fetchone()
    assert row["message_type"] == "financial_report"
    conn.close()


def test_cli_query_stock_shows_message_type_tag(tmp_path, monkeypatch):
    """消息内容类型标签在查询输出中可见：financial_report→[·财报业绩]。"""
    db = tmp_path / "t.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    conn = connect(db)
    init_db(conn)
    eid = storage.create_event(conn, "赛力斯事件", entity_type="stock", importance=4)
    storage.link_event_stock(conn, eid, "601127.SH")
    storage.add_message(conn, eid, "7月销量腰斩", source_type="official",
                        message_type="financial_report")
    conn.close()
    r = runner.invoke(app, ["query-stock", "601127.SH"])
    assert r.exit_code == 0, r.output
    assert "财报业绩" in r.output    # [官方·财报业绩] 标签
    assert "[官方·财报业绩]" in r.output


def test_cli_event_shows_message_type_tag(tmp_path, monkeypatch):
    """event <id> 消息时间线与 query-* 一致：同样显示 [官方·财报业绩] 双标签。"""
    db = tmp_path / "t.db"
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    conn = connect(db)
    init_db(conn)
    eid = storage.create_event(conn, "赛力斯事件", entity_type="stock", importance=4)
    storage.add_message(conn, eid, "7月销量腰斩", source_type="official",
                        message_type="financial_report")
    conn.close()
    r = runner.invoke(app, ["event", str(eid)])
    assert r.exit_code == 0, r.output
    assert "[官方·财报业绩]" in r.output
