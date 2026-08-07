"""写入层：实体、事件、消息、关系、刷新请求。"""

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
    """新增或返回已有行业 id（先精确查 name，再查别名归一化，避免分裂）。"""
    row = conn.execute("SELECT id FROM industries WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT industry_id AS id FROM industry_aliases WHERE alias_name=?", (name,)).fetchone()
    if row:
        return row["id"]
    conn.execute("INSERT INTO industries (name, parent_id) VALUES (?, ?) "
                 "ON CONFLICT(name) DO NOTHING", (name, parent_id))
    conn.commit()
    return conn.execute("SELECT id FROM industries WHERE name=?", (name,)).fetchone()["id"]


def add_industry_alias(conn, industry_id, alias):
    """登记行业别名（幂等）。"""
    conn.execute("INSERT INTO industry_aliases (industry_id, alias_name) VALUES (?, ?) "
                 "ON CONFLICT(industry_id, alias_name) DO NOTHING", (industry_id, alias))
    conn.commit()


def list_industry_aliases(conn, industry_id):
    """列出某行业所有别名。"""
    return [r["alias_name"] for r in conn.execute(
        "SELECT alias_name FROM industry_aliases WHERE industry_id=?", (industry_id,)).fetchall()]


def get_industry_by_name(conn, name):
    return conn.execute("SELECT * FROM industries WHERE name=?", (name,)).fetchone()


# ---------- 事件 + 消息 ----------

def create_event(conn, title, entity_type="market", entity_id=None,
                 time_sensitivity="medium", importance=3):
    """创建事件，返回事件 id。"""
    cur = conn.execute("""
        INSERT INTO events (title, entity_type, entity_id, time_sensitivity,
                            importance, status, started_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'open', datetime('now','localtime'), datetime('now','localtime'))
    """, (title, entity_type, entity_id, time_sensitivity, int(importance)))
    conn.commit()
    return cur.lastrowid


def _index_message(conn, mid, title, summary, keywords):
    """同步插入 FTS 索引（standalone fts5 表，不做外部 content 触发器）。"""
    conn.execute(
        "INSERT INTO messages_fts (rowid, title, summary, keywords, event_id) VALUES (?, ?, ?, ?, ?)",
        (mid, title or "", summary or "", keywords or "", None),
    )


