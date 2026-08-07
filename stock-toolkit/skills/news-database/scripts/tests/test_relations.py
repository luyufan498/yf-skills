"""事件↔实体 关联 + 实体间关系。"""

from news_database.db import connect, init_db
from news_database import storage


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def test_link_event_stock(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "华为合作", entity_type="stock")
    storage.upsert_stock(conn, "601127.SH", "赛力斯")
    storage.upsert_stock(conn, "000977.SZ", "浪潮信息")
    storage.link_event_stock(conn, eid, "601127.SH", relevance=90)
    storage.link_event_stock(conn, eid, "000977.SZ", relevance=30)
    stocks = storage.event_stocks(conn, eid)
    assert {s["stock_code"] for s in stocks} == {"601127.SH", "000977.SZ"}
    # relevance 排序
    assert stocks[0]["stock_code"] == "601127.SH"
    conn.close()


def test_link_event_industry(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "光模块涨价", entity_type="industry")
    iid = storage.upsert_industry(conn, "光模块")
    storage.link_event_industry(conn, eid, iid, relevance=80)
    inds = storage.event_industries(conn, eid)
    assert len(inds) == 1 and inds[0]["industry_id"] == iid
    conn.close()


def test_add_relation_upsert(db_path):
    conn = _conn(db_path)
    storage.upsert_stock(conn, "601127.SH", "赛力斯")
    storage.upsert_stock(conn, "000977.SZ", "浪潮信息")
    storage.add_relation(conn, "stock", "601127.SH", "stock", "000977.SZ",
                         rel_type="peer_competitor", strength=70)
    # 幂等更新 strength
    storage.add_relation(conn, "stock", "601127.SH", "stock", "000977.SZ",
                         rel_type="peer_competitor", strength=80)
    rows = conn.execute("SELECT * FROM relations").fetchall()
    assert len(rows) == 1
    assert rows[0]["strength"] == 80
    conn.close()


def test_related_stocks(db_path):
    conn = _conn(db_path)
    storage.upsert_stock(conn, "601127.SH", "赛力斯")
    storage.upsert_stock(conn, "000977.SZ", "浪潮信息")
    storage.add_relation(conn, "stock", "601127.SH", "stock", "000977.SZ",
                         rel_type="peer_competitor", strength=70)
    related = storage.related_stocks(conn, "601127.SH")
    assert related == ["000977.SZ"]
    conn.close()
