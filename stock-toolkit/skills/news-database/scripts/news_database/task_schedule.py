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
# 语义（方案 §2/§7）：TTL 是**任务兜底节奏**，不是精确挂时点——
#   T1 交易时段 2 轮（10:05/17:05）的精确对齐由调度层（cron prompt）落，
#   ttl_hours=4 是频率下限（§7 T1≥4h 防风暴）+ 两轮间的兜底间隔；
#   T4 行业事件触发优先（事件链注入 COLLECT，不走本表节奏），720h 仅无事件兜底。
# ---------------------------------------------------------------------------

# T1 雪球情绪扫描：方案节奏=每交易日 2 轮（10:05/17:05，沿旧 cron 节奏）。
# 调度对齐交易时段：精确挂时点由 cron prompt 按 10:05/17:05 触发，本值 4h
# 为 §7 频率下限（防风暴）与错过挂时点时的兜底节奏（TTL≥两轮间隔 7h 才不吞轮，
# 兜底场景按 4h 下限即可，两轮完整跑时靠调度挂时点）。M1 迁移期调度层未精确
# 挂时点前，此值即实际节奏（约每 4h 一轮，覆盖 2 轮语义）。
XUEQIU_SENTIMENT = "xueqiu_sentiment"
TTL_XUEQIU_SENTIMENT = 4.0   # 调度对齐交易时段（见上方注释）

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


def list_tasks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """返回 task_schedule 全表行（按 next_due_at 升序）。

    表不存在 → 先建表，返回 []。供 cron agent/monitor 查看全量任务节奏。
    """
    ensure_table(conn)
    return conn.execute(
        """
        SELECT task_id, last_run_at, ttl_hours, next_due_at, last_result
        FROM task_schedule
        ORDER BY next_due_at
        """
    ).fetchall()


def get_task(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    """按 task_id 取单行；不存在返回 None。"""
    ensure_table(conn)
    return conn.execute(
        "SELECT task_id, last_run_at, ttl_hours, next_due_at, last_result "
        "FROM task_schedule WHERE task_id=?",
        (task_id,),
    ).fetchone()


def reset_task(conn: sqlite3.Connection, task_id: str,
               ttl_hours: float | None = None) -> bool:
    """手工重置任务 TTL：next_due_at=now（立即到期），last_run_at 清空（never）。

    ttl_hours 给定则同时改节奏（含默认矩阵外的新任务行）；不给则保留原 ttl。
    行不存在 → 按 DEFAULT_TASK_MATRIX/缺省节奏建行后再重置（效果=立即到期）。
    返回 True（行存在被重置）；行不存在建行后也返回 False 语义不明——统一返回
    True（重置后处于到期态）。last_result 保留（历史轮次摘要仍有参考价值）。
    """
    ensure_table(conn)
    effective_ttl = ttl_hours
    if effective_ttl is None:
        row = conn.execute(
            "SELECT ttl_hours FROM task_schedule WHERE task_id=?", (task_id,)
        ).fetchone()
        # 未知任务：与 mark_task_run 缺省一致（T2 节奏）
        effective_ttl = TTL_DAILY_NEWS if row is None else row["ttl_hours"]
    conn.execute(
        """
        INSERT INTO task_schedule (task_id, last_run_at, ttl_hours, next_due_at)
        VALUES (?, NULL, ?, datetime('now','localtime'))
        ON CONFLICT(task_id) DO UPDATE SET
            last_run_at=NULL, next_due_at=datetime('now','localtime'),
            ttl_hours=excluded.ttl_hours
        """,
        (task_id, float(effective_ttl)),
    )
    conn.commit()
    return True


def stale_check_enabled() -> bool:
    """T5 空置检查开关（M0 默认关闭，NEWS_COLLECT_STALE_CHECK=1 才启用）。

    M0 阶段池内 88 只股全算会长期 N>0 → monitor 每拍变字节 → 空跑 IDLE 不稳。
    """
    import os
    return os.getenv("NEWS_COLLECT_STALE_CHECK") == "1"
