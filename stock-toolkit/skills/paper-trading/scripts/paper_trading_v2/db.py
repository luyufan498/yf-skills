"""SQLite 连接 + schema DDL + 迁移版本"""
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL,
    migrated_at TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_name TEXT UNIQUE NOT NULL,
    stock_code TEXT,
    capital_total REAL NOT NULL,
    capital_available REAL NOT NULL,
    capital_used REAL NOT NULL,
    fifo_index INTEGER DEFAULT -1,
    fifo_offset REAL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    operation TEXT NOT NULL,
    stock_code TEXT,
    quantity INTEGER,
    price REAL,
    total_cost REAL,
    timestamp TEXT,
    note TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    price REAL,
    quantity INTEGER,
    amount REAL,
    cost REAL,
    profit REAL,
    capital REAL,
    timestamp TEXT,
    note TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    cond_key TEXT,
    is_event INTEGER DEFAULT 0,
    type TEXT NOT NULL,
    name TEXT,
    price REAL,
    action TEXT,
    category TEXT,
    expiry_date TEXT,
    status TEXT,
    auto_link_cost INTEGER DEFAULT 0,
    peak_price REAL,
    seq INTEGER,
    cond_uid TEXT,
    created_at TEXT,
    modified_at TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS condition_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id INTEGER NOT NULL,
    old_price REAL,
    new_price REAL,
    reason TEXT,
    timestamp TEXT,
    level TEXT,
    override_triggers TEXT,
    FOREIGN KEY(condition_id) REFERENCES conditions(id)
);

CREATE TABLE IF NOT EXISTS exright_applied (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    cqr TEXT,
    fhcontent TEXT,
    applied_at TEXT,
    reason TEXT,
    migrated INTEGER DEFAULT 0,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);
"""

# v3：弹性组合池层 5 张表（池 / 持仓段 / 总池账本 / 审计 / 池变更日志）
V3_DDL = """
CREATE TABLE IF NOT EXISTS pool (
    stock TEXT PRIMARY KEY,
    code TEXT,
    strategy TEXT NOT NULL,      -- L1 / L2 / L3
    pool_status TEXT NOT NULL,   -- active / removed
    refresh_cadence TEXT,        -- daily / weekly / event
    entered_at TEXT,
    exit_reason TEXT
);

CREATE TABLE IF NOT EXISTS position (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock TEXT NOT NULL,
    code TEXT,
    strategy TEXT,
    status TEXT NOT NULL,        -- open / closed
    budget REAL,
    topup_total REAL DEFAULT 0,
    opened_at TEXT,
    closed_at TEXT,
    close_value REAL,
    realized_pnl REAL,
    cooldown_until TEXT
);

CREATE TABLE IF NOT EXISTS pool_ledger (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total REAL NOT NULL,
    free REAL NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    action TEXT,             -- init / allocate / topup / release
    stock TEXT,
    amount REAL,
    free_before REAL,
    free_after REAL,
    reason TEXT,
    source TEXT              -- agent / manual
);

CREATE TABLE IF NOT EXISTS watchlog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    action TEXT,             -- add / remove / set_strategy / lock
    stock TEXT,
    strategy_from TEXT,
    strategy_to TEXT,
    reason TEXT,
    source TEXT
);
"""

def get_connection(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def migrate_db(conn: sqlite3.Connection):
    # 先确保 schema_meta 存在，避免全新库上读取版本时 "no such table" 崩溃
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL, migrated_at TEXT)")
    row = conn.execute("SELECT version FROM schema_meta").fetchone()
    current = row[0] if row else 0
    if current < 1:
        conn.executescript(SCHEMA_DDL)
        conn.execute("INSERT INTO schema_meta (version, migrated_at) VALUES (1, ?)",
                     (datetime.now().isoformat(),))
    if current < 2:
        # v2: conditions 表补 cond_uid / created_at / modified_at
        try:
            conn.execute("ALTER TABLE conditions ADD COLUMN cond_uid TEXT")
            conn.execute("ALTER TABLE conditions ADD COLUMN created_at TEXT")
            conn.execute("ALTER TABLE conditions ADD COLUMN modified_at TEXT")
        except sqlite3.OperationalError as e:
            if 'duplicate column' not in str(e).lower():
                raise
        conn.execute("UPDATE schema_meta SET version=2")
        conn.commit()
    if current < 3:
        # v3: 弹性组合池层（池 / 持仓段 / 总池账本 / 审计 / 池变更日志）
        conn.executescript(V3_DDL)
        conn.execute("UPDATE schema_meta SET version=3")
        conn.commit()
