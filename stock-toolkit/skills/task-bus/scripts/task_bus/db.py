"""task-bus 数据库层：task_events 表 + 原子认领。"""
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

ENV = "STOCK_TASKS_DB"
DEFAULT_DB = os.path.join(os.getcwd(), "data", "tasks", "tasks.db")

# 事件类型（任务域 intents）：与信息域（newsdb events 事实）分离
# 退役说明：CANDIDATE（2026-09-04）——新入池触发分析由 analysis_schedule TTL 队列取代
# （seed_pool 幂等 sync，入池股 last_analyzed_at=NULL 最优先），不再需要单独事件；
# ROTATION_EXIT（2026-09-04）——存量 0 从未流通，轮换卖单走 watchpoint sell 由 C1 覆盖。
# REFRESH（2026-09-04）——存量 0 从未流通；历史语义漂移（补搜→C2）已被 COLLECT 取代，
# 外部强制分析刷新走 ANALYSIS_REFRESH（唯一消费者 analysis-watch 硬门）。
TYPES = ["DEEP_DIVE", "WATCH_ALERT", "CALENDAR", "L3_SNAPSHOT", "MSG_SNAPSHOT", "SLEEVE_FILL",
         # v12 消息挂单链路（方案 v12-news-order-20260903）：msg-watch 专属三类型
         "MSG_CANDIDATE", "MSG_ORDER", "MSG_REJUDGE",
         # 消息论点失效清退令（2026-09-09）：生产者=晚审（supervisor-evening-audit，
         # 19:35 盘后按收盘数据判定 A∧B∧n≥5∧C 腿核验，只挂事件不碰钱），
         # 消费者=msg-watch（C2 心跳盘中实时价执行卖出+关槽，claim 硬门）
         "MSG_EXPIRE",
         # 事件链注入采集（2026-09-03 M1，news-collect 心跳 v2 方案 §4/§5）：
         # COLLECT 独立新增（不复用 REFRESH——已退役，见上）
         "COLLECT",
         # 批量分析外部强制刷新（2026-09-04 analysis-ttl 方案 §三）：分析刷新外部请求，
         # consumer=analysis-watch（专用心跳 analysis-watch-monitor 认领）
         "ANALYSIS_REFRESH"]
STATUSES = ["pending", "processing", "done", "failed"]

# v12 claim 硬门：消息链路四类型仅专用心跳（consumer='msg-watch'）可认领；
# 存量类型不在本集合内 → 不校验（向后兼容，晨审/旧心跳照常 claim）。
MSG_TYPES = ("MSG_CANDIDATE", "MSG_ORDER", "MSG_REJUDGE", "MSG_EXPIRE")
MSG_CONSUMER = "msg-watch"

# M1 claim 硬门扩展（news-collect 心跳 v2 方案 §5）：COLLECT 仅专用心跳
# （consumer='news-collect'，与 job 名一致）可认领；存量类型不校验（不动
# msg-watch 既有逻辑，只加 COLLECT 分支）。
COLLECT_TYPES = ("COLLECT",)
COLLECT_CONSUMER = "news-collect"

