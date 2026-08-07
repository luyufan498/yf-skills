"""FTS 检索：lookup（去重/归属判断入口）+ search（全文检索）。

FTS5 trigram tokenizer 把正文切成连续 3 字符词元。为了让「部分重叠」的长查询也能
命中（如 "光模块涨价" 命中 "光模块龙头涨价"），把查询拆成 trigram 词元后用 OR
连接构造 MATCH 表达式。短查询（<3 字符）trigram 无法匹配，回退到 LIKE。

lookup 是事件级入口：新闻 agent 入库前调用，判断 归属(merge)/新建(create)/跳过(skip)。
query 永远返回事件列表（每条代表一个事件，取该事件下最新/最重要的命中消息）。
"""

import sqlite3

# 事件级候选查询：FTS 命中消息后按事件去重（每个事件取最重/最新一条代表消息）。
_LOOKUP_SQL = """
SELECT event_id, event_title, event_status, event_importance,
       matched_title, matched_summary, occurred_at
FROM (
    SELECT m.event_id    AS event_id,
           e.title       AS event_title,
           e.status      AS event_status,
           e.importance  AS event_importance,
           m.title       AS matched_title,
           m.summary     AS matched_summary,
           m.occurred_at AS occurred_at,
           ROW_NUMBER() OVER (
               PARTITION BY m.event_id
               ORDER BY m.importance DESC, m.fetched_at DESC
           ) AS rn
    FROM messages_fts f
    JOIN messages m ON m.id = f.rowid
    JOIN events e   ON e.id = m.event_id
    WHERE messages_fts MATCH ?
)
WHERE rn = 1
ORDER BY event_importance DESC, occurred_at DESC
LIMIT ?
"""

# trigram 不可用/短查询时的 LIKE 回退（同样按事件去重）。
_LOOKUP_LIKE_SQL = """
SELECT event_id, event_title, event_status, event_importance,
       matched_title, matched_summary, occurred_at
FROM (
    SELECT m.event_id    AS event_id,
           e.title       AS event_title,
           e.status      AS event_status,
           e.importance  AS event_importance,
           m.title       AS matched_title,
           m.summary     AS matched_summary,
           m.occurred_at AS occurred_at,
           ROW_NUMBER() OVER (
               PARTITION BY m.event_id
               ORDER BY m.importance DESC, m.fetched_at DESC
           ) AS rn
    FROM messages m
    JOIN events e ON e.id = m.event_id
    WHERE (m.title LIKE ? OR m.summary LIKE ? OR m.keywords LIKE ?)
)
WHERE rn = 1
ORDER BY event_importance DESC, occurred_at DESC
LIMIT ?
"""

# 全文检索（消息级，不去重）：返回完整消息行 + 事件标题。
_SEARCH_SQL = """
SELECT m.*, e.title AS event_title
FROM messages_fts f
JOIN messages m ON m.id = f.rowid
JOIN events e   ON e.id = m.event_id
WHERE messages_fts MATCH ?
ORDER BY m.importance DESC, m.fetched_at DESC
LIMIT ?
"""

_SEARCH_LIKE_SQL = """
SELECT m.*, e.title AS event_title
FROM messages m
JOIN events e ON e.id = m.event_id
WHERE (m.title LIKE ? OR m.summary LIKE ? OR m.keywords LIKE ?)
ORDER BY m.importance DESC, m.fetched_at DESC
LIMIT ?
"""


def _quoted(term):
    """把词包成 FTS 短语，避免特殊字符被解析成运算符。"""
    return '"' + term.replace('"', '""') + '"'


def _trigrams(text):
    """把查询切成连续 3 字符子串（对应 FTS5 trigram 词元）；不足 3 字符返回空。"""
    text = (text or "").strip()
    return [text[i:i + 3] for i in range(len(text) - 2)] if len(text) >= 3 else []


def _match_expr(query):
    """构造 FTS MATCH 表达式：trigram 词元 OR 连接，子串部分重叠即可命中。

    短查询（<3 字符）无 trigram 词元，返回 None，调用方回退 LIKE。
    """
    grams = _trigrams(query)
    return " OR ".join(_quoted(g) for g in grams) or None


def _run(conn, sql, params):
    """执行并返回行列表；FTS 不可用/表达式非法时静默降级为空。"""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def lookup_events(conn, query, entity_type=None, limit=10):
    """语义去重查询：返回与 query 相关的事件候选（事件级，已按重要度倒序）。

    供新闻 agent 入库前判断：新进展→归属 / 全新→新建 / 无新信息→跳过。
    短查询（<3 字符）或 trigram 不可用时自动回退 LIKE。
    """
    query = (query or "").strip()
    if not query:
        return []
    match = _match_expr(query)
    rows = _run(conn, _LOOKUP_SQL, (match, int(limit))) if match else []
    if not rows:
        rows = _run(conn, _LOOKUP_LIKE_SQL,
                    (f"%{query}%", f"%{query}%", f"%{query}%", int(limit)))
    if entity_type:
        ids = {r["event_id"] for r in rows}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            valid = {r["id"] for r in conn.execute(
                f"SELECT id FROM events WHERE entity_type=? AND id IN ({placeholders})",
                (entity_type, *ids))}
            rows = [r for r in rows if r["event_id"] in valid]
    return rows[:limit]


def search_messages(conn, query, limit=10):
    """全文检索：返回匹配的消息行列表（含 event_title），按重要度倒序。"""
    query = (query or "").strip()
    if not query:
        return []
    match = _match_expr(query)
    rows = _run(conn, _SEARCH_SQL, (match, int(limit))) if match else []
    if not rows:
        rows = _run(conn, _SEARCH_LIKE_SQL,
                    (f"%{query}%", f"%{query}%", f"%{query}%", int(limit)))
    return rows
