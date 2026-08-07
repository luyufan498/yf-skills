"""写入层：实体、事件、消息、关系、刷新请求。"""

from news_database.db import connect  # noqa: F401  (conn type 一致性；非必需)

# ---------- 实体 ----------

def upsert_stock(conn, code, name, industry=None, is_watchlist=0, priority=0):
    """新增或更新股票。返回 code。"""
    conn.execute("""
        INSERT INTO stocks (code, name, industry, is_watchlist, priority, added_at)
        VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name,
            industry=COALESCE(excluded.industry, stocks.industry),
            is_watchlist=excluded.is_watchlist,
            priority=excluded.priority
    """, (code, name, industry, int(is_watchlist), int(priority)))
    conn.commit()
    return code


def get_stock(conn, code):
    return conn.execute("SELECT * FROM stocks WHERE code=?", (code,)).fetchone()


def upsert_industry(conn, name, parent_id=None):
    """新增或返回已有行业 id。"""
    row = conn.execute("SELECT id FROM industries WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO industries (name, parent_id) VALUES (?, ?)", (name, parent_id))
    conn.commit()
    return cur.lastrowid


def get_industry_by_name(conn, name):
    return conn.execute("SELECT * FROM industries WHERE name=?", (name,)).fetchone()