# analysis-ttl 改造（2026-09-04 方案 §三）：ANALYSIS_REFRESH 仅专用心跳
# （consumer='analysis-watch'）可认领；存量类型不校验（同 COLLECT 模式 fail-closed，
# 只加 ANALYSIS 分支）。分析刷新外部请求：事件强制插队重跑池内股批量分析。
ANALYSIS_TYPES = ("ANALYSIS_REFRESH",)
ANALYSIS_CONSUMER = "analysis-watch"

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,               -- DEEP_DIVE/WATCH_ALERT/CALENDAR/MSG_*/COLLECT/ANALYSIS_REFRESH
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/done/failed
    priority    INTEGER NOT NULL DEFAULT 3,  -- 1 最高，5 最低
    source      TEXT,                        -- 生产者: news-collector/x-scan/scan/analysis/user
    entity      TEXT,                        -- 实体: 股票代码/行业名/事件id
    payload     TEXT,                        -- JSON 附加参数
    creator     TEXT NOT NULL DEFAULT '',    -- 对象创建者（方案 v3 §1.1 词表，与 source=事件发射方区分）
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    claimed_at  TEXT,
    done_at     TEXT,
    note        TEXT                         -- 消费结果备注
);
CREATE INDEX IF NOT EXISTS idx_task_status ON task_events(status);
CREATE INDEX IF NOT EXISTS idx_task_type ON task_events(type);
CREATE INDEX IF NOT EXISTS idx_task_created ON task_events(created_at);
CREATE TABLE IF NOT EXISTS kv_store (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS watch_points (
    wp_id            TEXT PRIMARY KEY,    -- wp:<entity>:<epoch 秒>（碰撞加 -2 后缀）
    entity           TEXT NOT NULL,       -- 实体: 股票名
    code             TEXT,                -- 股票代码（可选，检测用）
    price            REAL NOT NULL,       -- 触发价（eval/buy: 上沿；sell: 下沿）
    min              REAL,                -- 区间另一沿（可选）
    mode             TEXT NOT NULL DEFAULT 'eval',  -- eval/buy/sell
    amount           REAL,                -- buy 预算（可选）
    note             TEXT DEFAULT '',
    created_by       TEXT DEFAULT '',     -- 对象创建者（迁移回填 analysis-watch，方案 v3 §1.1）
    added_at         TEXT,                -- 原格式 MM-DD HH:MM（kv 迁移保真）
    status           TEXT NOT NULL DEFAULT 'active',  -- active/removed（软删）/triggered
    consumed_at      TEXT,
    trigger_event_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wp_entity ON watch_points(entity, status);
"""


def get_db_path() -> str:
    return os.environ.get(ENV) or DEFAULT_DB


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    """PRAGMA table_info 判列存在（幂等迁移用）。"""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """幂等 schema 迁移（B3/B4）：旧库自动补列/建表。SCHEMA 建表已含新列，
    本函数只处理存量旧库（列缺失时 ALTER TABLE ADD COLUMN，可重入）。"""
    if _table_exists(conn, "task_events") and "creator" not in _table_columns(conn, "task_events"):
        conn.execute("ALTER TABLE task_events ADD COLUMN creator TEXT NOT NULL DEFAULT ''")


def connect() -> sqlite3.Connection:
    db = get_db_path()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    conn.commit()
    return conn


def validate_type(t: str) -> bool:
    return t in TYPES


def add(type_: str, entity: str, source: str = "user", priority: int = 3,
        payload: dict | None = None, creator: str = "") -> int:
    if not validate_type(type_):
        raise ValueError(f"未知事件类型 {type_!r}，可选: {', '.join(TYPES)}")
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO task_events (type, entity, source, priority, payload, creator) "
            "VALUES (?,?,?,?,?,?)",
            (type_, entity, source, int(priority),
             json.dumps(payload, ensure_ascii=False) if payload else None, creator or ""),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def claim(task_id: int, consumer: str | None = None) -> dict | None:
    """原子认领：pending → processing。认领失败（已被抢/状态不对/不存在）返回 None。

    v12 claim 硬门：消息链路四类型（MSG_CANDIDATE/MSG_ORDER/MSG_REJUDGE/MSG_EXPIRE）
    仅 consumer='msg-watch' 可认领——不符抛 PermissionError（含归属提示）；consumer
    缺省同样拒绝（fail-closed，防旧 prompt 漏传参数绕过）。M1 扩展（news-collect
    心跳 v2 方案 §5）：COLLECT 仅 consumer='news-collect' 可认领，规则同构。
    存量类型不校验（向后兼容：晨审/旧心跳无 --consumer 照常 claim）。consumer
    传入时写进 payload（claimed_by）供审计。
    """
    conn = connect()
    try:
        row = conn.execute("SELECT id, type, payload FROM task_events WHERE id=?",
                           (task_id,)).fetchone()
        if row is None:
            return None
        if row["type"] in MSG_TYPES and consumer != MSG_CONSUMER:
            who = consumer or "(未提供 --consumer)"
            raise PermissionError(
                f"#{task_id} [{row['type']}] 属消息链路，唯一消费者=msg-watch"
                f"（专用心跳 stock-msg-watch）；当前 consumer={who}。"
                f"旧心跳/晨审请跳过 MSG_* 类型（review 修订#5：唯一消费者保证）")
        if row["type"] in COLLECT_TYPES and consumer != COLLECT_CONSUMER:
            who = consumer or "(未提供 --consumer)"
            raise PermissionError(
                f"#{task_id} [{row['type']}] 属采集任务链路，唯一消费者={COLLECT_CONSUMER}"
                f"（专用心跳 news-collect）；当前 consumer={who}。"
                f"存量消费者请跳过 COLLECT 类型（唯一消费者保证）")
        if row["type"] in ANALYSIS_TYPES and consumer != ANALYSIS_CONSUMER:
            who = consumer or "(未提供 --consumer)"
            raise PermissionError(
                f"#{task_id} [{row['type']}] 属批量分析刷新链路，唯一消费者={ANALYSIS_CONSUMER}"
                f"（专用心跳 analysis-watch）；当前 consumer={who}。"
                f"存量消费者请跳过 ANALYSIS_REFRESH 类型（唯一消费者保证）")
        cur = conn.execute(
            "UPDATE task_events SET status='processing', claimed_at=datetime('now','localtime') "
            "WHERE id=? AND status='pending'",
            (task_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None  # 只有 UPDATE 命中 pending 才算认领成功；否则一律失败
        if consumer:
            # 消费者写入 payload（审计：谁认领的）；payload 非 JSON 时不阻塞认领
            try:
                p = json.loads(row["payload"]) if row["payload"] else {}
                if not isinstance(p, dict):
                    p = {"_raw_payload": p}
            except (ValueError, TypeError):
                p = {"_raw_payload": row["payload"]}
            p["claimed_by"] = consumer
            conn.execute("UPDATE task_events SET payload=? WHERE id=?",
                         (json.dumps(p, ensure_ascii=False), task_id))
            conn.commit()
        r = conn.execute("SELECT * FROM task_events WHERE id=?", (task_id,)).fetchone()
        return dict(r)
    finally:
        conn.close()


def finish(task_id: int, status: str, note: str | None = None) -> bool:
    """processing → done/failed。只允许从 processing 流转，防重复消费。"""
    if status not in ("done", "failed"):
        raise ValueError("finish status 只能是 done/failed")
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE task_events SET status=?, done_at=datetime('now','localtime'), note=? "
            "WHERE id=? AND status='processing'",
            (status, note, task_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------- B2（WP6-B）失败码通道：taskbus fail --code/--ref/--result ----------

FAIL_RESULTS = ("done", "cancelled", "failed")


def fail_event(task_id: int, note: str | None = None, code: str | None = None,
               ref: str | dict | None = None, result: str = "failed") -> bool:
    """processing → failed，失败原因码落 payload.exec={result,code,ref,at}。

    WP6-B（方案 v3）：执行方（agent / 直调 CLI）用 --code 带回原因码
    （gate_reject/no_position/already_fulfilled/stale_quote/halted/price_drifted…），
    --ref 带回对象指针（'kind:id' 串或 JSON '{"kind":..,"id":..}'），--result 声明
    处置结果（done=已自动归档 / cancelled=放弃 / failed=默认失败）。
    行状态恒归 failed（状态机白名单不动），语义由 exec.result 携带（消费方读 payload）。
    """
    if result not in FAIL_RESULTS:
        raise ValueError(f"result 只能是 {'/'.join(FAIL_RESULTS)}，收到 {result!r}")
    conn = connect()
    try:
        row = conn.execute("SELECT payload FROM task_events WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return False
        try:
            p = json.loads(row["payload"]) if row["payload"] else {}
            if not isinstance(p, dict):
                p = {"_raw_payload": p}
        except (ValueError, TypeError):
            p = {"_raw_payload": row["payload"]}
        ref_val: str | dict | None = ref
        if isinstance(ref, str) and ref.strip().startswith("{"):
            try:
                ref_val = json.loads(ref)
            except (ValueError, TypeError):
                ref_val = ref  # JSON 解析失败保留原文（不阻塞落库）
        exec_payload = {"result": result, "code": code, "ref": ref_val,
                        "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        p["exec"] = exec_payload
        cur = conn.execute(
            "UPDATE task_events SET status='failed', done_at=datetime('now','localtime'), "
            "note=?, payload=? WHERE id=? AND status='processing'",
            (note, json.dumps(p, ensure_ascii=False), task_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def requeue(task_id: int) -> bool:
    """failed → pending 重试。"""
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE task_events SET status='pending', claimed_at=NULL, note=NULL WHERE id=? AND status='failed'",
            (task_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def recover(stale_hours: float = 2.0) -> int:
    """processing 超时（agent 崩溃卡死）→ 重置为 pending。返回恢复数量。"""
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE task_events SET status='pending', claimed_at=NULL, "
            "note=COALESCE(note,'') || '[recover: 超时重置]' "
            "WHERE status='processing' AND claimed_at IS NOT NULL "
            "AND (julianday('now','localtime') - julianday(claimed_at)) * 24 > ?",
            (float(stale_hours),),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def list_events(status: str | None = None, type_: str | None = None, limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM task_events WHERE 1=1"
        args = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if type_:
            sql += " AND type=?"
            args.append(type_)
        sql += " ORDER BY priority ASC, id DESC LIMIT ?"
        args.append(int(limit))
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def stats() -> dict:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM task_events GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        latest = conn.execute("SELECT * FROM task_events ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "latest_id": latest["id"] if latest else None,
            "latest": dict(latest) if latest else None,
        }
    finally:
        conn.close()


# ---------- kv 状态存储（watch_scan 等脚本的持久化状态） ----------

def kv_set(key: str, value: dict | str) -> None:
    """写入 KV（upsert）。value 为 dict 时序列化为 JSON。"""
    conn = connect()
    try:
        v = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
            (key, v),
        )
        conn.commit()
    finally:
        conn.close()


def kv_get(key: str) -> dict | str | None:
    """读取 KV。JSON 可解析则返回 dict，否则返回原始字符串。"""
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return row[0]
    finally:
        conn.close()


def kv_update(key: str, mutate) -> dict:
    """原子读改写（B1 审计既存 bug 修复）：同一 BEGIN IMMEDIATE 事务内完成
    读→改→写，防并发 kv_get→改→kv_set 互相覆盖丢点。mutate(dict)->dict。

    CLI watchpoint add/remove 走本入口；watch_scan 等旧调用方走 kv_get/kv_set
    不受影响（行为不变）。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        if row is None:
            current: dict | str | None = None
        else:
            try:
                current = json.loads(row[0])
            except (ValueError, TypeError):
                current = row[0]
        if not isinstance(current, dict):
            current = {}  # 旧值为 str/None → 按 {} 起步（不崩，调用方重建）
        new_value = mutate(current)
        if not isinstance(new_value, dict):
            raise ValueError("kv_update mutate 必须返回 dict")
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (key, json.dumps(new_value, ensure_ascii=False)),
        )
        conn.commit()
        return new_value
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- B1（WP5）watch_points 表化：wp_id / 迁移 / 行操作 ----------

WP_BACKFILL_CREATOR = "analysis-watch"  # 方案 v3 §1.1：48 个历史挂点存量回填


def wp_make_id(entity: str, added_at: str | None) -> str:
    """wp:<entity>:<added_at 或 now 的 epoch 秒>——同输入稳定（迁移可重入）。

    added_at 为 kv 原格式（MM-DD HH:MM，无年份）→ 解析失败/缺省退化为
    当前 epoch 秒。同秒同实体多点的碰撞由调用方查重时加 -2 后缀解决。
    """
    epoch = None
    if added_at:
        try:
            dt = datetime.strptime(added_at, "%m-%d %H:%M").replace(
                year=datetime.now().year)
            epoch = int(dt.timestamp())
        except (ValueError, TypeError):
            epoch = None
    if epoch is None:
        epoch = int(datetime.now().timestamp())
    return f"wp:{entity}:{epoch}"


def _wp_row_from_kv(entity: str, p: dict) -> dict:
    """kv 点（旧形状 {code,price,note,mode,amount,min,added_at}）→ 表行。"""
    return {
        "entity": entity,
        "code": p.get("code"),
        "price": p.get("price"),
        "min": p.get("min"),
        "mode": p.get("mode", "eval"),
        "amount": p.get("amount"),
        "note": p.get("note", ""),
        "added_at": p.get("added_at", ""),
    }


def migrate_watch_points(creator: str = WP_BACKFILL_CREATOR, dry_run: bool = False) -> int:
    """kv_store('watch_points') → watch_points 表（幂等，可重入）。

    - wp_id 按 wp:<entity>:<added_at epoch 秒> 生成（同输入稳定）；
    - 插入前按（entity, price, mode, added_at）查重，已存在则跳过 → 连跑两次行数不变；
    - 同秒同实体多点 wp_id 冲突时递增后缀 -2/-3…（保持稳定可重入）；
    - 全部处理完后写 kv 标记 watch_points_migrated_at（ISO 时间）；dry_run=True 时
      只统计不写入、也不写标记（审计补丁 2026-09-10：生产窗口先 --dry-run 过目）。
    返回本次新导入行数。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM kv_store WHERE key='watch_points'").fetchone()
        if row is None:
            conn.execute("COMMIT")
            return 0
        try:
            points = json.loads(row[0])
        except (ValueError, TypeError):
            points = {}
        if not isinstance(points, dict):
            points = {}
        existing = {
            (r["entity"], r["price"], r["mode"], r["added_at"]): r["wp_id"]
            for r in conn.execute(
                "SELECT wp_id, entity, price, mode, added_at FROM watch_points")
        }
        imported = 0
        now_epoch = int(datetime.now().timestamp())
        for entity, pts in points.items():
            if not isinstance(pts, list):
                continue
            for p in pts:
                if not isinstance(p, dict) or p.get("price") is None:
                    continue
                item = _wp_row_from_kv(entity, p)
                key = (item["entity"], item["price"], item["mode"], item["added_at"])
                if key in existing:
                    continue  # 幂等：内容已导入过（按内容去重，不按 wp_id）
                base_id = wp_make_id(entity, item["added_at"])
                if item["added_at"] == "":
                    base_id = f"wp:{entity}:{now_epoch}"
                wp_id = base_id
                suffix = 2
                while conn.execute("SELECT 1 FROM watch_points WHERE wp_id=?",
                                   (wp_id,)).fetchone():
                    wp_id = f"{base_id}-{suffix}"
                    suffix += 1
                if not dry_run:
                    conn.execute(
                        "INSERT INTO watch_points (wp_id, entity, code, price, min, mode, "
                        "amount, note, created_by, added_at, status) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (wp_id, item["entity"], item["code"], item["price"], item["min"],
                         item["mode"], item["amount"], item["note"], creator,
                         item["added_at"], "active"),
                    )
                existing[key] = wp_id
                imported += 1
        if dry_run:
            conn.rollback()          # 审计补丁：dry-run 零写入、不写标记
            return imported
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES ('watch_points_migrated_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),),
        )
        conn.commit()
        return imported
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wp_insert(entity: str, price: float, mode: str = "eval", code: str | None = None,
              min_price: float | None = None, amount: float | None = None,
              note: str = "", created_by: str = "", added_at: str | None = None) -> str:
    """插入单点（CLI add 用）：wp_id 同规则生成，同 entity+price+mode+added_at
    已存在（含 removed 软删行）→ 跳过返回已有 wp_id（幂等）。"""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        added = added_at or datetime.now().strftime("%m-%d %H:%M")
        key = (entity, round(price, 2), mode, added)
        dup = conn.execute(
            "SELECT wp_id FROM watch_points WHERE entity=? AND price=? AND mode=? "
            "AND added_at=?", (entity, round(price, 2), mode, added)).fetchone()
        if dup:
            conn.execute("COMMIT")
            return dup["wp_id"]
        base_id = f"wp:{entity}:{int(datetime.now().timestamp())}"
        wp_id = base_id
        suffix = 2
        while conn.execute("SELECT 1 FROM watch_points WHERE wp_id=?",
                           (wp_id,)).fetchone():
            wp_id = f"{base_id}-{suffix}"
            suffix += 1
        conn.execute(
            "INSERT INTO watch_points (wp_id, entity, code, price, min, mode, amount, "
            "note, created_by, added_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (wp_id, entity, code, round(price, 2), min_price, mode, amount, note,
             created_by, added, "active"),
        )
        conn.commit()
        return wp_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _wp_ensure_migrated() -> None:
    """审计补丁（2026-09-10 主代理 R1.5）：迁移必须有生产触发点。

    原缺陷：migrate_watch_points() 只有单测调用 → 存量 kv 点永不进表，
    C 批切读表后全部挂点静默失效。修复：任何读路径（wp_list）在迁移标记缺失时
    自动补迁一次（幂等）；失败只告警不崩读路径。
    """
    try:
        if kv_get("watch_points_migrated_at"):
            return
        n = migrate_watch_points()
        if n:
            sys.stderr.write(f"[watch_points] 自动迁移 kv → 表：导入 {n} 点\n")
    except Exception as e:                      # noqa: BLE001 — 读路径不因迁移失败而崩
        sys.stderr.write(f"⚠️ [watch_points] 自动迁移失败（读路径继续，表内容可能不全）: {e}\n")


def wp_list(active_only: bool = True) -> list[dict]:
    """列出表行（默认只列 active；active_only=False 含 removed/triggered）。"""
    _wp_ensure_migrated()
    conn = connect()
    try:
        sql = "SELECT * FROM watch_points"
        if active_only:
            sql += " WHERE status='active'"
        sql += " ORDER BY entity, added_at, wp_id"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def wp_remove(entity: str) -> int:
    """软删（B1）：表行置 status='removed'，返回软删行数（物理行保留）。"""
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE watch_points SET status='removed' WHERE entity=? AND status='active'",
            (entity,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def wp_reconcile() -> dict:
    """对账（B4）：表 active 行 vs kv('watch_points') 实体/点集合。

    返回 {match, table_active, kv_points, only_table, only_kv}。
    """
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT entity, price, mode, added_at FROM watch_points WHERE status='active'")]
    finally:
        conn.close()
    table_set = {(r["entity"], r["price"], r["mode"], r["added_at"] or "") for r in rows}
    kv = kv_get("watch_points")
    kv_set: set = set()
    if isinstance(kv, dict):
        for entity, pts in kv.items():
            if isinstance(pts, list):
                for p in pts:
                    if isinstance(p, dict) and p.get("price") is not None:
                        kv_set.add((entity, p.get("price"), p.get("mode", "eval"),
                                    p.get("added_at", "")))
    return {
        "match": table_set == kv_set,
        "table_active": len(table_set),
        "kv_points": len(kv_set),
        "only_table": sorted(table_set - kv_set),
        "only_kv": sorted(kv_set - table_set),
    }
