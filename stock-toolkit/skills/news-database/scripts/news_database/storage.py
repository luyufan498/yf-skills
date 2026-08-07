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
    """新增或返回已有行业 id（原子 upsert，避免并发竞态）。"""
    conn.execute("INSERT INTO industries (name, parent_id) VALUES (?, ?) "
                 "ON CONFLICT(name) DO NOTHING", (name, parent_id))
    conn.commit()
    return conn.execute("SELECT id FROM industries WHERE name=?", (name,)).fetchone()["id"]


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
    cur = conn.execute("""
        INSERT INTO messages (event_id, title, summary, url, source, occurred_at,
                              fetched_at, importance, keywords)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now','localtime'), ?, ?)
    """, (event_id, title, summary, url, source, occurred_at, int(importance), keywords))
    mid = cur.lastrowid
    _index_message(conn, mid, title, summary, keywords)
    # 更新事件
    e = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    new_importance = max(e["importance"], int(importance)) if e["importance"] else int(importance)
    conn.execute("""
        UPDATE events SET msg_count = msg_count + 1,
            importance = ?, latest_summary = COALESCE(?, latest_summary),
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (new_importance, summary, event_id))
    conn.commit()
    return mid


def update_event_summary(conn, event_id, latest_summary, importance=None):
    """刷新事件最新摘要（不覆盖历史消息）。"""
    if importance is not None:
        conn.execute("""
            UPDATE events SET latest_summary = ?, importance = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (latest_summary, int(importance), event_id))
    else:
        conn.execute("""
            UPDATE events SET latest_summary = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (latest_summary, event_id))
    conn.commit()


def resolve_event(conn, event_id):
    """标记事件结束。"""
    conn.execute("""
        UPDATE events SET status='resolved', resolved_at=datetime('now','localtime')
        WHERE id = ?
    """, (event_id,))
    conn.commit()


def get_event_with_messages(conn, event_id):
    """返回 (event_row, [message_rows])，消息按重要度倒序、时间倒序。"""
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        return None, []
    msgs = conn.execute("""
        SELECT * FROM messages WHERE event_id=? ORDER BY importance DESC, fetched_at DESC
    """, (event_id,)).fetchall()
    return ev, msgs
