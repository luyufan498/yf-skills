"""SQLite 连接 + schema DDL + 迁移版本"""
import re
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 8  # 与 migrate_db 实际最高版同步（v8: sleeve_ledger CHECK(free>=0)，2026-09-01）

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
    exit_reason TEXT,
    pin INTEGER DEFAULT 0        -- pin=1: 允许降级但禁止删除（名单保护，独立于档位）
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

# v4：重新 allocate（重入）前归档旧段操作，保留历史
V4_DDL = """
CREATE TABLE IF NOT EXISTS operations_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    archived_at TEXT,
    segment_id INTEGER,
    type TEXT NOT NULL,
    price REAL, quantity INTEGER, amount REAL, cost REAL, profit REAL, capital REAL,
    timestamp TEXT, note TEXT,
    seq INTEGER,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);
"""

# v7：L3 事件 sleeve 架构（2026-09-01 方案 3.1）
# - event_slots：事件槽（20 事件坑计数器=event_slots 活跃行 status∈(open,partial)）
# - event_slot_members：事件↔成员关联权威（G3 跨票归并反查靠它；members JSON 降级为开槽快照）
# - sleeve_ledger：消息池账本（pool_ledger CHECK(id=1) 挡第二行，故独立建表）
# - shadow_log：影子账（9 类契约 + gate_violation），永不回流生产字段
V7_DDL = """
CREATE TABLE IF NOT EXISTS event_slots (
    event_key TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'open',   -- open/partial/migrated/closed/archived
    opened_at TEXT,
    closed_at TEXT,
    budget REAL DEFAULT 0,
    realized REAL DEFAULT 0,
    news_kind TEXT,                        -- 六类词表 1.4 + other 兜底
    title TEXT,
    members_json TEXT,                     -- 开槽快照（关联权威=event_slot_members）
    invalidation TEXT,                     -- 论点失效旗标（灰度=影子，不执行卖出）
    topup_locked INTEGER DEFAULT 0,        -- 加仓锁（migrated 槽=1：不得对已迁移持仓加仓）
    orig_budget REAL,                      -- 迁移附加列：原槽预算
    migrated_at TEXT,
    migrated_stock TEXT,
    fill_status TEXT DEFAULT 'pending',    -- 待成交单：pending/filled/cancelled
    fill_at TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS event_slot_members (
    event_key TEXT NOT NULL,
    stock TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    joined_at TEXT,
    exited_at TEXT,
    migrated_at TEXT,
    PRIMARY KEY (event_key, stock),
    FOREIGN KEY(event_key) REFERENCES event_slots(event_key)
);

CREATE TABLE IF NOT EXISTS sleeve_ledger (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total REAL NOT NULL,
    free REAL NOT NULL CHECK (free >= 0),   -- v8: 资金底线（R2/A1，负余额=账本错账即崩）
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS shadow_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,        -- 9 类：drop_order/t5_counterfactual/news_kind/off10h/
                               -- invalidation/bridge_track/gap_open/tech_buy_all/event_key_missing
                               -- + gate_violation（闸门违例）
    key TEXT,
    payload TEXT,              -- JSON 契约（每 kind 一个模板，进 skill）
    payoff REAL,               -- 回填收益
    created_at TEXT,
    filled_at TEXT
);
"""

