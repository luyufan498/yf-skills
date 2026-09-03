"""SQLite 连接 + schema DDL + 迁移版本"""
import os
import re
import sqlite3
from collections import deque
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 11  # 与 migrate_db 实际最高版同步（v11: v12 消息挂单列，2026-09-03）

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
    band_min REAL,                         -- v12 挂单带下沿（0.95×anchor；NULL=未挂单）
    band_max REAL,                         -- v12 挂单带上沿（1.05×anchor）
    anchor_price REAL,                     -- v12 锚=事件入库时刻价快照（watch_scan payload）
    order_ttl TEXT,                        -- v12 挂单到期（ISO；=挂单时刻后第一个交易节收盘）
    order_id TEXT,                         -- v12 挂单标识（order:<event_key>:<epoch>）
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

# ======================================================================
# v9：账户层退役（M1.6 深度重构，方案 3.7 + 2026-09-01 用户拍板）
# 「户=段的缓存」——accounts 表退役，position 段表吸收现金/FIFO 状态，段即账户。
# ----------------------------------------------------------------------

# U1：流水表更名 positions → trades（纯 FIFO 现金流语义不变）。
# account_id 列名保留、语义钉死为 position 段 id（段即账户，任务书 U2"列改名可不做"）。
TRADES_V9_DDL = """
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,   -- v9 语义=position 段 id（原 accounts.id，段即账户）
    seq INTEGER NOT NULL,
    operation TEXT NOT NULL,       -- buy / sell / exright_bonus / exright_dividend
    stock_code TEXT,
    quantity INTEGER,
    price REAL,
    total_cost REAL,
    timestamp TEXT,
    note TEXT,
    FOREIGN KEY(account_id) REFERENCES position(id)
);
"""

# 旧代码兼容垫片（U1）：单表视图 + INSTEAD OF 三触发器 = 完整可写
# （实测 SQLite 对无 INSTEAD OF 触发器的视图一律只读："cannot modify positions
#   because it is a view"——任务书"单表视图可写"预判不成立，垫片按触发器实现）。
# 旧代码零改动立即正常（SELECT 直读；INSERT/UPDATE/DELETE 经触发器落 trades）；
# 视图与触发器删除留给 v10。新代码一律写 trades。
TRADES_VIEW_V9_DDL = "CREATE VIEW positions AS SELECT * FROM trades"

TRADES_VIEW_TRIGGERS_V9 = """
CREATE TRIGGER trg_positions_ins_v9 INSTEAD OF INSERT ON positions BEGIN
    INSERT INTO trades (account_id, seq, operation, stock_code, quantity, price,
                        total_cost, timestamp, note)
    VALUES (NEW.account_id, NEW.seq, NEW.operation, NEW.stock_code, NEW.quantity,
            NEW.price, NEW.total_cost, NEW.timestamp, NEW.note);
END;
CREATE TRIGGER trg_positions_upd_v9 INSTEAD OF UPDATE ON positions BEGIN
    UPDATE trades SET account_id=NEW.account_id, seq=NEW.seq, operation=NEW.operation,
        stock_code=NEW.stock_code, quantity=NEW.quantity, price=NEW.price,
        total_cost=NEW.total_cost, timestamp=NEW.timestamp, note=NEW.note
    WHERE id=OLD.id;
END;
CREATE TRIGGER trg_positions_del_v9 INSTEAD OF DELETE ON positions BEGIN
    DELETE FROM trades WHERE id=OLD.id;
END;
"""

OPERATIONS_V9_DDL = """
CREATE TABLE operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,   -- v9 语义=position 段 id
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
    FOREIGN KEY(account_id) REFERENCES position(id)
);
"""

CONDITIONS_V9_DDL = """
CREATE TABLE conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,   -- v9 语义=position 段 id
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
    FOREIGN KEY(account_id) REFERENCES position(id)
);
"""

EXRIGHT_V9_DDL = """
CREATE TABLE exright_applied (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,   -- v9 语义=position 段 id
    seq INTEGER NOT NULL,
    cqr TEXT,
    fhcontent TEXT,
    applied_at TEXT,
    reason TEXT,
    migrated INTEGER DEFAULT 0,
    FOREIGN KEY(account_id) REFERENCES position(id)
);
"""

