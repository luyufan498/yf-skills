"""pool_publicize.py — v11 一次性迁移：非 NEWS open 段滞留 cash → 主池 free（公开化）

方案 v11-pool-model-20260902 §现金流-5：段即账户时代技术段里睡觉的现金（5.884M/15 段）
搬回 pool.free，使物理层恒等式 free + Σ段cash + Σ净持仓成本 == total 达成迁移后目标态
（技术 free = 1,000,000.20 + Σ迁移cash）。

纪律：
- 只搬 strategy != 'NEWS' 且 status='open' 且 cash>0 的段；NEWS 段/槽严禁触碰
  （消息池独立账，9:30 sleeve-fill 灰度锚红线）。closed 段不动（历史死壳，release 已结算）。
- 每笔 audit 'v11_publicize'（per-stock，free_before/after 真实）+ 末尾
  'v11_publicize_summary' 一行（amount=Σ搬运）。逐笔条件 UPDATE（cash>=? 认领）+
  同事务——重跑时 cash=0 → rowcount=0 天然幂等，零双计。
- --execute 前默认只预演（dry-run，打印 per-stock 明细，零写入）。
- 生产执行由主代理在审计窗口做：`python -m paper_trading_v2.pool_publicize --execute
  --date 2026-09-03`（--date 只写进 audit reason 供对账，不造时间机器）。

骨架仿 scripts 迁移惯例（argparse + --execute 门 + 预演打印），放在包内保证
tests_v2 全链 fixture 可 import（同包 CLI/测试同一 sys.path）。
"""
import argparse
import sys
from datetime import datetime

from paper_trading_v2.db import get_connection, migrate_db


def publicize(db_path, execute=False, date=None, quiet=False):
    """非 NEWS open 段 cash→pool_ledger.free。返回 {rowcount, moved_total, moves}。

    moves: [{stock, seg_id, amount, free_before, free_after}]（预演与执行同形态）。
    执行=单事务逐笔认领（cash>=? 条件 UPDATE）+ 池条件累加 + 逐笔 audit + summary 行；
    中途任何一笔认领失败（并发 release 抢先）→ 整事务回滚（要么全成要么零变化）。
    """
    conn = get_connection(db_path)
    migrate_db(conn)
    now = datetime.now().isoformat()
    tag = f"（迁移窗口 {date}）" if date else ""
    try:
        rows = conn.execute(
            "SELECT id, stock, cash FROM position WHERE status='open' "
            "AND COALESCE(strategy,'') != 'NEWS' AND COALESCE(cash,0) > 0 "
            "ORDER BY id").fetchall()
        if not rows:
            if not quiet:
                print("✅ v11 公开化预演：无非 NEWS open 段滞留现金（rowcount=0，幂等空转）")
            return {"rowcount": 0, "moved_total": 0.0, "moves": []}

        ledger = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()
        if ledger is None:
            raise SystemExit("❌ 主池未初始化（pool_ledger 无行），拒绝迁移")
        free = ledger[0]
        moves = []
        for r in rows:
            amt = r['cash']
            moves.append({"stock": r['stock'], "seg_id": r['id'], "amount": amt,
                          "free_before": free, "free_after": free + amt})
            free += amt
        moved_total = sum(m['amount'] for m in moves)

        if not execute:
            print(f"🔍 v11 公开化预演（未写入；确认无误后加 --execute）：")
            for m in moves:
                print(f"   段#{m['seg_id']:<4} {m['stock']:<8} cash ¥{m['amount']:,.2f} "
                      f"→ pool free ¥{m['free_before']:,.2f} → ¥{m['free_after']:,.2f}")
            print(f"   合计 {len(moves)} 段 / ¥{moved_total:,.2f}（NEWS 段不在清单）")
            return {"rowcount": len(moves), "moved_total": moved_total, "moves": moves,
                    "dry_run": True}

        with conn:
            for m in moves:
                cur = conn.execute(
                    "UPDATE position SET cash=ROUND(cash-?, 2), "
                    "source=COALESCE(source,'v11_publicized') WHERE id=? AND status='open' "
                    "AND COALESCE(cash,0)>=?", (m['amount'], m['seg_id'], m['amount']))
                if cur.rowcount == 0:
                    raise SystemExit(
                        f"❌ 段#{m['seg_id']} {m['stock']} 认领失败（并发 release/清零？）"
                        f"——整事务回滚，零变化")
                conn.execute(
                    "UPDATE pool_ledger SET free=ROUND(free+?, 2), updated_at=? WHERE id=1",
                    (m['amount'], now))
                conn.execute(
                    "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                    "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                    (now, 'v11_publicize', m['stock'], m['amount'], m['free_before'],
                     m['free_after'],
                     f"v11 公开化：段滞留现金→主池 free{tag}", 'v11_migration'))
            conn.execute(
                "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                (now, 'v11_publicize_summary', None, moved_total,
                 moves[0]['free_before'], moves[-1]['free_after'],
                 f"v11 公开化汇总：{len(moves)} 段 / ¥{moved_total:,.2f}{tag}",
                 'v11_migration'))
        print(f"✅ v11 公开化执行完成：{len(moves)} 段 / ¥{moved_total:,.2f} 入主池 free")
        return {"rowcount": len(moves), "moved_total": moved_total, "moves": moves}
    finally:
        conn.close()


def main(argv=None):
    from paper_trading_v2.config import get_workspace_config
    ap = argparse.ArgumentParser(
        prog='pool_publicize',
        description='v11 一次性迁移：非 NEWS open 段 cash→主池 free（幂等；默认预演）')
    ap.add_argument('--execute', action='store_true',
                    help='真实写入（缺省=预演零变化）')
    ap.add_argument('--date', default=None, help='迁移窗口标记（写进 audit reason 供对账）')
    ap.add_argument('--db', default=None, help='库路径（缺省=workspace master_pool.db）')
    args = ap.parse_args(argv)
    db = args.db or get_workspace_config()['db_path']
    r = publicize(db, execute=args.execute, date=args.date)
    return 0 if r.get('rowcount', 0) or args.execute else 0


if __name__ == '__main__':
    sys.exit(main())
