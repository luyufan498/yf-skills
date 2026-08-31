"""task-bus 数据库层：task_events 表 + 原子认领。"""
import json
import os
import sqlite3

ENV = "STOCK_TASKS_DB"
DEFAULT_DB = os.path.join(os.getcwd(), "data", "tasks", "tasks.db")

# 事件类型（任务域 intents）：与信息域（newsdb events 事实）分离
TYPES = ["CANDIDATE", "REFRESH", "DEEP_DIVE", "WATCH_ALERT", "REVIEW", "CALENDAR", "L3_SNAPSHOT"]
STATUSES = ["pending", "processing", "done", "failed"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,               -- CANDIDATE/REFRESH/DEEP_DIVE/WATCH_ALERT/REVIEW/CALENDAR
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/done/failed
    priority    INTEGER NOT NULL DEFAULT 3,  -- 1 最高，5 最低
    source      TEXT,                        -- 生产者: news-collector/x-scan/scan/analysis/user
    entity      TEXT,                        -- 实体: 股票代码/行业名/事件id
    payload     TEXT,                        -- JSON 附加参数
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
"""


def get_db_path() -> str:
    return os.environ.get(ENV) or DEFAULT_DB


def connect() -> sqlite3.Connection:
    db = get_db_path()
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def validate_type(t: str) -> bool:
    return t in TYPES


def add(type_: str, entity: str, source: str = "user", priority: int = 3,
        payload: dict | None = None) -> int:
    if not validate_type(type_):
        raise ValueError(f"未知事件类型 {type_!r}，可选: {', '.join(TYPES)}")
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO task_events (type, entity, source, priority, payload) VALUES (?,?,?,?,?)",
            (type_, entity, source, int(priority), json.dumps(payload, ensure_ascii=False) if payload else None),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def claim(task_id: int) -> dict | None:
    """原子认领：pending → processing。认领失败（已被抢/状态不对/不存在）返回 None。"""
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE task_events SET status='processing', claimed_at=datetime('now','localtime') "
            "WHERE id=? AND status='pending'",
            (task_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None  # 只有 UPDATE 命中 pending 才算认领成功；否则一律失败
        row = conn.execute("SELECT * FROM task_events WHERE id=?", (task_id,)).fetchone()
        return dict(row)
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
