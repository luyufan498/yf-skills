#!/usr/bin/env python3
"""news-collect 心跳 monitor（M1 版，方案 §4 三查）。

每拍输出（心跳 agent 按输出变化唤醒，无变化=IDLE 睡眠，字节稳定语义）：
  a) task_schedule 到期行（T1-T4）：[COLLECT-DUE] <task_id> 上次 <last_run_at or never> ttl=<h>
  b) tasks.db pending COLLECT 事件数：[COLLECT-INJECT] pending N
  c) T5 空置候选（默认关闭，NEWS_COLLECT_STALE_CHECK=1 才启用）：
     集合签名字节稳定（newsdb kv last_stale_sig）：候选**集合无变化**输出固定行
     "[STALE] 空置候选 N 只"（字节不变）；**集合变化**才输出新行（含最长天数/
     名单更新）；N=0 输出空。

无任何到期/注入/空置输出 → "IDLE"。

生产库只读约束：newsdb 侧仅幂等建表（task_schedule/stale_check_log/kv_store，
不触碰 scan_log）；tasks.db 侧仅 SELECT（查 task_events 有无
type='COLLECT' AND status='pending' 行，表不存在=0）。

用法（cron 或手动）：
  python3 news_collect_monitor.py            # 读 STOCK_NEWS_DB / STOCK_TASKS_DB
"""

import os
import sqlite3
import sys
from datetime import datetime
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
    # T5（2026-09-04 从 legacy 迁入）：C1 异动初过滤后插的深挖请求——
    # DEEP_DIVE（个股/大盘异动原因不明需查证）+ MARKET_SHOCK（大盘暴跌）
    # 归 news-collect 消费（采集域），与 COLLECT 同源同门
    if tasks_db_path.exists():
        try:
            tconn2 = sqlite3.connect(f"file:{tasks_db_path}?mode=ro", uri=True)
            try:
                n2 = tconn2.execute(
                    "SELECT COUNT(*) FROM task_events "
                    "WHERE type IN ('DEEP_DIVE','MARKET_SHOCK') AND status='pending'"
                ).fetchone()[0]
            finally:
                tconn2.close()
        except sqlite3.OperationalError:
            n2 = 0
        if n2 > 0:
            lines.append(f"[COLLECT-INJECT] 深挖 {n2}（DEEP_DIVE/MARKET_SHOCK，C1 异动初过滤产出）")
    return lines


def query_stale_candidates(conn: sqlite3.Connection, now: datetime) -> list[str]:
    """查 c) T5 空置候选 → 输出行（仅 NEWS_COLLECT_STALE_CHECK=1 时被 main 调用）。

    M1 起（方案 §4 查 c + 任务定案）：候选**集合签名**存 newsdb kv
    （last_stale_sig）——集合无变化输出固定行（含 N，字节不变），集合变化
    才变字节（含最长天数/名单更新标记）；N=0 输出空。彻底解决 M0 担忧
    （88 只全算长期 N>0 → 每拍变字节）。
    候选判定走 news_collector.stale_candidates（口径=watchlist 股近 7 天
    零 messages 关联），输出行由 stale_line 统一生成。
    """
    from news_database import news_collector as nc
    from news_database import task_schedule as ts

    candidates = nc.stale_candidates(
        conn, days=STALE_DAYS, now=_fmt_dt(now))
    return nc.stale_line(candidates, conn, enabled=ts.stale_check_enabled())


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
