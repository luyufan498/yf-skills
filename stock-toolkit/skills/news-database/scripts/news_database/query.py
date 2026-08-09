"""查询层（只读）：分析 agent 用。"""


def _with_days(sql, params, days):
    if days:
        sql += " AND e.updated_at >= datetime('now','localtime', ?)"
        params.append(f"-{int(days)} days")
    return sql, params


def _confidence_filter(include_low_confidence):
    """返回置信度过滤 SQL 子句。

    事件级查询：默认只返回「至少有一条 confidence>=3 消息」的事件。
    include_low_confidence=True 时不加过滤。
    """
    if include_low_confidence:
        return ""
    return """ AND EXISTS (
        SELECT 1 FROM messages m2
        WHERE m2.event_id = e.id AND m2.confidence >= 3
    )"""


def query_stock(conn, stock_code, days=None, include_low_confidence=False):
    """某股票关联的事件（按重要度/更新时间倒序）。

    include_low_confidence=False（默认）：只返回含 confidence>=3 消息的事件，
    即决策依据（官方/媒体/已证实）。True 时也含仅舆情/流言的事件。
    """
    conf_clause = _confidence_filter(include_low_confidence)
    sql = f"""
        SELECT DISTINCT e.* FROM events e
        JOIN event_stock es ON es.event_id = e.id
        WHERE es.stock_code = ?{conf_clause}
    """
    params = [stock_code]
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC, e.id DESC"
    return conn.execute(sql, params).fetchall()


def resolve_industry_ids(conn, industry_name):
    """把查询名解析为行业 id 列表（含父带子展开）。未命中返回 None。

    解析链：精确 name → 别名 → 未命中返回 None。
    命中后：返回该行业 id + 所有直接子行业 id（父带子）。
    """
    row = conn.execute("SELECT id FROM industries WHERE name=?", (industry_name,)).fetchone()
    if not row:
        row = conn.execute(
            "SELECT industry_id AS id FROM industry_aliases WHERE alias_name=?", (industry_name,)).fetchone()
    if not row:
        return None
    parent_id = row["id"]
    ids = [parent_id]
    for r in conn.execute("SELECT id FROM industries WHERE parent_id=?", (parent_id,)):
        ids.append(r["id"])
    return ids


def query_industry_by_ids(conn, industry_ids, days=None, include_low_confidence=False):
    """按行业 id 列表查事件。"""
    placeholders = ",".join("?" for _ in industry_ids)
    conf_clause = _confidence_filter(include_low_confidence)
    sql = f"""
        SELECT DISTINCT e.* FROM events e
        JOIN event_industry ei ON ei.event_id = e.id
        WHERE ei.industry_id IN ({placeholders}){conf_clause}
    """
    params = list(industry_ids)
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC, e.id DESC"
    return conn.execute(sql, params).fetchall()


def query_industry(conn, industry_name, days=None, include_low_confidence=False):
    """某行业关联的事件（支持别名 + 父带子展开）。未命中返回 []。"""
    ids = resolve_industry_ids(conn, industry_name)
    if not ids:
        return []
    return query_industry_by_ids(conn, ids, days=days, include_low_confidence=include_low_confidence)


def suggest_industries(conn, query_text, limit=5):
    """模糊匹配候选行业（名称/别名 LIKE），用于提示匹配。"""
    like = f"%{query_text}%"
    return conn.execute("""
        SELECT DISTINCT i.id, i.name, i.parent_id FROM industries i
        WHERE i.name LIKE ? OR EXISTS (
            SELECT 1 FROM industry_aliases a WHERE a.industry_id=i.id AND a.alias_name LIKE ?
        )
        ORDER BY i.name LIMIT ?
    """, (like, like, limit)).fetchall()


def related_industries(conn, industry_id):
    """返回某行业的所有关联行业（relations 表，rel_type='related'）。

    关系互惠：from→to 与 to→from 都算相关，按 strength 倒序。
    relations 的 from_id/to_id 存的是 str(id)，故按 str 匹配。
    """
    return conn.execute("""
        SELECT to_id AS other_id, strength FROM relations
        WHERE from_type='industry' AND from_id=?
        UNION
        SELECT from_id AS other_id, strength FROM relations
        WHERE to_type='industry' AND to_id=?
        ORDER BY strength DESC
    """, (str(industry_id), str(industry_id))).fetchall()


def query_market(conn, days=None, include_low_confidence=False):
    """宏观/政策/大盘 全局层事件。"""
    conf_clause = _confidence_filter(include_low_confidence)
    sql = f"SELECT e.* FROM events e WHERE e.entity_type IN ('policy','market'){conf_clause}"
    params = []
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC, e.id DESC"
    return conn.execute(sql, params).fetchall()


def query_important(conn, min_importance=4, days=None, include_low_confidence=False):
    """高重要度事件（默认 ≥4）。"""
    conf_clause = _confidence_filter(include_low_confidence)
    sql = f"SELECT e.* FROM events e WHERE e.importance >= ?{conf_clause}"
    params = [int(min_importance)]
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC, e.id DESC"
    return conn.execute(sql, params).fetchall()
