"""实体跟踪：stock / industry 的 upsert。"""

import pytest

from news_database.db import connect, init_db
from news_database import storage


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def test_upsert_stock_creates_then_updates(db_path):
    conn = _conn(db_path)
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯",
                         industry="新能源汽车", is_watchlist=1, priority=5)
    row = conn.execute("SELECT * FROM stocks WHERE code='601127.SH'").fetchone()
    assert row["name"] == "赛力斯"
    assert row["is_watchlist"] == 1
    # 再次 upsert 更新，不重复
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯",
                         industry="新能源汽车", is_watchlist=0, priority=2)
    rows = conn.execute("SELECT COUNT(*) c FROM stocks WHERE code='601127.SH'").fetchone()
    assert rows["c"] == 1
    assert conn.execute("SELECT priority FROM stocks WHERE code='601127.SH'").fetchone()["priority"] == 2
    conn.close()


def test_upsert_stock_preserves_industry_on_none(db_path):
    conn = _conn(db_path)
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯", industry="新能源汽车")
    # 二次 upsert 不带 industry：应保留原行业
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯")
    row = conn.execute("SELECT * FROM stocks WHERE code='601127.SH'").fetchone()
    assert row["industry"] == "新能源汽车"
    # 显式指定 industry：应覆盖
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯", industry="整车")
    row = conn.execute("SELECT * FROM stocks WHERE code='601127.SH'").fetchone()
    assert row["industry"] == "整车"
    conn.close()


def test_upsert_stock_with_market_cap(db_path):
    conn = _conn(db_path)
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯", industry="新能源汽车",
                         is_watchlist=1, priority=5, market_cap=994.85)
    row = conn.execute("SELECT market_cap FROM stocks WHERE code='601127.SH'").fetchone()
    assert row["market_cap"] == 994.85
    # 不带 market_cap 更新时应保留原值
    storage.upsert_stock(conn, code="601127.SH", name="赛力斯")
    row = conn.execute("SELECT market_cap FROM stocks WHERE code='601127.SH'").fetchone()
    assert row["market_cap"] == 994.85
    conn.close()


def test_upsert_industry_returns_id(db_path):
    conn = _conn(db_path)
    i1 = storage.upsert_industry(conn, "光模块")
    i2 = storage.upsert_industry(conn, "光模块")   # 幂等
    assert i1 == i2
    assert conn.execute("SELECT COUNT(*) c FROM industries").fetchone()["c"] == 1
    conn.close()


def test_get_stock_missing_returns_none(db_path):
    conn = _conn(db_path)
    assert storage.get_stock(conn, "000000.SZ") is None
    conn.close()


def test_get_industry_by_name(db_path):
    conn = _conn(db_path)
    storage.upsert_industry(conn, "光模块")
    row = storage.get_industry_by_name(conn, "光模块")
    assert row is not None and row["name"] == "光模块"
    conn.close()


# ---------- 行业别名 ----------

def test_add_and_list_industry_alias(db_path):
    conn = _conn(db_path)
    iid = storage.upsert_industry(conn, "光模块")
    storage.add_industry_alias(conn, iid, "光模块行业")
    storage.add_industry_alias(conn, iid, "AI光通信")
    aliases = storage.list_industry_aliases(conn, iid)
    assert "光模块行业" in aliases and "AI光通信" in aliases
    conn.close()


def test_upsert_industry_resolves_alias(db_path):
    conn = _conn(db_path)
    iid = storage.upsert_industry(conn, "光模块")
    storage.add_industry_alias(conn, iid, "光模块行业")
    # 用别名 upsert：应命中已有行业，不新建
    iid2 = storage.upsert_industry(conn, "光模块行业")
    assert iid2 == iid
    assert conn.execute("SELECT COUNT(*) c FROM industries").fetchone()["c"] == 1
    conn.close()


def test_upsert_industry_unknown_creates(db_path):
    conn = _conn(db_path)
    iid = storage.upsert_industry(conn, "全新行业")
    assert iid > 0
    # 同名再次 upsert 返回同一 id
    assert storage.upsert_industry(conn, "全新行业") == iid
    conn.close()


# ---------- 事件 + 消息 ----------

def test_create_event_and_add_message(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "光模块景气上行", entity_type="industry",
                               time_sensitivity="medium", importance=4)
    assert eid > 0
    mid = storage.add_message(conn, eid, title="光模块涨价",
                              summary="龙头涨价10%", keywords="光模块,涨价", importance=4)
    assert mid > 0
    e = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    assert e["msg_count"] == 1
    assert e["latest_summary"] == "龙头涨价10%"
    m = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    assert m["event_id"] == eid
    assert m["title"] == "光模块涨价"
    conn.close()