def add_message(conn, event_id, title, summary=None, url=None, source=None,
                occurred_at=None, importance=3, keywords=None):
    """向事件追加一条消息，更新事件的 msg_count/importance/latest_summary。返回消息 id。"""
    # 先校验事件存在，避免孤儿消息
    ev = conn.execute("SELECT importance FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        raise ValueError(f"事件 {event_id} 不存在")
    cur = conn.execute("""
        INSERT INTO messages (event_id, title, summary, url, source, occurred_at,
                              fetched_at, importance, keywords)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), ?, ?)
    """, (event_id, title, summary, url, source, occurred_at, int(importance), keywords))
    mid = cur.lastrowid
    _index_message(conn, mid, title, summary, keywords)
    new_importance = max(ev["importance"] or 0, int(importance))
    conn.execute("""
        UPDATE events SET msg_count = msg_count + 1,
            importance = ?, latest_summary = COALESCE(?, latest_summary),
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (new_importance, summary, event_id))
    conn.commit()
    return mid


def update_event_summary(conn, event_id, latest_summary, importance=None):
    """刷新事件最新摘要（不覆盖历史消息）。返回受影响行数。"""
    if importance is not None:
        cur = conn.execute("""
            UPDATE events SET latest_summary = ?, importance = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (latest_summary, int(importance), event_id))
    else:
        cur = conn.execute("""
            UPDATE events SET latest_summary = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (latest_summary, event_id))
    conn.commit()
    return cur.rowcount


def resolve_event(conn, event_id):
    """标记事件结束。返回受影响行数。"""
    cur = conn.execute("""
        UPDATE events SET status='resolved', resolved_at=datetime('now','localtime')
        WHERE id = ?
    """, (event_id,))
    conn.commit()
    return cur.rowcount


def get_event_with_messages(conn, event_id):
    """返回 (event_row, [message_rows])，消息按重要度倒序、时间倒序。"""
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        return None, []
    msgs = conn.execute("""
        SELECT * FROM messages WHERE event_id=? ORDER BY importance DESC, fetched_at DESC
    """, (event_id,)).fetchall()
    return ev, msgs


# ---------- 事件↔实体 关联 ----------

def link_event_stock(conn, event_id, stock_code, relevance=50):
    """关联事件↔股票（多对多，UPSERT）。"""
    if not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
        raise ValueError(f"事件 {event_id} 不存在")
    conn.execute("""
        INSERT INTO event_stock (event_id, stock_code, relevance) VALUES (?, ?, ?)
        ON CONFLICT(event_id, stock_code) DO UPDATE SET relevance=excluded.relevance
    """, (event_id, stock_code, int(relevance)))
    conn.commit()


def link_event_industry(conn, event_id, industry_id, relevance=50):
    """关联事件↔行业（多对多，UPSERT）。"""
    if not conn.execute("SELECT id FROM events WHERE id=?", (event_id,)).fetchone():
        raise ValueError(f"事件 {event_id} 不存在")
    conn.execute("""
        INSERT INTO event_industry (event_id, industry_id, relevance) VALUES (?, ?, ?)
        ON CONFLICT(event_id, industry_id) DO UPDATE SET relevance=excluded.relevance
    """, (event_id, industry_id, int(relevance)))
    conn.commit()


def event_stocks(conn, event_id):
    """返回事件关联的股票列表（relevance 倒序）。"""
    return conn.execute("""
        SELECT * FROM event_stock WHERE event_id=? ORDER BY relevance DESC
    """, (event_id,)).fetchall()


def event_industries(conn, event_id):
    """返回事件关联的行业列表（relevance 倒序）。"""
    return conn.execute(
        "SELECT * FROM event_industry WHERE event_id=? ORDER BY relevance DESC", (event_id,)).fetchall()


# ---------- 实体间关系 ----------

def add_relation(conn, from_type, from_id, to_type, to_id, rel_type, strength=50):
    """记录实体间关系（UPSERT）。"""
    conn.execute("""
        INSERT INTO relations (from_type, from_id, to_type, to_id, rel_type, strength)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(from_type, from_id, to_type, to_id, rel_type) DO UPDATE SET strength=excluded.strength
    """, (from_type, from_id, to_type, to_id, rel_type, int(strength)))
    conn.commit()


def related_stocks(conn, stock_code, rel_type=None):
    """返回与某股票有关系的股票代码列表（双向，strength 倒序）。

    同时考虑 from→to 和 to→from 两个方向（关系是互惠的），按 strength 倒序。
    """
    # 关系互惠：from→to 与 to→from 都算相关；UNION ALL 后按 code 去重取最大 strength
    rel_clause = " AND rel_type=?" if rel_type else ""
    sql = f"""
        SELECT to_id AS code, strength FROM relations
        WHERE from_type='stock' AND from_id=? AND to_type='stock'{rel_clause}
        UNION ALL
        SELECT from_id AS code, strength FROM relations
        WHERE to_type='stock' AND to_id=? AND from_type='stock'{rel_clause}
    """
    params = [stock_code, stock_code]
    if rel_type:
        params = [stock_code, rel_type, stock_code, rel_type]
    sql = f"SELECT code, MAX(strength) AS strength FROM ({sql}) GROUP BY code ORDER BY strength DESC"
    return [r["code"] for r in conn.execute(sql, params).fetchall()]


# ---------- 刷新请求队列 ----------

def create_refresh_request(conn, stock_code, signal, reason=None, priority=3):
    """分析 agent 检测到异动时写入刷新请求。返回 id。"""
    cur = conn.execute("""
        INSERT INTO refresh_requests (stock_code, signal_text, reason, priority, created_at, status)
        VALUES (?, ?, ?, ?, datetime('now','localtime'), 'pending')
    """, (stock_code, signal, reason, int(priority)))
    conn.commit()
    return cur.lastrowid


def list_refresh_requests(conn, status="pending"):
    """列出某状态的请求，按优先级倒序、创建时间正序（id 兜底保证确定序）。"""
    return conn.execute("""
        SELECT * FROM refresh_requests WHERE status=? ORDER BY priority DESC, created_at ASC, id ASC
    """, (status,)).fetchall()


def ack_refresh_request(conn, request_id):
    """新闻 agent 处理完后标记完成。返回受影响行数。"""
    cur = conn.execute("UPDATE refresh_requests SET status='done' WHERE id=?", (request_id,))
    conn.commit()
    return cur.rowcount
