"""查询层（只读）：分析 agent 用。"""


def _with_days(sql, params, days):
    if days:
        sql += " AND e.updated_at >= datetime('now','localtime', ?)"
        params.append(f"-{int(days)} days")
    return sql, params


def query_stock(conn, stock_code, days=None):
    """某股票关联的事件（relevance/importance 排序）。"""
    sql = """
        SELECT DISTINCT e.* FROM events e
        JOIN event_stock es ON es.event_id = e.id
        WHERE es.stock_code = ?
    """
    params = [stock_code]
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC"
    return conn.execute(sql, params).fetchall()


def query_industry(conn, industry_name, days=None):
    """某行业关联的事件（通过 industries.name）。"""
    sql = """
        SELECT DISTINCT e.* FROM events e
        JOIN event_industry ei ON ei.event_id = e.id
        JOIN industries i      ON i.id = ei.industry_id
        WHERE i.name = ?
    """
    params = [industry_name]
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC"
    return conn.execute(sql, params).fetchall()


def query_market(conn, days=None):
    """宏观/政策/大盘 全局层事件。"""
    sql = "SELECT e.* FROM events e WHERE e.entity_type IN ('macro','policy','market')"
    params = []
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC"
    return conn.execute(sql, params).fetchall()


def query_important(conn, min_importance=4, days=None):
    """高重要度事件（默认 ≥4）。"""
    sql = "SELECT e.* FROM events e WHERE e.importance >= ?"
    params = [int(min_importance)]
    sql, params = _with_days(sql, params, days)
    sql += " ORDER BY e.importance DESC, e.updated_at DESC"
    return conn.execute(sql, params).fetchall()
