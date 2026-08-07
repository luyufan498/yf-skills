"""行业回填同步：从配置登记别名、建立行业关联、设置层级。

用于一次性修复基础构筑留下的行业分裂/缺层级问题。SYNC_CONFIG 里的
行业名通过 upsert_industry 归一化，重复运行幂等。
"""

from news_database import storage

# 现有库回填配置（规范名 → 别名列表）
SYNC_CONFIG = {
    "aliases": {
        "计算机设备/AI算力": ["AI算力", "算力设备", "AI服务器"],
        "精密温控节能设备/数据中心液冷": ["液冷", "数据中心液冷", "精密温控", "温控设备"],
        "新能源汽车": ["新能源车", "智能电动车"],
        "化学纤维/锦纶新材料": ["锦纶", "锦纶新材料", "化学纤维", "尼龙"],
        "3D打印": ["增材制造"],
        "磁性材料": ["电感材料", "软磁材料"],
        "OLED": ["AMOLED", "OLED材料"],
        "橡胶和塑料制品业": ["橡胶塑料", "橡胶制品"],
        "专用设备制造": ["打印设备"],
    },
    # 相关但不同的行业（relations 表，rel_type='related'）
    "relations": [
        ("计算机设备/AI算力", "精密温控节能设备/数据中心液冷", 60),
        ("磁性材料", "计算机设备/AI算力", 55),
    ],
    # 大行业中的小方向（parent_id）
    "hierarchy": {
        "3D打印": "专用设备制造",
    },
    # 既有事件补充行业关联（修复基础构筑的挂错行业问题：液冷事件挂到 #1/#4 而非 #37）
    "event_industry_links": {
        "精密温控节能设备/数据中心液冷": [18, 58, 62, 65],
    },
}


def apply_sync_config(conn, config=None, dry_run=False):
    """应用回填配置。返回摘要 dict（aliases_added/relations_added/parents_set/industry_links）。

    config 结构同 SYNC_CONFIG：{"aliases": {规范名: [别名...]},
    "relations": [(行业A, 行业B, strength)...], "hierarchy": {子行业: 父行业},
    "event_industry_links": {行业名: [event_id...]}}
    dry_run=True 时不写库，仅按配置规模计算摘要（供 --dry-run 预览）。
    """
    config = SYNC_CONFIG if config is None else config
    aliases = config.get("aliases", {})
    relations = config.get("relations", [])
    hierarchy = config.get("hierarchy", {})
    event_links = config.get("event_industry_links", {})
    summary = {
        "aliases_added": sum(len(a) for a in aliases.values()),
        "relations_added": len(relations),
        "parents_set": len(hierarchy),
        "industry_links": sum(len(ids) for ids in event_links.values()),
    }
    if dry_run:
        return summary
    for canonical, alias_list in aliases.items():
        iid = storage.upsert_industry(conn, canonical)
        for alias in alias_list:
            storage.add_industry_alias(conn, iid, alias)
    for a, b, strength in relations:
        storage.relate_industries(conn, a, b, strength=strength)
    for child, parent in hierarchy.items():
        storage.set_industry_parent(conn, child, parent)
    for canonical, event_ids in event_links.items():
        storage.upsert_industry(conn, canonical)
        for eid in event_ids:
            # link_event_industry 会校验事件存在（缺 event_id 抛 ValueError）
            storage.link_event_industry(conn, eid, canonical, relevance=60)
    return summary
