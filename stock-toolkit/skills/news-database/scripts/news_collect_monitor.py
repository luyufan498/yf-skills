#!/usr/bin/env python3
"""news-collect 心跳 monitor（M0 空跑观察版，方案 §4 三查）。

每拍输出（心跳 agent 按输出变化唤醒，无变化=IDLE 睡眠，字节稳定语义）：
  a) task_schedule 到期行（T1-T4）：[COLLECT-DUE] <task_id> 上次 <last_run_at or never> ttl=<h>
  b) tasks.db pending COLLECT 事件数：[COLLECT-INJECT] pending N
  c) T5 空置候选（默认关闭，NEWS_COLLECT_STALE_CHECK=1 才启用）：
     [STALE] 空置候选 N 只（最长 M 天），N=0 不输出。
     M0 默认关闭：池内 88 只全算会长期 N>0 → 每拍变字节 → 空跑 IDLE 不稳。

无任何到期/注入/空置输出 → "IDLE"。

生产库只读约束：newsdb 侧仅 ensure_table 幂等建表（新表 task_schedule，
不触碰 scan_log）；tasks.db 侧仅 SELECT（COLLECT 类型 M0 未登记白名单，
查 task_events 有无 type='COLLECT' AND status='pending' 行，表不存在=0）。

用法（cron 或手动）：
  python3 news_collect_monitor.py            # 读 STOCK_NEWS_DB / STOCK_TASKS_DB
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 让脚本可从任意 cwd 直接运行（scripts/ 下找 news_database 包）
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from news_database import task_schedule as ts  # noqa: E402
from news_database.config import get_db_path  # noqa: E402

WORKSPACE_DEFAULT_TASKS_DB = (
    "/home/catmouse/Github_Project/daily-stock-workspace/data/tasks/tasks.db"
)
STALE_DAYS = 7  # T5 空置判定：池内股近 7 天零关联（方案 §2.B，用户定案）


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def query_due_lines(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """查 a) task_schedule 到期行 → 输出行列表。"""
    due = ts.due_tasks(conn, now=_fmt_dt(now))
    lines = []
    for row in due:
        last = row["last_run_at"] if row["last_run_at"] else "never"
        lines.append(
            f"[COLLECT-DUE] {row['task_id']} 上次 {last} ttl={row['ttl_hours']:g}"
        )
    return lines


def query_collect_inject(tasks_db_path: Path, now: datetime) -> list[str]:
    """查 b) tasks.db pending COLLECT 事件数（只读；无表/无库=0）。

    COLLECT 类型 M0 尚未登记 task-bus 白名单（v12 迁移 M2 才上），
    这里只查 task_events 表有无 type='COLLECT' AND status='pending' 的行。
    """
    lines = []
    if tasks_db_path.exists():
        try:
            tconn = sqlite3.connect(f"file:{tasks_db_path}?mode=ro", uri=True)
            try:
                n = tconn.execute(
                    "SELECT COUNT(*) FROM task_events "
                    "WHERE type='COLLECT' AND status='pending'"
                ).fetchone()[0]
            finally:
                tconn.close()
        except sqlite3.OperationalError:
            n = 0  # 无 task_events 表（未 init）→ 视为 0
    else:
        n = 0
    if n > 0:
        lines.append(f"[COLLECT-INJECT] pending {n}")
    return lines


def query_stale_candidates(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """查 c) T5 空置候选 → 输出行（仅 NEWS_COLLECT_STALE_CHECK=1 时被 main 调用）。

    输出 [STALE] 空置候选 N 只（最长 M 天）；N=0 不输出（无变化）。
    候选=池内股（stocks 表）近 STALE_DAYS 天无任何关联内容
    （event_stock 关联事件的最新 fetched_at/started_at 早于 7 天前或缺失）。
    """
    cutoff = _fmt_dt(now - timedelta(days=STALE_DAYS))
    rows = conn.execute(
        """
        SELECT s.code, s.name,
               MAX(COALESCE(m.fetched_at, e.started_at)) AS last_related
        FROM stocks s
        LEFT JOIN event_stock es ON es.stock_code = s.code
        LEFT JOIN events e ON e.id = es.event_id
        LEFT JOIN messages m ON m.event_id = e.id
        GROUP BY s.code, s.name
        HAVING last_related IS NULL OR last_related < ?
        """,
        (cutoff,),
    ).fetchall()
    if not rows:
        return []
    related = [r["last_related"] for r in rows if r["last_related"]]
    if related:
        oldest = min(related)
        days = int((now - datetime.strptime(oldest, "%Y-%m-%d %H:%M:%S")).days)
        return [f"[STALE] 空置候选 {len(rows)} 只（最长 {days} 天）"]
    # 全部零关联（从未有任何内容映射）：最长天数无意义，省略
    return [f"[STALE] 空置候选 {len(rows)} 只"]


def main() -> int:
    now = datetime.now()
    lines: list[str] = []

    # 查 a) + c)：newsdb（task_schedule 幂等建表，不触碰 scan_log）
    from news_database.db import connect as _connect

    conn = _connect(get_db_path())
    ts.ensure_table(conn)
    lines.extend(query_due_lines(conn, now))
    if ts.stale_check_enabled():
        lines.extend(query_stale_candidates(conn, now))
    conn.close()

    # 查 b)：tasks.db（只读 SELECT，无表/无库=0）
    tasks_db = Path(os.getenv("STOCK_TASKS_DB", WORKSPACE_DEFAULT_TASKS_DB))
    lines.extend(query_collect_inject(tasks_db, now))

    if not lines:
        print("IDLE")
    else:
        for line in lines:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
