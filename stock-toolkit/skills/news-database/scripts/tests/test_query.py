"""分析 agent 查询端：个股/行业/市场/重要事件。"""

from news_database.db import connect, init_db
from news_database import storage, query


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def _seed(conn):
    # 个股事件
    e1 = storage.create_event(conn, "赛力斯业绩预亏", entity_type="stock", importance=5)
    storage.upsert_stock(conn, "601127.SH", "赛力斯")
    storage.link_event_stock(conn, e1, "601127.SH", relevance=100)
    storage.add_message(conn, e1, title="发布预亏公告", importance=5)
    # 行业事件
    e2 = storage.create_event(conn, "光模块景气上行", entity_type="industry", importance=4)
    storage.link_event_industry(conn, e2, "光模块", relevance=80)
    storage.add_message(conn, e2, title="龙头涨价", importance=4)
    # 市场事件
    e3 = storage.create_event(conn, "上证放量上攻", entity_type="market", importance=3)
    storage.add_message(conn, e3, title="成交破万亿", importance=3)
    return e1, e2, e3


def test_query_stock(db_path):
    conn = _conn(db_path)
    e1, _, _ = _seed(conn)
    evs = query.query_stock(conn, "601127.SH")
    assert len(evs) == 1 and evs[0]["id"] == e1
    conn.close()


def test_query_stock_with_days(db_path):
    conn = _conn(db_path)
    _seed(conn)
    # 过去 7 天应能取到（刚插入）
    evs = query.query_stock(conn, "601127.SH", days=7)
    assert len(evs) == 1
    conn.close()


def test_query_stock_with_days_excludes_old(db_path):
    conn = _conn(db_path)
    e1, _, _ = _seed(conn)
    # 把事件回拨 30 天，days=7 时应被排除
    conn.execute("UPDATE events SET updated_at = datetime('now','localtime','-30 days') WHERE id=?", (e1,))
    conn.commit()
    evs = query.query_stock(conn, "601127.SH", days=7)
    assert len(evs) == 0
    conn.close()


def test_query_industry(db_path):
    conn = _conn(db_path)
    _, e2, _ = _seed(conn)
    evs = query.query_industry(conn, "光模块")
    assert len(evs) == 1 and evs[0]["id"] == e2
    conn.close()


def test_query_market(db_path):
    conn = _conn(db_path)
    _, _, e3 = _seed(conn)
    evs = query.query_market(conn)
    assert len(evs) == 1 and evs[0]["id"] == e3
    conn.close()


def test_query_important(db_path):
    conn = _conn(db_path)
    e1, e2, _ = _seed(conn)
    evs = query.query_important(conn, min_importance=4)
    assert {e["id"] for e in evs} == {e1, e2}      # e1(5), e2(4)，排除 e3(3)
    conn.close()


def test_query_industry_by_alias(db_path):
    conn = _conn(db_path)
    _, e2, _ = _seed(conn)
    storage.add_industry_alias(conn, storage.upsert_industry(conn, "光模块"), "光模块行业")
    evs = query.query_industry(conn, "光模块行业")
    assert len(evs) == 1 and evs[0]["id"] == e2
    conn.close()


def test_query_industry_parent_includes_child(db_path):
    conn = _conn(db_path)
    _, e2, _ = _seed(conn)
    # 设 "AI算力" 为 "光模块" 的子行业，事件关联到 "AI算力"
    storage.set_industry_parent(conn, "AI算力", "光模块")
    e_new = storage.create_event(conn, "AI算力景气", entity_type="industry")
    storage.link_event_industry(conn, e_new, "AI算力")
    # 事件需有 confidence>=3 的消息才被默认查询返回（决策依据）
    storage.add_message(conn, e_new, "AI算力景气公告", source_type="media")
    # 查父行业应同时返回 光模块事件 + 子行业 AI算力事件
    evs = query.query_industry(conn, "光模块")
    assert {ev["id"] for ev in evs} == {e2, e_new}
    conn.close()


def test_resolve_industry_ids_missing_returns_none(db_path):
    conn = _conn(db_path)
    _seed(conn)
    assert query.resolve_industry_ids(conn, "不存在的行业") is None
    conn.close()


def test_suggest_industries(db_path):
    conn = _conn(db_path)
    _seed(conn)
    cands = query.suggest_industries(conn, "模块")
    assert len(cands) >= 1
    assert any("光模块" in c["name"] for c in cands)
    conn.close()


def test_related_industries_bidirectional(db_path):
    conn = _conn(db_path)
    a = storage.upsert_industry(conn, "计算机设备/AI算力")
    b = storage.upsert_industry(conn, "精密温控节能设备/数据中心液冷")
    storage.relate_industries(conn, "计算机设备/AI算力", "精密温控节能设备/数据中心液冷", strength=60)
    # 双向都能看到（from_id 存的是 str）
    ra = query.related_industries(conn, a)
    rb = query.related_industries(conn, b)
    assert {int(r["other_id"]) for r in ra} == {b}
    assert {int(r["other_id"]) for r in rb} == {a}
    assert ra[0]["strength"] == 60
    conn.close()


def _mk_stock_event(conn, code, title):
    eid = storage.create_event(conn, title, entity_type="stock", importance=4)
    storage.link_event_stock(conn, eid, code)
    return eid


def test_query_stock_filters_low_confidence(db_path):
    conn = _conn(db_path)
    eid = _mk_stock_event(conn, "601127.SH", "赛力斯事件")
    storage.add_message(conn, eid, "官方公告", source_type="official")   # conf 5
    storage.add_message(conn, eid, "论坛流言", source_type="rumor")     # conf 1
    evs = query.query_stock(conn, "601127.SH")
    assert len(evs) == 1
    # include_low_confidence=True 仍返回该事件
    evs2 = query.query_stock(conn, "601127.SH", include_low_confidence=True)
    assert len(evs2) == 1
    conn.close()


def test_query_stock_messages_confdence_visible(db_path):
    """事件返回后，消息带置信度字段。"""
    conn = _conn(db_path)
    eid = _mk_stock_event(conn, "601127.SH", "赛力斯事件")
    storage.add_message(conn, eid, "官方公告", source_type="official")
    msgs = conn.execute(
        "SELECT source_type, confidence FROM messages WHERE event_id=?", (eid,)).fetchall()
    assert {(m["source_type"], m["confidence"]) for m in msgs} == {("official", 5)}
    conn.close()


def test_query_stock_filters_event_with_only_rumor(db_path):
    """事件下只有流言(conf<3)时，默认查询应过滤掉该事件。"""
    conn = _conn(db_path)
    eid = _mk_stock_event(conn, "601127.SH", "流言事件")
    storage.add_message(conn, eid, "论坛流言", source_type="rumor", confidence=1)
    # 默认：只含低置信度消息的事件被过滤
    evs = query.query_stock(conn, "601127.SH")
    assert len(evs) == 0
    # include_low_confidence=True：能查到
    evs2 = query.query_stock(conn, "601127.SH", include_low_confidence=True)
    assert len(evs2) == 1
    conn.close()
