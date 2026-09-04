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

import json
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


def query_calendar_lines(now) -> list[str]:
    """查 c) tasks.db 到期 CALENDAR（2026-09-04 从 legacy 迁入 analysis-watch）。

    CALENDAR=分析回查（财报/解禁到期→重新评估股票），属分析域归本心跳。
    到期语义同 watch_scan.check_calendar（2026-08-18 修复，防凌晨空触发）：
    纯日期 '2026-08-20' → 当天 15:30 到期；带时间 → 精确时刻。
    未到期不输出（安静睡眠）；到期 → 📅 行唤醒 agent 消费（claim → delegate 复评）。
    """
    import os
    import sqlite3
    tasks_db = os.getenv(
        "STOCK_TASKS_DB",
        "/home/catmouse/Github_Project/daily-stock-workspace/data/tasks/tasks.db")
    if not os.path.exists(tasks_db):
        return []
    out = []
    try:
        tconn = sqlite3.connect(f"file:{tasks_db}?mode=ro", uri=True)
        try:
            rows = tconn.execute(
                "SELECT id, entity, payload FROM task_events "
                "WHERE type='CALENDAR' AND status='pending'").fetchall()
        finally:
            tconn.close()
    except sqlite3.OperationalError:
        return []
    for rid, entity, payload in rows:
        try:
            p = json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            continue
        due_raw = str(p.get("due") or "")
        if not due_raw:
            continue
        try:
            if "T" in due_raw or " " in due_raw:
                due_dt = datetime.fromisoformat(due_raw[:19].replace(" ", "T"))
            else:
                due_dt = datetime.strptime(due_raw[:10], "%Y-%m-%d").replace(
                    hour=15, minute=30)
        except ValueError:
            continue  # due 格式非法 → 跳过不触发不崩溃
        if due_dt <= now:
            out.append(f"📅 CALENDAR 到期 #{rid} {entity} due={due_dt:%Y-%m-%d %H:%M} "
                       f"[{p.get('event', '')}]")
    return out


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
    lines.extend(query_calendar_lines(now))

    if not lines:
        print("IDLE")
    else:
        for line in lines:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
