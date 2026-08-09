"""行业回填同步：别名登记 + 行业关联 + 层级建立。"""

from news_database.db import connect, init_db
from news_database import industry_sync


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def test_apply_sync_config_aliases_relations_hierarchy(db_path):
    conn = _conn(db_path)
    config = {
        "aliases": {
            "计算机设备/AI算力": ["AI算力", "算力设备"],
            "精密温控节能设备/数据中心液冷": ["液冷", "数据中心液冷"],
        },
        "relations": [
            ("计算机设备/AI算力", "精密温控节能设备/数据中心液冷", 60),
        ],
        "hierarchy": {
            "3D打印": "专用设备制造",
        },
    }
    summary = industry_sync.apply_sync_config(conn, config)
    # 别名登记
    ai_id = conn.execute("SELECT id FROM industries WHERE name='计算机设备/AI算力'").fetchone()["id"]
    aliases = conn.execute("SELECT alias_name FROM industry_aliases WHERE industry_id=?",
                           (ai_id,)).fetchall()
    assert {a["alias_name"] for a in aliases} >= {"AI算力", "算力设备"}
    # 关联
    rel = conn.execute("SELECT * FROM relations").fetchall()
    assert len(rel) == 1 and rel[0]["strength"] == 60
    # 层级
    child_id = conn.execute("SELECT id FROM industries WHERE name='3D打印'").fetchone()["id"]
    parent_id = conn.execute("SELECT id FROM industries WHERE name='专用设备制造'").fetchone()["id"]
    assert conn.execute("SELECT parent_id FROM industries WHERE id=?", (child_id,)).fetchone()["parent_id"] == parent_id
    # summary 反映操作数
    assert summary["aliases_added"] >= 4
    assert summary["relations_added"] == 1
    assert summary["parents_set"] == 1
    conn.close()


def test_apply_sync_config_idempotent(db_path):
    conn = _conn(db_path)
    config = {
        "aliases": {"光模块": ["光模块行业"]},
        "relations": [("光模块", "AI算力", 50)],
        "hierarchy": {},
    }
    industry_sync.apply_sync_config(conn, config)
    industry_sync.apply_sync_config(conn, config)  # 再跑一次不应出错/重复
    aliases = conn.execute("SELECT COUNT(*) c FROM industry_aliases").fetchone()["c"]
    assert aliases == 1
    rels = conn.execute("SELECT COUNT(*) c FROM relations").fetchone()["c"]
    assert rels == 1
    conn.close()


def test_apply_sync_config_dry_run_no_write(db_path):
    conn = _conn(db_path)
    config = {
        "aliases": {"光模块": ["光模块行业"]},
        "relations": [("光模块", "AI算力", 50)],
        "hierarchy": {"3D打印": "专用设备制造"},
    }
    summary = industry_sync.apply_sync_config(conn, config, dry_run=True)
    # 预览摘要反映配置规模
    assert summary == {"aliases_added": 1, "relations_added": 1, "parents_set": 1,
                       "industry_links": 0, "skipped_links": 0}
    # 但不写任何数据
    assert conn.execute("SELECT COUNT(*) c FROM industries").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM industry_aliases").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM relations").fetchone()["c"] == 0
    conn.close()


def test_apply_sync_config_event_industry_links(db_path):
    conn = _conn(db_path)
    from news_database import storage
    eid = storage.create_event(conn, "液冷行业进入业绩兑现期", entity_type="industry")
    # 事件需有 confidence>=3 的消息才被默认查询返回（决策依据）
    storage.add_message(conn, eid, "液冷行业进入业绩兑现期", source_type="media")
    config = {
        "aliases": {"精密温控节能设备/数据中心液冷": ["液冷"]},
        "relations": [], "hierarchy": {},
        "event_industry_links": {"精密温控节能设备/数据中心液冷": [eid]},
    }
    summary = industry_sync.apply_sync_config(conn, config)
    assert summary["industry_links"] == 1
    # 用别名查询应能找到事件
    from news_database import query
    evs = query.query_industry(conn, "液冷")
    assert any(ev["id"] == eid for ev in evs)
    conn.close()


def test_apply_sync_config_skips_missing_events(db_path):
    conn = _conn(db_path)
    config = {
        "aliases": {}, "relations": [], "hierarchy": {},
        "event_industry_links": {"精密温控节能设备/数据中心液冷": [18, 58, 62, 65]},
    }
    summary = industry_sync.apply_sync_config(conn, config)
    # 空库：缺失事件被跳过，不崩溃
    assert summary["industry_links"] == 4
    assert summary.get("skipped_links", 0) == 4
    assert conn.execute("SELECT COUNT(*) c FROM event_industry").fetchone()["c"] == 0
    conn.close()