def test_add_message_updates_event_and_indexes_fts(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "事件A", entity_type="stock")
    # trigram 分词要求查询 ≥3 字符，故给两条消息共同的 4 字关键词做检索
    storage.add_message(conn, eid, title="进展1", importance=3, keywords="事件进展")
    storage.add_message(conn, eid, title="进展2", importance=5, keywords="事件进展")
    e = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    assert e["msg_count"] == 2
    assert e["importance"] == 5                      # 取最大
    # FTS 已索引（两条消息都能被检索到）
    hits = conn.execute("SELECT COUNT(*) c FROM messages_fts WHERE keywords MATCH '事件进展'").fetchone()
    assert hits["c"] == 2
    conn.close()


def test_update_event_summary_and_resolve(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "事件B", entity_type="policy")
    storage.update_event_summary(conn, eid, "最新：补贴落地")
    assert conn.execute("SELECT latest_summary FROM events WHERE id=?", (eid,)).fetchone()["latest_summary"] == "最新：补贴落地"
    storage.resolve_event(conn, eid)
    e = conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    assert e["status"] == "resolved"
    assert e["resolved_at"] is not None
    conn.close()


def test_get_event_with_messages(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "事件C", entity_type="market")
    storage.add_message(conn, eid, title="消息1", importance=2)
    storage.add_message(conn, eid, title="消息2", importance=4)
    ev, msgs = storage.get_event_with_messages(conn, eid)
    assert ev["title"] == "事件C"
    assert len(msgs) == 2
    assert msgs[0]["importance"] == 4                 # 按重要度倒序
    conn.close()


def test_add_message_bad_event_raises_no_orphan(db_path):
    conn = _conn(db_path)
    with pytest.raises(ValueError):
        storage.add_message(conn, 99999, title="孤儿消息", importance=3)
    # 未留下孤儿消息
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 0
    # 后续正常操作不应带上孤儿行
    eid = storage.create_event(conn, "正常事件", entity_type="market")
    storage.add_message(conn, eid, title="正常消息")
    assert conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"] == 1
    conn.close()


def test_add_message_none_summary_preserves_latest(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "事件D", entity_type="market")
    storage.add_message(conn, eid, title="消息1", summary="摘要1")
    storage.add_message(conn, eid, title="消息2", summary=None)
    e = conn.execute("SELECT latest_summary FROM events WHERE id=?", (eid,)).fetchone()
    assert e["latest_summary"] == "摘要1"          # None 不覆盖旧摘要
    conn.close()


# ---------- 行业层级 + 关联 ----------

def test_link_event_industry_by_name(db_path):
    conn = _conn(db_path)
    eid = storage.create_event(conn, "光模块涨价", entity_type="industry")
    # 按名称关联，内部归一化；同一事件关联两个行业
    storage.link_event_industry(conn, eid, "光模块")
    storage.link_event_industry(conn, eid, "通信设备")
    inds = storage.event_industries(conn, eid)
    assert len(inds) == 2
    conn.close()


def test_link_event_industry_missing_event_raises(db_path):
    conn = _conn(db_path)
    import pytest
    with pytest.raises(ValueError):
        storage.link_event_industry(conn, 99999, "光模块")
    conn.close()


def test_set_industry_parent(db_path):
    conn = _conn(db_path)
    child = storage.upsert_industry(conn, "AI算力")
    parent = storage.upsert_industry(conn, "计算机设备")
    storage.set_industry_parent(conn, "AI算力", "计算机设备")
    assert conn.execute("SELECT parent_id FROM industries WHERE id=?", (child,)).fetchone()["parent_id"] == parent
    conn.close()


def test_relate_industries(db_path):
    conn = _conn(db_path)
    storage.relate_industries(conn, "计算机设备/AI算力", "精密温控节能设备/数据中心液冷", strength=60)
    rows = conn.execute("SELECT * FROM relations").fetchall()
    assert len(rows) == 1
    assert rows[0]["rel_type"] == "related"
    assert rows[0]["strength"] == 60
    conn.close()


def test_set_industry_parent_rejects_self_and_cycle(db_path):
    conn = _conn(db_path)
    # 自环：子=父
    with pytest.raises(ValueError):
        storage.set_industry_parent(conn, "AI算力", "AI算力")
    # 2-环：A→B 后再设 B→A
    storage.set_industry_parent(conn, "AI算力", "计算机设备")
    with pytest.raises(ValueError):
        storage.set_industry_parent(conn, "计算机设备", "AI算力")
    # 非法操作未留下脏数据
    assert conn.execute("SELECT parent_id FROM industries WHERE name='计算机设备'").fetchone()["parent_id"] is None
    conn.close()
