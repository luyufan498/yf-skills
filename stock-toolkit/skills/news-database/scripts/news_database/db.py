"""SQLite 连接与 schema。"""

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stocks (
    code         TEXT PRIMARY KEY,                -- 600519.SH
    name         TEXT NOT NULL,
    industry     TEXT,                            -- 所属行业名（冗余，便捷查询）
    market_cap   REAL,                            -- 总市值（亿元）
    is_watchlist INTEGER NOT NULL DEFAULT 0,
    priority     INTEGER NOT NULL DEFAULT 0,
    added_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS industries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    parent_id  INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL,
    entity_type       TEXT NOT NULL,              -- stock/industry/policy/market
    entity_id         INTEGER,                    -- 预留：行业事件曾计划存 industries.id，现经 event_industry 表关联，此列保留不用
    time_sensitivity  TEXT NOT NULL DEFAULT 'medium',   -- high/medium/low
    importance        INTEGER NOT NULL DEFAULT 3,       -- 1-5
    status            TEXT NOT NULL DEFAULT 'open',     -- open/resolved/irrelevant
    latest_summary    TEXT,
    started_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    resolved_at       TEXT,
    msg_count         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT,
    url         TEXT,
    source      TEXT,
    occurred_at TEXT,
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    importance  INTEGER NOT NULL DEFAULT 3,
    keywords    TEXT,                              -- 逗号分隔
    embedding   BLOB,                              -- 预留：语义扩展
    ts_updated  TEXT,                              -- 预留：语义扫描游标
    source_type TEXT NOT NULL DEFAULT 'media',     -- official/media/community/rumor
    confidence  INTEGER NOT NULL DEFAULT 4         -- 1-5，1=流言 5=官方
);
CREATE INDEX IF NOT EXISTS idx_messages_event ON messages(event_id);

CREATE TABLE IF NOT EXISTS event_stock (
    event_id   INTEGER NOT NULL,
    stock_code TEXT NOT NULL,
    relevance  INTEGER NOT NULL DEFAULT 50,
    PRIMARY KEY (event_id, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_event_stock_code ON event_stock(stock_code, event_id);

CREATE TABLE IF NOT EXISTS event_industry (
    event_id    INTEGER NOT NULL,
    industry_id INTEGER NOT NULL,
    relevance   INTEGER NOT NULL DEFAULT 50,
    PRIMARY KEY (event_id, industry_id)
);
CREATE INDEX IF NOT EXISTS idx_event_industry_id ON event_industry(industry_id, event_id);

CREATE TABLE IF NOT EXISTS industry_aliases (
    industry_id INTEGER NOT NULL,       -- 指向 industries.id
    alias_name  TEXT NOT NULL,
    PRIMARY KEY (industry_id, alias_name)
);
CREATE INDEX IF NOT EXISTS idx_industry_aliases_name ON industry_aliases(alias_name);

CREATE TABLE IF NOT EXISTS relations (
    from_type TEXT NOT NULL,
    from_id   TEXT NOT NULL,
    to_type   TEXT NOT NULL,
    to_id     TEXT NOT NULL,
    rel_type  TEXT NOT NULL,
    strength  INTEGER NOT NULL DEFAULT 50,
    PRIMARY KEY (from_type, from_id, to_type, to_id, rel_type)
);

CREATE TABLE IF NOT EXISTS refresh_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code  TEXT NOT NULL,
    signal_text TEXT NOT NULL,
    reason      TEXT,
    priority    INTEGER NOT NULL DEFAULT 3,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    status      TEXT NOT NULL DEFAULT 'pending'    -- pending/processing/done
);

CREATE TABLE IF NOT EXISTS scan_log (
    scope_type  TEXT NOT NULL,        -- stock/industry/policy/market
    scope_id    TEXT NOT NULL,        -- 股票代码/行业id/global
    last_scan   TEXT NOT NULL,        -- datetime('now','localtime')
    PRIMARY KEY (scope_type, scope_id)
);

-- 注意：messages_fts 是独立表，写入 messages 时必须同步插入（rowid = messages.id），否则搜索静默漂移
-- FTS5 全文索引（trigram 支持中文子串匹配）
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    title, summary, keywords, event_id UNINDEXED,
    tokenize='trigram'
);
"""


def connect(db_path):
    """打开（必要时创建）数据库连接，返回 row_factory=Row 的连接。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    """执行 schema。幂等，可重复调用。"""
    conn.executescript(SCHEMA_SQL)
    # 兼容旧库：新增列（已存在则跳过）
    try:
        conn.execute("ALTER TABLE stocks ADD COLUMN market_cap REAL")
    except sqlite3.OperationalError:
        pass  # 列已存在
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN source_type TEXT NOT NULL DEFAULT 'media'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN confidence INTEGER NOT NULL DEFAULT 4")
    except sqlite3.OperationalError:
        pass
    conn.commit()
