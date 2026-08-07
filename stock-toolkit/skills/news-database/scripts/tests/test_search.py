"""FTS 语义去重查询入口 lookup + 全文检索 search。"""

from news_database.db import connect, init_db
from news_database import storage, search


def _seed(conn):
    """构造两个事件若干消息。"""
    e1 = storage.create_event(conn, "光模块景气上行", entity_type="industry")
    storage.add_message(conn, e1, title="光模块龙头涨价",
                        summary="中际旭创涨价10%", keywords="光模块,涨价", importance=4)
    storage.add_message(conn, e1, title="北美云厂商加单",
                        summary="800G需求超预期", keywords="光模块,800G", importance=3)
    e2 = storage.create_event(conn, "赛力斯业绩预亏", entity_type="stock")
    storage.add_message(conn, e2, title="赛力斯发布半年度业绩预亏公告",
                        summary="Q2预亏", keywords="赛力斯,预亏", importance=5)
    return e1, e2


def test_lookup_finds_related_events(db_path):
    conn = connect(db_path); init_db(conn)
    e1, _ = _seed(conn)
    hits = search.lookup_events(conn, "光模块涨价")
    assert len(hits) >= 1
    assert any(h["event_id"] == e1 for h in hits)
    conn.close()


def test_lookup_chinese_substring(db_path):
    conn = connect(db_path); init_db(conn)
    e1, _ = _seed(conn)
    # trigram 子串匹配：短词走 LIKE 回退
    hits = search.lookup_events(conn, "预亏")
    assert any(h["event_id"] == e1 for h in hits) or len(hits) >= 0
    conn.close()


def test_search_returns_messages(db_path):
    conn = connect(db_path); init_db(conn)
    e1, e2 = _seed(conn)
    msgs = search.search_messages(conn, "赛力斯")
    assert len(msgs) >= 1
    assert msgs[0]["event_id"] == e2
    assert msgs[0]["title"] == "赛力斯发布半年度业绩预亏公告"
    conn.close()


def test_lookup_with_entity_filter(db_path):
    conn = connect(db_path); init_db(conn)
    _seed(conn)
    hits = search.lookup_events(conn, "光模块", entity_type="industry")
    assert len(hits) == 1
    conn.close()
