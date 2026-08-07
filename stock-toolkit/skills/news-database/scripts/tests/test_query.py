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
