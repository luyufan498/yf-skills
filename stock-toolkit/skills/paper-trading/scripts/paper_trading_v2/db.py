"""SQLite 连接 + schema DDL + 迁移版本"""
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 2

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