# 索引在加列之后建（idx_pool_event_key 依赖 pool.event_key 列，见 migrate_db v7 分支顺序）
V7_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_event_slots_status ON event_slots(status);
CREATE INDEX IF NOT EXISTS idx_esm_stock ON event_slot_members(stock);
CREATE INDEX IF NOT EXISTS idx_shadow_log_kind ON shadow_log(kind);
CREATE INDEX IF NOT EXISTS idx_pool_event_key ON pool(event_key);
"""

# accounts 重建（v7）：放开 UNIQUE(stock_name) → UNIQUE(stock_name, grp)
# 同票双组：sleeve 成员账户 grp='news'，主仓 grp='tech'；以 grp 列路由，非名字约定
# （v8/R3 起按安全重建模式使用：表名即最终名 accounts，重建流程见 _rebuild_accounts_v7）
ACCOUNTS_V7_DDL = """
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_name TEXT NOT NULL,
    stock_code TEXT,
    capital_total REAL NOT NULL,
    capital_available REAL NOT NULL,
    capital_used REAL NOT NULL,
    fifo_index INTEGER DEFAULT -1,
    fifo_offset REAL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    grp TEXT NOT NULL DEFAULT 'tech',
    UNIQUE (stock_name, grp)
);
"""

# v8：sleeve_ledger 加 CHECK(free>=0)（R2/A1 并发双花资金底线——负余额即账本错账，
# 在写入瞬间崩溃而不是静默污染后续对账）。重建走 R3 安全迁移模式（_rebuild_sleeve_ledger_v8）。
SLEEVE_LEDGER_V8_DDL = """
CREATE TABLE sleeve_ledger (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total REAL NOT NULL,
    free REAL NOT NULL CHECK (free >= 0),
    updated_at TEXT
);
"""

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


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
    if current < 4:
        # v4: 重新 allocate 前归档旧段操作（operations_archive）
        conn.executescript(V4_DDL)
        conn.execute("UPDATE schema_meta SET version=4")
        conn.commit()
    if current < 5:
        # v5: pool 表加 pin 字段（名单保护：允许降级、禁止删除，独立于档位）
        try:
            conn.execute("ALTER TABLE pool ADD COLUMN pin INTEGER DEFAULT 0")
        except sqlite3.OperationalError as e:
            if 'duplicate column' not in str(e).lower():
                raise
        conn.execute("UPDATE schema_meta SET version=5")
        conn.commit()
    if current < 6:
        # v6: reports 表（每日分析报告数据库缓存，文件仍为源）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock TEXT NOT NULL,
                report_date TEXT NOT NULL,
                title TEXT,
                summary TEXT,
                content TEXT,
                file_path TEXT,
                created_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_stock_date ON reports(stock, report_date)")
        conn.execute("UPDATE schema_meta SET version=6")
        conn.commit()
    if current < 7:
        # v7: L3 事件 sleeve 架构（方案 3.1）。⚠ 只做 schema（建表/加列/表重建），
        # 严禁任何行数据改写——strategy 'L3'→'L2' 值合并属 M2 手动事务，绝不进这里。
        conn.commit()
        # accounts 表重建需关外键（DROP 父表时子表引用会触发 FK 校验）；
        # PRAGMA foreign_keys 在事务内是 no-op，必须先落事务再改。
        fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        fk_before = {(r[0], r[1], r[2], r[3])
                     for r in conn.execute("PRAGMA foreign_key_check").fetchall()}
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.executescript(V7_DDL)
            for table, col, decl in (
                ('watchlog', 'event_key', 'TEXT'),
                ('watchlog', 'news_kind', 'TEXT'),
                ('pool', 'archived_at', 'TEXT'),
                ('pool', 'event_key', 'TEXT'),
            ):
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                if col not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            conn.executescript(V7_INDEX_DDL)
            _rebuild_accounts_v7(conn)
            # 验收门：accounts 重建不得新增外键违规（方案 3.7 迁移增量）。
            # 库里若有迁移前就存在的违规（如生产库 condition_history 孤儿行），
            # 迁移不替用户清数据（行数据纪律），只保证"零新增"并把存量原样报出来。
            fk_after = {(r[0], r[1], r[2], r[3])
                        for r in conn.execute("PRAGMA foreign_key_check").fetchall()}
            new_violations = fk_after - fk_before
            if new_violations:
                raise RuntimeError(
                    f"v7 迁移新增外键违规 {len(new_violations)} 条（迁移前既有 {len(fk_before)} 条不动）: "
                    f"{sorted(new_violations)[:10]}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if fk_was_on:
                conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("UPDATE schema_meta SET version=7")
        conn.commit()
    if current < 8:
        # v8: sleeve_ledger 加 CHECK(free>=0)（R2/A1 资金底线；R3 安全迁移重建模式首个应用案例）。
        # 重建与版本号同一事务提交：中途崩溃回滚到 v7 干净态，重跑走恢复分支。
        _rebuild_sleeve_ledger_v8(conn)
        conn.execute("UPDATE schema_meta SET version=8")
        conn.commit()


def _rebuild_sleeve_ledger_v8(conn: sqlite3.Connection):
    """v8：sleeve_ledger 加 CHECK(free>=0)——R3 安全迁移重建模式首个应用案例（F1）。

    模式：RENAME 旧表→建新表→灌数→DROP 旧表；任何时点崩溃，数据都在 *_old 或新表
    之一，永不双失。重跑入口检测到 <table>_old 残留（上次重建中断）→ 先走恢复分支：
    半成品新表可弃（权威数据在 *_old），还原后整段重做。禁止函数开头无条件 DROP。
    （sleeve_ledger 无子表引用，RENAME 不产生 FK 引用悬空，无需 legacy_alter_table。）
    """
    old = 'sleeve_ledger_old'
    if _table_exists(conn, old):
        # 恢复分支：上次重建中断——半成品新表可弃，权威数据在 *_old
        conn.execute("DROP TABLE IF EXISTS sleeve_ledger")
        conn.execute(f"ALTER TABLE {old} RENAME TO sleeve_ledger")
    if not _table_exists(conn, 'sleeve_ledger'):
        conn.execute(SLEEVE_LEDGER_V8_DDL)      # 全新库：直接按 v8 定义建
        return
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                       "AND name='sleeve_ledger'").fetchone()
    if row and re.search(r"free\s+REAL\s+NOT\s+NULL\s+CHECK", row[0] or '', re.I):
        return                                   # 已是 v8 定义（幂等）
    conn.execute("ALTER TABLE sleeve_ledger RENAME TO sleeve_ledger_old")
    conn.execute(SLEEVE_LEDGER_V8_DDL)
    conn.execute("INSERT INTO sleeve_ledger (id, total, free, updated_at) "
                 "SELECT id, total, free, updated_at FROM sleeve_ledger_old")
    conn.execute("DROP TABLE sleeve_ledger_old")


def _rebuild_accounts_v7(conn: sqlite3.Connection):
    """accounts 重建：UNIQUE(stock_name) → UNIQUE(stock_name, grp)，grp 缺省 'tech'。

    R3 安全迁移重建模式（F1）：RENAME 旧表→建新表→灌数→DROP 旧表，重跑入口检测
    accounts_old 残留先走恢复分支（禁函数开头无条件 DROP tmp）。
    调用方须已 PRAGMA foreign_keys=OFF + legacy_alter_table=ON（父表重建时子表
    REFERENCES 不随 RENAME 改写，避免 DROP 旧表后子表引用悬空）。
    老库 accounts 无 grp 列 → 全部按 'tech' 迁入；已有 grp 列（重复迁移）→ 原值保留。
    重建后 sqlite_sequence 随 AUTOINCREMENT 从 max(id) 续号，不会复用已删 id。
    """
    legacy_was = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        old = 'accounts_old'
        if _table_exists(conn, old):
            # 恢复分支：上次重建中断——半成品新表可弃，权威数据在 accounts_old
            conn.execute("DROP TABLE IF EXISTS accounts")
            conn.execute("DROP TABLE IF EXISTS accounts_v7")   # 旧实现残留的 tmp 表
            conn.execute(f"ALTER TABLE {old} RENAME TO accounts")
        if not _table_exists(conn, 'accounts'):
            conn.execute(ACCOUNTS_V7_DDL)                       # 全新库
            return
        cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
        if 'grp' in cols:
            idx = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                               "AND name='accounts'").fetchone()
            if idx and 'stock_name, grp' in idx[0].replace('`', '').replace('\n', ' '):
                return                 # 唯一约束已放开（幂等跳过）
            grp_expr = "grp"
        else:
            grp_expr = "'tech'"
        conn.execute("ALTER TABLE accounts RENAME TO accounts_old")
        conn.execute(ACCOUNTS_V7_DDL)
        conn.execute(
            f"INSERT INTO accounts (id, stock_name, stock_code, capital_total, "
            f"capital_available, capital_used, fifo_index, fifo_offset, created_at, "
            f"updated_at, grp) SELECT id, stock_name, stock_code, capital_total, "
            f"capital_available, capital_used, fifo_index, fifo_offset, created_at, "
            f"updated_at, {grp_expr} FROM accounts_old")
        conn.execute("DROP TABLE accounts_old")
    finally:
        conn.execute(f"PRAGMA legacy_alter_table = {1 if legacy_was else 0}")
