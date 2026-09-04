#!/usr/bin/env python3
"""analysis_schedule 池 sync（2026-09-04，取代 CANDIDATE 事件）：

pool 全量幂等 diff 入 analysis_schedule——新入池股自动进表
（last_analyzed_at=NULL → due_stocks 最优先 → 下轮触发首次分析），
出池股保留行（历史不删）。analysis-watch 每轮处理前调用。

用法：python3 analysis_pool_sync.py   （读默认生产库；STOCK_NEWS_DB 可覆盖）
输出：sync 新增 N 行 / 总 M 行
"""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/home/catmouse/Github_Project/yf-skills/stock-toolkit/skills/news-database/scripts")
from news_database import analysis_schedule as asched
from news_database.db import connect as news_connect

POOL_DB = os.environ.get(
    "STOCK_ANALYSIS_WORKSPACE",
    "/home/catmouse/Github_Project/daily-stock-workspace/.paper-trading") + "/master_pool.db"
NEWS_DB = os.environ.get(
    "STOCK_NEWS_DB",
    "/home/catmouse/Github_Project/daily-stock-workspace/data/news/news.db")


def main() -> int:
    pc = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    try:
        stocks = sorted(r[0] for r in pc.execute(
            "SELECT stock FROM pool WHERE pool_status='active'"))
    finally:
        pc.close()
    conn = news_connect(NEWS_DB)
    try:
        n = asched.seed_pool(conn, stocks)
        total = conn.execute("SELECT COUNT(*) FROM analysis_schedule").fetchone()[0]
        never = conn.execute("SELECT COUNT(*) FROM analysis_schedule "
                             "WHERE last_analyzed_at IS NULL").fetchone()[0]
    finally:
        conn.close()
    print(f"sync 新增 {n} 行 | 总 {total}（从未分析 {never}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
