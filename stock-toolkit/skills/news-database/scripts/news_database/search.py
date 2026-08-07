"""FTS 检索：lookup（去重/归属判断入口）+ search（全文检索）。

FTS5 trigram tokenizer 把正文切成连续 3 字符词元。为了让「部分重叠」的长查询也能
命中（如 "光模块涨价" 命中 "光模块龙头涨价"），把查询拆成 trigram 词元后用 OR
连接构造 MATCH 表达式。短查询（<3 字符）trigram 无法匹配，回退到 LIKE。

lookup 是事件级入口：新闻 agent 入库前调用，判断 归属(merge)/新建(create)/跳过(skip)。
query 永远返回事件列表（每条代表一个事件，取该事件下最新/最重要的命中消息）。
entity_type 过滤内联在 SQL 中、先于 LIMIT，避免 top-N 截断把目标事件挤出候选。

LIKE 回退按 token 化查询做：把查询按标点/空白切词，逐词匹配消息 title/summary/
keywords 及事件 title。措辞不同但含同一主题词（如查"预亏 公告"命中"业绩预亏"）时
也能召回，避免新建重复事件。
"""

import re
import sqlite3

# LIKE 回退的查询切词：按中英文标点/空白切分，避免把整个长句当单个子串。
_TOKEN_SPLIT = re.compile(r"[\s,，。;；:：、]+")

# 事件级候选查询：FTS 命中消息后按事件去重（每个事件取最重/最新一条代表消息）。
# entity_type 过滤在 WHERE 中先于 LIMIT；窗口 ORDER BY 带 m.id DESC 保证确定序。
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
               ORDER BY m.importance DESC, m.fetched_at DESC, m.id DESC
           ) AS rn
    FROM messages_fts f
    JOIN messages m ON m.id = f.rowid
    JOIN events e   ON e.id = m.event_id
    WHERE messages_fts MATCH ?
      AND (? IS NULL OR e.entity_type = ?)
)
WHERE rn = 1
ORDER BY event_importance DESC, occurred_at DESC, event_id DESC
LIMIT ?
"""

# LIKE 回退模板：{where} 由 token 化后的 OR 子句填充（见 _tokenized_like_sql）。
# 同样按事件去重、entity_type 过滤先于 LIMIT；窗口 ORDER BY 带 m.id DESC 保证确定序。
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
               ORDER BY m.importance DESC, m.fetched_at DESC, m.id DESC
           ) AS rn
    FROM messages m
    JOIN events e ON e.id = m.event_id
    WHERE ({where})
      AND (? IS NULL OR e.entity_type = ?)
)
WHERE rn = 1
ORDER BY event_importance DESC, occurred_at DESC, event_id DESC
LIMIT ?
"""

# 全文检索（消息级，不去重）：返回完整消息行 + 事件标题。
_SEARCH_SQL = """
SELECT m.*, e.title AS event_title
FROM messages_fts f
JOIN messages m ON m.id = f.rowid
JOIN events e   ON e.id = m.event_id
WHERE messages_fts MATCH ?
ORDER BY m.importance DESC, m.fetched_at DESC, m.id DESC
LIMIT ?
"""

_SEARCH_LIKE_SQL = """
SELECT m.*, e.title AS event_title
FROM messages m
JOIN events e ON e.id = m.event_id
WHERE (m.title LIKE ? OR m.summary LIKE ? OR m.keywords LIKE ?)
ORDER BY m.importance DESC, m.fetched_at DESC, m.id DESC
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


def _tokens(query):
    """把查询按标点/空白切词，返回 ≥2 字符的词元（用于 LIKE 回退）。"""
    return [t for t in _TOKEN_SPLIT.split(query or "") if len(t) >= 2]


def _tokenized_like_sql(tokens):
    """每个 token 生成 title/summary/keywords/事件标题 LIKE，token 间 OR。"""
    clause = " OR ".join(
        "(m.title LIKE ? OR m.summary LIKE ? OR m.keywords LIKE ? OR e.title LIKE ?)"
        for _ in tokens
    )
    return _LOOKUP_LIKE_SQL.format(where=clause)


def lookup_events(conn, query, entity_type=None, limit=10):
    """语义去重查询：返回与 query 相关的事件候选（事件级，已按重要度倒序）。

    供新闻 agent 入库前判断：新进展→归属 / 全新→新建 / 无新信息→跳过。
    entity_type 过滤内联在 SQL 中先于 LIMIT，避免候选被截断漏报。
    FTS（trigram）无结果或短查询时，回退到按 token 拆分的 LIKE（含事件标题），
    提高措辞不同但同主题的查询召回（如查"预亏公告"命中"业绩预亏"事件）。
    """
    query = (query or "").strip()
    if not query:
        return []
    match = _match_expr(query)
    if match is not None:
        rows = _run(conn, _LOOKUP_SQL, (match, entity_type, entity_type, int(limit)))
        if rows:
            return rows
    # 短查询或 FTS 无结果 → token 化 LIKE 回退
    tokens = _tokens(query)
    if not tokens:
        return []
    params = [f"%{t}%" for t in tokens for _ in range(4)]
    params += [entity_type, entity_type, int(limit)]
    return _run(conn, _tokenized_like_sql(tokens), params)


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