OPERATIONS_ARCHIVE_V9_DDL = """
CREATE TABLE operations_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,   -- v9 语义=position 段 id（归档行按 segment_id 对位）
    archived_at TEXT,
    segment_id INTEGER,
    type TEXT NOT NULL,
    price REAL, quantity INTEGER, amount REAL, cost REAL, profit REAL, capital REAL,
    timestamp TEXT, note TEXT,
    seq INTEGER,
    FOREIGN KEY(account_id) REFERENCES position(id)
);
"""

# M1.7/D12 遗留到 v9 窗口：pool_ledger 加 CHECK(free>=0)（与 sleeve_ledger v8 同款资金底线）
POOL_LEDGER_V9_DDL = """
CREATE TABLE pool_ledger (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total REAL NOT NULL,
    free REAL NOT NULL CHECK (free >= 0),
    updated_at TEXT
);
"""

# U2：组由 strategy 推导（strategy='NEWS' ⟺ news，其余 ⟺ tech）。
def grp_of_strategy(strategy) -> str:
    return 'news' if (strategy or '') == 'NEWS' else 'tech'


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def get_connection(db_path) -> sqlite3.Connection:
    # timeout=5s（M1.7/D4 遗留 v9 窗口项）：并发写锁等待后干净拒绝，
    # 不再"database is locked"瞬时崩（单写者纪律下只是兜底，不是并发模型）
    conn = sqlite3.connect(str(db_path), timeout=5.0)
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
    if current < 9:
        # v9: 账户层退役（M1.6 深度重构）。D 是唯一允许改行数据的迁移轮：
        #   U1 positions→trades + 可写兼容视图；U2 段吸收 cash/FIFO + accounts 退役（accounts_old 保留）；
        #   join 键 account_id 语义改挂 position 段 id；U7.4 FK 孤儿清理；U7.1/7.2 条件表卫生；
        #   M17-D12 pool_ledger CHECK(free>=0)。
        # 红线：资金恒等式前后逐分相等（迁移零挪钱）；FK 违规零新增（v7 同款验收门）。
        conn.commit()
        fk_was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        fk_before = {(r[0], r[1], r[2], r[3])
                     for r in conn.execute("PRAGMA foreign_key_check").fetchall()}
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            _migrate_v9(conn)
            fk_after = {(r[0], r[1], r[2], r[3])
                        for r in conn.execute("PRAGMA foreign_key_check").fetchall()}
            new_violations = fk_after - fk_before
            if new_violations:
                raise RuntimeError(
                    f"v9 迁移新增外键违规 {len(new_violations)} 条（迁移前既有 {len(fk_before)} 条不动）: "
                    f"{sorted(new_violations)[:10]}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if fk_was_on:
                conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("UPDATE schema_meta SET version=9")
        conn.commit()
    if current < 10:
        # v10: v11 池模型（预算/现金分离）段列——entry_mode: normal|rotation
        # （入场门矩阵：rotation=轮换换入，floor 豁免+义务帽校验）；source: 建段来源
        # （'manual'=L1 人工特权全豁免；NULL=旧段=v11 前存量，按 agent 口径）。
        # 只加列零行改写（资金零挪动——cash→free 的一次性搬运归 pool_publicize.py，
        # 不进 migrate_db：schema 与账务搬运分离，搬运须人工 --execute 窗口）。
        cols = [r[1] for r in conn.execute("PRAGMA table_info(position)").fetchall()]
        if cols:  # 合成 schema 容错：无 position 表（最小库）→ 无处加列，只推版本号
            for col, decl in (('entry_mode', "TEXT DEFAULT 'normal'"),
                              ('source', 'TEXT')):
                if col not in cols:
                    try:
                        conn.execute(f"ALTER TABLE position ADD COLUMN {col} {decl}")
                    except sqlite3.OperationalError as e:
                        if 'duplicate column' not in str(e).lower():
                            raise
        conn.execute("UPDATE schema_meta SET version=10")
        conn.commit()
    if current < 11:
        # v11: v12 消息挂单链路（plans/v12-news-order-20260903）event_slots 挂单列——
        # band_min/band_max/anchor_price（成交带 = [0.95,1.05]×事件入库时刻价锚）、
        # order_ttl（挂单到期=下一节收盘）、order_id（挂单标识）。
        # 只加列零行改写；存量行默认 NULL=未挂单（兼容旧 pending/filled 槽）。
        # SCHEMA_DDL 同步新库建表；此处 ALTER 覆盖存量库（duplicate column 幂等）。
        cols = [r[1] for r in conn.execute("PRAGMA table_info(event_slots)").fetchall()]
        if cols:  # 合成 schema 容错：无 event_slots 表（最小库）→ 无处加列，只推版本号
            for col, decl in (('band_min', 'REAL'), ('band_max', 'REAL'),
                              ('anchor_price', 'REAL'), ('order_ttl', 'TEXT'),
                              ('order_id', 'TEXT')):
                if col not in cols:
                    try:
                        conn.execute(f"ALTER TABLE event_slots ADD COLUMN {col} {decl}")
                    except sqlite3.OperationalError as e:
                        if 'duplicate column' not in str(e).lower():
                            raise
        conn.execute("UPDATE schema_meta SET version=11")
        conn.commit()


# ----------------------------------------------------------------------
# v9 迁移实现（账户层退役）
# ----------------------------------------------------------------------

# 子表重建清单：RENAME→建新(FK→position)→灌数(段 id 重映射+id 原值保留)→DROP old
# （R3 安全迁移模式）。id 原值保留：condition_history.condition_id 等跨表引用不断链。
_V9_CHILD_TABLES = (
    ('positions', 'trades', TRADES_V9_DDL,
     'id, account_id, seq, operation, stock_code, quantity, price, total_cost, timestamp, note',
     'j.id, j.account_id, j.seq, j.operation, j.stock_code, j.quantity, j.price, j.total_cost, '
     'j.timestamp, j.note'),
    ('operations', 'operations', OPERATIONS_V9_DDL,
     'id, account_id, seq, type, price, quantity, amount, cost, profit, capital, timestamp, note',
     'j.id, m.position_id, j.seq, j.type, j.price, j.quantity, j.amount, j.cost, j.profit, '
     'j.capital, j.timestamp, j.note'),
    ('conditions', 'conditions', CONDITIONS_V9_DDL,
     'id, account_id, cond_key, is_event, type, name, price, action, category, expiry_date, '
     'status, auto_link_cost, peak_price, seq, cond_uid, created_at, modified_at',
     'j.id, m.position_id, j.cond_key, j.is_event, j.type, j.name, j.price, j.action, j.category, '
     'j.expiry_date, j.status, j.auto_link_cost, j.peak_price, j.seq, j.cond_uid, '
     'j.created_at, j.modified_at'),
    ('exright_applied', 'exright_applied', EXRIGHT_V9_DDL,
     'id, account_id, seq, cqr, fhcontent, applied_at, reason, migrated',
     'j.id, m.position_id, j.seq, j.cqr, j.fhcontent, j.applied_at, j.reason, j.migrated'),
    ('operations_archive', 'operations_archive', OPERATIONS_ARCHIVE_V9_DDL,
     'id, account_id, archived_at, segment_id, type, price, quantity, amount, cost, profit, '
     'capital, timestamp, note, seq',
     "j.id, COALESCE(j.segment_id, m.position_id), j.archived_at, j.segment_id, j.type, j.price, "
     "j.quantity, j.amount, j.cost, j.profit, j.capital, j.timestamp, j.note, j.seq"),
)


def _fifo_residual_rows(rows) -> tuple:
    """FIFO 残余 (qty, cost)——与 sleeve_slots.account_remaining 同算法（审计/迁移用，纯本地）。"""
    q = deque()
    for r in rows:
        op = r['operation'] if not isinstance(r, dict) else r.get('operation')
        qty = (r['quantity'] or 0) if not isinstance(r, dict) else (r.get('quantity') or 0)
        cost = (r['total_cost'] or 0) if not isinstance(r, dict) else (r.get('total_cost') or 0)
        if op == 'buy':
            q.append([float(qty), cost / qty if qty else 0.0])
        elif op == 'sell':
            while qty > 0 and q:
                if q[0][0] <= qty:
                    qty -= q[0][0]
                    q.popleft()
                else:
                    q[0][0] -= qty
                    qty = 0
        elif op == 'exright_bonus':
            if q:
                total = sum(i[0] for i in q)
                if total:
                    ratio = 1 + (qty / total)
                    for i in q:
                        i[0] *= ratio
                        i[1] /= ratio
        elif op == 'exright_dividend':
            if q:
                total = sum(i[0] for i in q)
                if total:
                    dps = abs(cost) / total
                    for i in q:
                        i[1] -= dps
    qty = int(sum(i[0] for i in q))
    cost = sum(i[0] * i[1] for i in q)
    if qty == 0:
        cost = 0.0
    return qty, cost


def _v9_recovery(conn):
    """v9 恢复分支（R3 模式）：上次迁移中断——半成品新表可弃，权威数据在旧名/*_v9_old 里。

    状态判定与还原（只认"权威在旧名"原则，禁止无条件 DROP 权威表）：
      - 兼容视图 positions → 直接删（重建时按新定义建）
      - 子表 staged（{new}_v9_old）在 → 半成品新表弃、staged 还原为旧名
      - 新名表在而旧名不在（positions→trades 已改名未删 staged）→ 还原名
      - 新名表在而旧名也在 → 旧名为权威，弃新表
      - pool_ledger_v9_old / accounts_old 在 → 同款还原（accounts_old 是权威）
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND name='positions'"
                    ).fetchone():
        conn.execute("DROP VIEW positions")
    for old, new, *_rest in _V9_CHILD_TABLES:
        staged = f'{new}_v9_old'
        if _table_exists(conn, staged):
            conn.execute(f"DROP TABLE IF EXISTS {new}")
            conn.execute(f"ALTER TABLE {staged} RENAME TO {old}")
        elif new != old and _table_exists(conn, new):
            if _table_exists(conn, old):
                conn.execute(f"DROP TABLE {new}")
            else:
                conn.execute(f"ALTER TABLE {new} RENAME TO {old}")
    if _table_exists(conn, 'pool_ledger_v9_old'):
        if _table_exists(conn, 'pool_ledger'):
            conn.execute("DROP TABLE pool_ledger")
        conn.execute("ALTER TABLE pool_ledger_v9_old RENAME TO pool_ledger")
    if _table_exists(conn, 'accounts_old'):
        if _table_exists(conn, 'accounts'):
            conn.execute("DROP TABLE accounts")
        conn.execute("ALTER TABLE accounts_old RENAME TO accounts")
    conn.execute("DROP TABLE IF EXISTS _v9_map")


def _v9_account_segment_map(conn) -> dict:
    """账户→段映射（U2）：每活户并入配对段；死壳归该票最后一个归档段；无段则建 stub。

    规则（确定性）：
      1. 有匹配组的 open 段（grp='news'→strategy='NEWS'；tech→非 NEWS）→ 取 id 最大者
      2. 否则该票最后一个 closed 段（closed_at/id 最大者）
      3. 否则建 stub closed 段（budget=0, note 标记 v9 迁移归档）
    返回 {old_account_id: position_id}；顺带把 cash/fifo 写进配对段（活户=段现金真身）。
    （accounts/position 缺表（合成 schema 测试）→ 空映射，段表照建。）
    """
    mapping = {}
    if not _table_exists(conn, 'accounts'):
        return mapping
    accts = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    for a in accts:
        stock = a['stock_name']
        grp = a['grp'] if 'grp' in a.keys() else 'tech'
        segs = conn.execute("SELECT * FROM position WHERE stock=? ORDER BY id", (stock,)).fetchall()
        want = 'NEWS' if grp == 'news' else None
        open_seg = None
        for s in segs:
            if s['status'] != 'open':
                continue
            if want == 'NEWS' and (s['strategy'] or '') == 'NEWS':
                open_seg = s
            elif want is None and (s['strategy'] or '') != 'NEWS':
                open_seg = s
        target = open_seg
        if target is None:
            closed = [s for s in segs if s['status'] == 'closed']
            target = closed[-1] if closed else None
        if target is None:
            # 无任何段的账户：建 stub closed 段承载历史（生产无此形态，测试/兜底用）
            cur = conn.execute(
                "INSERT INTO position (stock, code, strategy, status, budget, topup_total, "
                "opened_at, closed_at, cash, fifo_index, fifo_offset, note) "
                "VALUES (?,?,'L1','closed',0,0,?,?,?,COALESCE(?,0),COALESCE(?,0),?)",
                (stock, a['stock_code'], a['created_at'], a['updated_at'],
                 a['capital_available'], a['fifo_index'], a['fifo_offset'],
                 '[v9迁移] 无段账户历史归档（原 accounts 行）'))
            target_id = cur.lastrowid
        else:
            target_id = target['id']
            if target['status'] == 'open':
                # 活户并入配对段：段现金/FIFO 状态 = 原账户值（段即账户，U2 主迁移动作）
                conn.execute(
                    "UPDATE position SET cash=?, fifo_index=COALESCE(?, fifo_index), "
                    "fifo_offset=COALESCE(?, fifo_offset), code=COALESCE(code, ?) WHERE id=?",
                    (a['capital_available'], a['fifo_index'], a['fifo_offset'],
                     a['stock_code'], target_id))
            else:
                # 死壳：现金已在 release 结算回池/出金，原 capital_available 是
                # get_account 重建公式算出的"幽灵值"（如沃森生物 -9,270.80=费用损失）。
                # 按原值迁入 closed 段 cash（逐分保真、恒等式前后不变），不回池不再分配。
                conn.execute(
                    "UPDATE position SET cash=?, fifo_index=COALESCE(?, fifo_index), "
                    "fifo_offset=COALESCE(?, fifo_offset) WHERE id=?",
                    (a['capital_available'], a['fifo_index'], a['fifo_offset'], target_id))
        mapping[a['id']] = target_id
    return mapping


def _v9_audit_before(conn, mapping) -> list:
    """U2 前置不变量审计（只读，逐票）：不过即停（raise），env M16_ALLOW_DIRTY_SHELLS=1 显式放行。

    1) 段清仓时刻 FIFO 残余=0（死壳 → 最后归档段，任务书 U2）
    2) 活户 FIFO 残余 == 原账户 capital_used（±0.01，段吸收 FIFO 后可复核）
    返回 [违规描述]。
    """
    problems = []
    for old_aid, seg_id in mapping.items():
        a = conn.execute("SELECT * FROM accounts WHERE id=?", (old_aid,)).fetchone()
        seg = conn.execute("SELECT * FROM position WHERE id=?", (seg_id,)).fetchone()
        rows = conn.execute("SELECT operation, quantity, total_cost, timestamp FROM positions "
                            "WHERE account_id=? ORDER BY seq", (old_aid,)).fetchall()
        qty_all, cost_all = _fifo_residual_rows(rows)
        if seg['status'] == 'open':
            used = a['capital_used'] or 0.0
            if abs(cost_all - used) > 0.01:
                # 成本为准（除权不改股数语义；capital_used 即段占用成本）
                problems.append(
                    f"活户 FIFO 不对账：acct#{old_aid}({a['stock_name']}) FIFO 残余成本 "
                    f"¥{cost_all:,.2f} != accounts.capital_used ¥{used:,.2f}")
        else:
            closed_at = seg['closed_at']
            rows_at = [r for r in rows if (r['timestamp'] or '') <= (closed_at or '')] \
                if closed_at else list(rows)
            qty_at, cost_at = _fifo_residual_rows(rows_at)
            if qty_at != 0:
                problems.append(
                    f"死壳不变量 FAIL：acct#{old_aid}({a['stock_name']}) 段#{seg_id} 清仓时刻"
                    f"（{closed_at}）FIFO 残余 {qty_at} 股/¥{cost_at:,.2f}（全史 {qty_all} 股/"
                    f"¥{cost_all:,.2f}）——按任务书 U2 不过即停")
    return problems


def _migrate_v9(conn: sqlite3.Connection):
    """v9 迁移主体：账户层退役（M1.6/U1+U2+U7.1+U7.2+U7.4 + M17-D12）。

    顺序（单事务，R3 安全迁移模式）：
      0. 恢复分支（accounts_old / *_v9_old 残留）
      1. 段表加列 cash / fifo_index / fifo_offset（U2）
      2. 账户→段映射 + cash/FIFO 迁入（U2）；前置不变量审计不过即停（env 显式放行）
      3. 子表重建（FK→position，account_id 语义=段 id）：positions→trades + 兼容视图、
         operations、conditions、exright_applied、operations_archive（U1/U2）
      4. accounts → accounts_old（保留，禁 DROP，U2）
      5. pool_ledger CHECK(free>=0)（M17-D12 v9 窗口项）
      6. U7.4 condition_history FK 孤儿清理；U7.1 活跃同型线唯一化；U7.2 清仓僵尸归档
      7. audit 留痕（行数/映射数/容差基准）
    """
    _v9_recovery(conn)

    # legacy_alter_table=ON（v7 账户重建同款）：RENAME 不改写子表 FK 文本，
    # 否则 condition_history REFERENCES 会跟随 RENAME 指向 *_v9_old（重建后悬空）。
    legacy_was = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        return _migrate_v9_body(conn)
    finally:
        conn.execute(f"PRAGMA legacy_alter_table = {1 if legacy_was else 0}")


def _migrate_v9_body(conn: sqlite3.Connection):
    """v9 迁移主体步骤（由 _migrate_v9 在 legacy_alter_table=ON 下调用）。"""

    # 1. 段表加列（U2：段吸收现金与 FIFO 状态）；缺 position 表（合成 schema）→ 按 v9 定义建
    if not _table_exists(conn, 'position'):
        conn.execute("""
            CREATE TABLE position (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock TEXT NOT NULL,
                code TEXT,
                strategy TEXT,
                status TEXT NOT NULL,
                budget REAL,
                topup_total REAL DEFAULT 0,
                opened_at TEXT,
                closed_at TEXT,
                close_value REAL,
                realized_pnl REAL,
                cooldown_until TEXT,
                cash REAL,
                fifo_index INTEGER DEFAULT -1,
                fifo_offset REAL DEFAULT 0
            )""")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(position)").fetchall()]
    for col, decl in (('cash', 'REAL'),
                      ('fifo_index', 'INTEGER DEFAULT -1'),
                      ('fifo_offset', 'REAL DEFAULT 0')):
        if col not in cols:
            conn.execute(f"ALTER TABLE position ADD COLUMN {col} {decl}")

    # 2. 账户→段映射 + 现金/FIFO 迁入 + 前置审计（不过即停）
    mapping = _v9_account_segment_map(conn)
    problems = _v9_audit_before(conn, mapping)
    if problems:
        if os.environ.get('M16_ALLOW_DIRTY_SHELLS') != '1':
            raise RuntimeError(
                "v9 迁移前置审计未过（任务书 U2：不过则停下报告）——未做任何改动：\n  "
                + "\n  ".join(problems)
                + "\n（确认知情并显式放行：export M16_ALLOW_DIRTY_SHELLS=1 后重跑）")
        for p in problems:
            print(f"[v9-migrate][审计放行] {p}")

    # 3. 子表重建：建映射表 → RENAME 旧表 → 建新表（FK→position）→ 灌数（段 id 重映射）
    #    → DROP old →（positions 另建可写兼容视图）
    conn.execute("DROP TABLE IF EXISTS _v9_map")
    conn.execute("CREATE TABLE _v9_map (old_id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL)")
    conn.executemany("INSERT INTO _v9_map (old_id, position_id) VALUES (?,?)",
                     list(mapping.items()))
    for old, new, ddl, cols_sql, select_sql in _V9_CHILD_TABLES:
        if not _table_exists(conn, old):
            # 合成/极简 schema（无该表）：直接按 v9 定义建空表（无需重建）
            if not _table_exists(conn, new):
                conn.execute(ddl)
            continue
        staged = f'{new}_v9_old'
        conn.execute(f"ALTER TABLE {old} RENAME TO {staged}")
        conn.execute(ddl)
        if old == 'positions':
            # trades：account_id 直接经 _v9_map 重映射（id 原值保留）
            conn.execute(
                "INSERT INTO trades (id, account_id, seq, operation, stock_code, quantity, "
                "price, total_cost, timestamp, note) "
                "SELECT j.id, m.position_id, j.seq, j.operation, j.stock_code, j.quantity, "
                "j.price, j.total_cost, j.timestamp, j.note FROM trades_v9_old j "
                "JOIN _v9_map m ON m.old_id=j.account_id")
        elif old == 'operations_archive':
            conn.execute(
                "INSERT INTO operations_archive (id, account_id, archived_at, segment_id, type, "
                "price, quantity, amount, cost, profit, capital, timestamp, note, seq) "
                "SELECT j.id, COALESCE(j.segment_id, m.position_id), j.archived_at, "
                "j.segment_id, j.type, j.price, j.quantity, j.amount, j.cost, j.profit, "
                "j.capital, j.timestamp, j.note, j.seq FROM operations_archive_v9_old j "
                "LEFT JOIN _v9_map m ON m.old_id=j.account_id")
        else:
            conn.execute(
                f"INSERT INTO {new} ({cols_sql}) SELECT {select_sql} FROM {staged} j "
                f"JOIN _v9_map m ON m.old_id=j.account_id")
        conn.execute(f"DROP TABLE {staged}")
    conn.execute(TRADES_VIEW_V9_DDL)          # U1 兼容视图（旧代码零改动）
    conn.executescript(TRADES_VIEW_TRIGGERS_V9)
    conn.execute("DROP TABLE _v9_map")

    # 4. accounts 退役：RENAME accounts_old 保留（安全迁移模式，禁 DROP）
    if _table_exists(conn, 'accounts'):
        conn.execute("ALTER TABLE accounts RENAME TO accounts_old")

    # 5. pool_ledger CHECK(free>=0)（M17-D12）
    _rebuild_pool_ledger_v9(conn)

    # 6a. U7.4：condition_history FK 孤儿清理（存量 3 条 1229-1231）
    if _table_exists(conn, 'condition_history') and _table_exists(conn, 'conditions'):
        orphans = [r[0] for r in conn.execute(
            "SELECT id FROM condition_history WHERE condition_id NOT IN "
            "(SELECT id FROM conditions)")]
        if orphans:
            conn.execute("DELETE FROM condition_history WHERE id IN (%s)"
                         % ','.join('?' * len(orphans)), orphans)

    # 6b. U7.2：清仓僵尸归档——closed 段（清仓=资金已结算）的 active/suspended 条件线全转 archived
    if not _table_exists(conn, 'conditions'):
        cur = conn.execute("SELECT 0 WHERE 0")     # 无条件表（合成 schema）：零行占位
    else:
        cur = conn.execute(
        "UPDATE conditions SET status='archived', modified_at=? WHERE status IN "
        "('active','suspended') AND account_id IN (SELECT id FROM position WHERE status='closed')",
        (datetime.now().isoformat(),))
    n_zombie = cur.rowcount

    # 6c. U7.1：活跃同型线唯一化（同段同型 (account_id,type) 多条 active → 保留价高者
    #     （trailing 高线=远离现价，低垂线先触发=提前误卖实证），其余归档
    n_dedupe = 0
    _cond_rows = (conn.execute("SELECT account_id, type, COUNT(*) c FROM conditions "
                               "WHERE status='active' GROUP BY account_id, type "
                               "HAVING COUNT(*)>1").fetchall()
                  if _table_exists(conn, 'conditions') else [])
    for r in _cond_rows:
        rows = conn.execute("SELECT id, price FROM conditions WHERE account_id=? AND type=? "
                            "AND status='active' ORDER BY price DESC, id DESC",
                            (r['account_id'], r['type'])).fetchall()
        keep = rows[0]['id']
        drop = [x['id'] for x in rows[1:]]
        conn.execute("UPDATE conditions SET status='archived', modified_at=? WHERE id IN (%s)"
                     % ','.join('?' * len(drop)),
                     (datetime.now().isoformat(), *drop))
        n_dedupe += len(drop)

    # 6d. U7.3：peak_price 回填——active trailing 而 peak=NULL 的线，用段开仓以来 K 线补
    #     （拿不到 K 线/离线 → 回退段 trades 成交价最高值；仍无 → 留 NULL 交 atr-sync 首扫 seed）
    n_peak = _v9_backfill_peak(conn)

    # 6e. 段已实现盈亏回填（段现金恒等式的前提）：open 段 realized_pnl = Σ(未标记 sell 流水
    #     amount−cost)。v8 时代该列只在 release 时落（value−budget），open 段恒 0，
    #     段内部分卖出的盈亏只藏在 cash 里——恒等式 cash+FIFO−realized==budget 因此破缺
    #     （生产副本实测爱司凯/中芯/凯莱英 3 段）。标记行（段转随迁）不计入。
    n_rpnl = 0
    for seg in conn.execute("SELECT id FROM position WHERE status='open'").fetchall():
        rp = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(amount,0)-COALESCE(cost,0)),0) FROM operations "
            "WHERE account_id=? AND type='sell' AND (note IS NULL OR note NOT LIKE '%段转%')",
            (seg['id'],)).fetchone()[0] or 0.0
        conn.execute("UPDATE position SET realized_pnl=? WHERE id=?", (rp, seg['id']))
        n_rpnl += 1

    # 7. 留痕（audit 表缺失的合成 schema 跳过）
    n_seg = conn.execute("SELECT COUNT(*) FROM position").fetchone()[0]
    n_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if not _table_exists(conn, 'audit'):
        return {"accounts": len(mapping), "zombies": n_zombie, "dedupe": n_dedupe}
    conn.execute(
        "INSERT INTO audit (timestamp, action, stock, amount, free_before, free_after, reason, "
        "source) VALUES (?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(), 'migrate_v9', None, 0, None, None,
         f"账户层退役：{len(mapping)} 户并段 / 段表 {n_seg} 行(+cash/fifo) / trades {n_trades} 行"
         f" / 僵尸归档 {n_zombie} 条 / 同型线去重 {n_dedupe} 条 / peak 回填 {n_peak} 条"
         f" / open段realized回填 {n_rpnl} 段 / accounts→accounts_old 保留",
         'manual'))
    return {"accounts": len(mapping), "zombies": n_zombie, "dedupe": n_dedupe}


def _v9_backfill_peak(conn) -> int:
    """U7.3：active trailing_stop 而 peak_price IS NULL 的线回填 peak。

    优先段开仓以来日 K 最高价（KLineDataFetcher，网络不可用即跳过）；
    回退该段 trades 成交价最高值；两者皆无 → 保持 NULL（atr-sync 首扫按当前价 seed，
    conditions_manager.sync_trailing_stop init_peak='current' 语义兜底）。
    返回回填条数（方法记入 audit reason）。
    """
    if not _table_exists(conn, 'conditions'):
        return 0
    rows = conn.execute(
        "SELECT c.id, c.price, c.account_id, p.stock, p.code, p.opened_at "
        "FROM conditions c LEFT JOIN position p ON p.id=c.account_id "
        "WHERE c.type='trailing_stop' AND c.status='active' AND c.peak_price IS NULL"
    ).fetchall()
    if not rows:
        return 0
    filled = 0
    for r in rows:
        peak = None
        method = None
        code = r['code']
        if code:
            try:
                from paper_trading_v2.kline_fetcher import KLineDataFetcher
                klines = KLineDataFetcher().fetch_kline_data(code, 'day', 120) or []
                since = (r['opened_at'] or '')[:10]
                highs = [float(k.get('high') or 0) for k in klines
                         if (k.get('date') or '') >= since and k.get('high')]
                if highs:
                    peak = max(highs)
                    method = 'kline'
            except Exception:
                peak = None
        if peak is None:
            row = conn.execute(
                "SELECT MAX(price) FROM trades WHERE account_id=? AND operation='buy'",
                (r['account_id'],)).fetchone()
            if row and row[0]:
                peak = float(row[0])
                method = 'trades'
        if peak is not None and peak > 0:
            conn.execute("UPDATE conditions SET peak_price=? WHERE id=?",
                         (round(peak, 2), r['id']))
            filled += 1
    return filled


def _rebuild_pool_ledger_v9(conn: sqlite3.Connection):
    """pool_ledger 加 CHECK(free>=0)（M17-D12 遗留 v9 窗口项）——R3 安全迁移重建模式。

    pool_ledger 无子表引用（audit 表不设 FK 指向它），RENAME 不产生引用悬空。
    """
    old = 'pool_ledger_v9_old'
    if _table_exists(conn, old):
        conn.execute("DROP TABLE IF EXISTS pool_ledger")
        conn.execute(f"ALTER TABLE {old} RENAME TO pool_ledger")
    if not _table_exists(conn, 'pool_ledger'):
        conn.execute(POOL_LEDGER_V9_DDL)
        return
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                       "AND name='pool_ledger'").fetchone()
    if row and re.search(r"free\s+REAL\s+NOT\s+NULL\s+CHECK", row[0] or '', re.I):
        return                                    # 已是 v9 定义（幂等）
    conn.execute("ALTER TABLE pool_ledger RENAME TO pool_ledger_v9_old")
    conn.execute(POOL_LEDGER_V9_DDL)
    conn.execute("INSERT INTO pool_ledger (id, total, free, updated_at) "
                 "SELECT id, total, free, updated_at FROM pool_ledger_v9_old")
    conn.execute("DROP TABLE pool_ledger_v9_old")


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
