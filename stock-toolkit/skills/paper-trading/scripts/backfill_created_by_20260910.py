#!/usr/bin/env python3
"""created_by 一次性回填（任务书 A4，2026-09-10 条件/挂单执行链改造 v3）

存量三对象 created_by 全空 → 按方案 §1.1 词表回填：

  event_slots    全部 → 'msg-watch'（sleeve 槽构造如此：MSG 发起、失败归宿=msg-watch）
  conditions     type in (trailing_stop, cost_protection) 且 peak_price 非空
                   → 'atr-auto'（ATR 自动线：sync_trailing_stop/sync_cost_protection 挂载）
                 其余 → 'analysis-watch'（分析者预设/技术组挂点：无机械默认）

两态：
  --dry-run   只统计打印，零写入（默认）
  --apply     事务内回填（幂等：已非空 created_by 的行不动，二跑零行）

只动 created_by 一列，零价格/状态改写。生产库使用前先备份（红线：改动前备份）。
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_WS = Path('/home/catmouse/Github_Project/daily-stock-workspace')


def _db_path(ws_root: Path) -> Path:
    return ws_root / 'master_pool.db'


def backfill(conn: sqlite3.Connection, apply: bool) -> dict:
    """回填 created_by。返回 {表: [(creator, 行数), ...]} 统计（含 0 行）。"""
    stats = {'event_slots': [], 'conditions': []}
    now = None
    with conn:
        # --- event_slots：全部 → msg-watch（只动 created_by 空行）---
        n = conn.execute(
            "UPDATE event_slots SET created_by='msg-watch' "
            "WHERE COALESCE(created_by,'')=''").rowcount
        stats['event_slots'].append(('msg-watch', n))

        # --- conditions：trailing_stop/cost_protection 且 peak_price 非空 → atr-auto ---
        n_atr = conn.execute(
            "UPDATE conditions SET created_by='atr-auto' "
            "WHERE COALESCE(created_by,'')='' AND type IN ('trailing_stop','cost_protection') "
            "AND peak_price IS NOT NULL").rowcount
        stats['conditions'].append(('atr-auto', n_atr))

        # --- conditions：其余（含事件条件/TP 阶梯/add_position）→ analysis-watch ---
        n_rest = conn.execute(
            "UPDATE conditions SET created_by='analysis-watch' "
            "WHERE COALESCE(created_by,'')=''").rowcount
        stats['conditions'].append(('analysis-watch', n_rest))
    return stats


def main():
    ap = argparse.ArgumentParser(description='created_by 一次性回填（A4）')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true', help='只统计，零写入')
    g.add_argument('--apply', action='store_true', help='执行回填（幂等）')
    ap.add_argument('--workspace', default=None,
                    help='工作区根（默认 env STOCK_ANALYSIS_WORKSPACE，再默认 daily-stock-workspace）')
    args = ap.parse_args()

    # v13/A4：workspace 解析链——CLI 显式 > env STOCK_ANALYSIS_WORKSPACE > 生产默认
    ws = Path(args.workspace or os.environ.get('STOCK_ANALYSIS_WORKSPACE')
              or str(DEFAULT_WS))
    db = _db_path(ws)
    if not db.exists():
        # 测试/副本场景：数据库可能尚未建——直接建（migrate 走 paper_trading_v2），
        # 不触网。仅在 --dry-run 也允许（零写入指业务数据，建库结构不算回填写入）。
        try:
            from paper_trading_v2.db import get_connection, migrate_db
            conn = get_connection(db)
            migrate_db(conn)
            conn.close()
        except Exception as e:
            print(f"❌ 数据库不存在且建库失败: {db}（{e}）")
            sys.exit(1)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        # 存量库尚未迁移（无 created_by 列）→ 先跑 migrate_db（幂等，只加列）。
        # 红线：migrate 只加列零行改写，不构成回填写入；显式 SQL 连接读 schema 不生效，
        # 须走 paper_trading_v2.db.get_connection 的迁移入口。
        cols = [r[1] for r in conn.execute("PRAGMA table_info(conditions)").fetchall()]
        es_cols = [r[1] for r in conn.execute("PRAGMA table_info(event_slots)").fetchall()]
        if 'created_by' not in cols or 'created_by' not in es_cols:
            from paper_trading_v2.db import get_connection, migrate_db
            mconn = get_connection(db)
            migrate_db(mconn)
            mconn.close()
            conn.close()
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
        # 前置统计（dry-run 只读这些数）
        n_slots = conn.execute(
            "SELECT COUNT(*) FROM event_slots WHERE COALESCE(created_by,'')=''").fetchone()[0]
        n_conds = conn.execute(
            "SELECT COUNT(*) FROM conditions WHERE COALESCE(created_by,'')=''").fetchone()[0]
        print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] {db}")
        print(f"  待回填: event_slots {n_slots} 行, conditions {n_conds} 行")
        if args.dry_run:
            # dry-run 预演分类口径（只读）
            n_atr = conn.execute(
                "SELECT COUNT(*) FROM conditions WHERE COALESCE(created_by,'')='' "
                "AND type IN ('trailing_stop','cost_protection') "
                "AND peak_price IS NOT NULL").fetchone()[0]
            n_rest = n_conds - n_atr
            print(f"  event_slots → msg-watch: {n_slots}")
            print(f"  conditions → atr-auto: {n_atr}（trailing_stop/cost_protection 且有 peak_price）")
            print(f"  conditions → analysis-watch: {n_rest}")
            print("DRY-RUN 零写入（--apply 执行）")
            return
        stats = backfill(conn, apply=True)
        for table, rows in stats.items():
            for creator, n in rows:
                print(f"  {table} → {creator}: {n} 行")
        # 复核：剩余空行
        left_slots = conn.execute(
            "SELECT COUNT(*) FROM event_slots WHERE COALESCE(created_by,'')=''").fetchone()[0]
        left_conds = conn.execute(
            "SELECT COUNT(*) FROM conditions WHERE COALESCE(created_by,'')=''").fetchone()[0]
        print(f"  复核剩余空行: event_slots {left_slots}, conditions {left_conds}")
        print("APPLY 完成（幂等：二跑零行）")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
