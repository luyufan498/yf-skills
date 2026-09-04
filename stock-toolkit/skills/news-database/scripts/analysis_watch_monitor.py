#!/usr/bin/env python3
"""analysis-watch 心跳 monitor（analysis-ttl 方案：待分析队列三查之 a）。

每拍输出（心跳 agent 按输出变化唤醒，无变化=IDLE 睡眠，字节稳定语义）：
  a) analysis_schedule 待分析队列（TOP N，refresh_required=1 插队优先 →
     last_analyzed_at 最久/NULL 最前）：
     [ANALYSIS-DUE] <stock> 上次 <last_analyzed_at or never> 原因=<refresh:reason|ttl-过期天数|ttl-never>

无待分析（空表未 seed / 全部 TTL 内）→ "IDLE"（无时间戳，字节稳定直醒）。

monitor 只输出不产事件（自身触发=输出字节变化）；外部强制刷新事件
（task_events ANALYSIS_REFRESH）由 analysis-watch 心跳消费侧 claim 后调
analysis_schedule.request_refresh 打标，本 monitor 读 tasks.db 只为把
pending ANALYSIS_REFRESH 数输出成 [ANALYSIS-INJECT] 行（同构 COLLECT-INJECT，
否则注入事件不改字节 → 心跳永不唤醒 → 事件积压死锁）。

生产库约束：newsdb 侧仅幂等建表（analysis_schedule，不触碰其他表）。

用法（cron 或手动）：
  python3 analysis_watch_monitor.py            # 读 STOCK_NEWS_DB
"""

import sys
from datetime import datetime
from pathlib import Path

# 让脚本可从任意 cwd 直接运行（scripts/ 下找 news_database 包）
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from news_database import analysis_schedule as asched  # noqa: E402
from news_database.config import get_db_path  # noqa: E402


def query_due_lines(conn, now) -> list[str]:
    """查 a) analysis_schedule 待分析队列 → 输出行列表（TOP N，含原因）。"""
    now_s = asched._fmt(now)
    lines = []
    for row in asched.due_stocks(conn, now=now_s):
        last = row["last_analyzed_at"] if row["last_analyzed_at"] else "never"
        reason = asched.due_reason(row, now=now_s)
        lines.append(f"[ANALYSIS-DUE] {row['stock']} 上次 {last} 原因={reason}")
    return lines


def query_inject_lines() -> list[str]:
    """查 b) tasks.db pending ANALYSIS_REFRESH 数（只读；无表/无库=0）。

    同构 news_collect_monitor 的 COLLECT-INJECT：注入事件改变本行字节 →
    心跳唤醒 → 消费侧 claim --consumer analysis-watch → request_refresh 打标。
    """
    import os
    import sqlite3
    tasks_db = os.getenv(
        "STOCK_TASKS_DB",
        "/home/catmouse/Github_Project/daily-stock-workspace/data/tasks/tasks.db")
    if not os.path.exists(tasks_db):
        return []
    try:
        tconn = sqlite3.connect(f"file:{tasks_db}?mode=ro", uri=True)
        try:
            n = tconn.execute(
                "SELECT COUNT(*) FROM task_events "
                "WHERE type='ANALYSIS_REFRESH' AND status='pending'"
            ).fetchone()[0]
        finally:
            tconn.close()
    except sqlite3.OperationalError:
        n = 0  # 无 task_events 表（未 init）→ 视为 0
    return [f"[ANALYSIS-INJECT] pending {n}"] if n > 0 else []


def main() -> int:
    now = datetime.now()

    from news_database.db import connect as _connect

    conn = _connect(get_db_path())
    try:
        asched.ensure_table(conn)
        lines = query_due_lines(conn, now)
    finally:
        conn.close()

    lines.extend(query_inject_lines())

    if not lines:
        print("IDLE")
    else:
        for line in lines:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
