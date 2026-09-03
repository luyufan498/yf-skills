"""任务级调度表 task_schedule：TTL 在任务行（news-collect 心跳 v2，方案 §3）。

与 scan.py/scan_log 的关系：scan_log 是旧 per-scope 扫描游标（PK=(scope_type,scope_id)，
7 个旧 cron 依赖，UPSERT 依赖该 PK），**保持原样不动**；本模块新增独立的 task_schedule
表存任务级节奏（T1-T4 仅几行），两套并存，过渡期旧 cron 照写 scan_log。
"""

import sqlite3
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 表结构（方案 §3）——仅几行，任务级节奏真源
# ---------------------------------------------------------------------------

TASK_SCHEDULE_DDL = """
CREATE TABLE IF NOT EXISTS task_schedule (
    task_id     TEXT PRIMARY KEY,     -- 'xueqiu_sentiment'|'daily_news'|'deep_analysis'|'industry_research'
    last_run_at TEXT,                 -- 上次任务完成
    ttl_hours   REAL NOT NULL,        -- 节奏（T1=4~8h 交易时段对齐、T2=12h、T3=24-48h、T4=触发+15-30天）
    next_due_at TEXT NOT NULL,        -- last_run_at + ttl
    last_result TEXT                  -- 上次轮次摘要
);
"""

# ---------------------------------------------------------------------------
# 任务默认矩阵（方案 §2 任务清单 v0.1 基础版）——monitor 初始化 seed 用
# ---------------------------------------------------------------------------

# T1 雪球情绪扫描：方案节奏=每交易日 2 轮（10:05/17:05，沿旧 cron 节奏），
# 由调度层对齐交易时段落地；存储侧先用 ttl_hours=4 占位（两轮间隔约 7h，
# TTL≥间隔才不吞轮；§7 频率上限 T1≥4h 同值）。M1 迁移时若改为调度层精确
# 挂时点，此值仅作兜底节奏。
XUEQIU_SENTIMENT = "xueqiu_sentiment"
TTL_XUEQIU_SENTIMENT = 4.0   # 占位：交易时段 2 轮语义由调度对齐（见注释）

# T2 日常新闻轮：07:00 + 19:00 两轮 → 12h TTL。
DAILY_NEWS = "daily_news"
TTL_DAILY_NEWS = 12.0

# T3 深度分析探索：方案 24-48h，取中 36h。
DEEP_ANALYSIS = "deep_analysis"
TTL_DEEP_ANALYSIS = 36.0

# T4 产业/政策调查：行业事件触发优先（事件链，不走本表节奏）；无事件时
# 15-30 天兜底，取 30 天=720h 固定值（审计 C9：定值+抖动防洪峰由调度层加）。
INDUSTRY_RESEARCH = "industry_research"
TTL_INDUSTRY_RESEARCH = 720.0

# 默认矩阵：monitor 初始化（seed）时写入缺行。
DEFAULT_TASK_MATRIX = (
    # (task_id, ttl_hours)
    (XUEQIU_SENTIMENT, TTL_XUEQIU_SENTIMENT),
    (DAILY_NEWS, TTL_DAILY_NEWS),
    (DEEP_ANALYSIS, TTL_DEEP_ANALYSIS),
    (INDUSTRY_RESEARCH, TTL_INDUSTRY_RESEARCH),
)


def _now_str() -> str:
    """本地时间戳，格式与 newsdb 其余表一致（datetime('now','localtime')）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def ensure_table(conn: sqlite3.Connection) -> None:
    """幂等建表（IF NOT EXISTS），不触碰 scan_log/其他表。可重复调用。"""
    conn.execute(TASK_SCHEDULE_DDL)
    conn.commit()


def ensure_default_tasks(conn: sqlite3.Connection) -> None:
    """默认任务矩阵 seed（缺行才 INSERT，幂等，不覆盖已有行的节奏）。"""
    ensure_table(conn)
    for task_id, ttl in DEFAULT_TASK_MATRIX:
        conn.execute(
            """
            INSERT INTO task_schedule (task_id, ttl_hours, next_due_at)
            VALUES (?, ?, datetime('now','localtime'))
            ON CONFLICT(task_id) DO NOTHING
            """,
            (task_id, ttl),
        )
    conn.commit()


def due_tasks(conn: sqlite3.Connection, now: str | None = None) -> list[sqlite3.Row]:
    """返回 next_due_at <= now 的任务行（task_id/last_run_at/ttl_hours/next_due_at）。

    now 缺省取当前本地时间。表不存在 → 先建表，返回 []（安全空跑）。
    """
    ensure_table(conn)
    if now is None:
        now = _now_str()
    return conn.execute(
        """
        SELECT task_id, last_run_at, ttl_hours, next_due_at
        FROM task_schedule
        WHERE next_due_at <= ?
        ORDER BY next_due_at
        """,
        (now,),
    ).fetchall()


def mark_task_run(conn: sqlite3.Connection, task_id: str, result: str = "",
                  now: str | None = None) -> None:
    """记录任务完成：last_run_at=now，next_due_at=now+ttl_hours，last_result=result。

    任务行不存在 → 自动建行（从 now 起算 TTL）。now 缺省取当前本地时间。
    """
    ensure_table(conn)
    if now is None:
        now_dt = datetime.now()
        now_s = _fmt(now_dt)
    else:
        now_s = now
        now_dt = _parse(now)
    ttl = conn.execute(
        "SELECT ttl_hours FROM task_schedule WHERE task_id=?", (task_id,)
    ).fetchone()
    if ttl is None:
        # 未知任务：按 T2 缺省节奏入行（调用方应先 ensure_default_tasks/INSERT 显式节奏）
        ttl_hours = TTL_DAILY_NEWS
        conn.execute(
            "INSERT INTO task_schedule (task_id, ttl_hours, next_due_at) VALUES (?, ?, ?)",
            (task_id, ttl_hours, _fmt(now_dt + timedelta(hours=ttl_hours))),
        )
    else:
        ttl_hours = ttl["ttl_hours"]
    conn.execute(
        """
        UPDATE task_schedule
        SET last_run_at=?, next_due_at=?, last_result=?
        WHERE task_id=?
        """,
        (now_s, _fmt(now_dt + timedelta(hours=ttl_hours)), result, task_id),
    )
    conn.commit()


def stale_check_enabled() -> bool:
    """T5 空置检查开关（M0 默认关闭，NEWS_COLLECT_STALE_CHECK=1 才启用）。

    M0 阶段池内 88 只股全算会长期 N>0 → monitor 每拍变字节 → 空跑 IDLE 不稳。
    """
    import os
    return os.getenv("NEWS_COLLECT_STALE_CHECK") == "1"
