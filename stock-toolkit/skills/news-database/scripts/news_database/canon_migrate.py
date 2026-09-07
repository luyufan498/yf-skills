"""存量清洗：event_stock.stock_code / stocks.code 三态混存 → ptrade2 canonical 归一。

独立脚本（非 CLI 命令）：
    python3 news_database/canon_migrate.py --db <news.db路径> [--apply] [--report <输出文件>]

- 默认 dry-run：逐行分类输出报告，不改库
- --apply：UPDATE 归一 + 同 event 冲突归并（relevance 取 max）+ stocks 以 name 去重
  （保留一条 canonical，其余 DELETE）；**需人工裁决行不处理**，留在报告里
- 归一规则唯一逻辑源 = ptrade2 canon-code（子进程复用 cli._canon_code，本脚本不复制规则）
- --apply 前自动备份 <db>.bak-canon-YYYYMMDD-HHMM；幂等（二次 --apply 空转）

分类（_classify）：
- canonical  已是 canonical 形态（sh600176/hk00700/gb_aapl，regex fullmatch）
- autofix    确定性机械归一（600176.SH / SH603019）或 ptrade2 可解析（裸 6 位）
- human      需人工裁决：中文名当码 / 5 位等错码形态 / ptrade2 拒绝 / 归一后跨名撞码
"""

import sys
from pathlib import Path

if __package__ in (None, ""):  # 直接 python3 canon_migrate.py 运行时补包路径
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import re
import shutil
import sqlite3
from datetime import datetime

from news_database import cli as _cli_mod
from news_database.storage import CANONICAL_STOCK_CODE_RE

DOT_SUFFIX_RE = re.compile(r'^(\d{6})\.(SH|SZ)$')
HK_SUFFIX_RE = re.compile(r'^(\d{5})\.(HK)$')   # .HK 后缀确定性映射（5 位即港股标准位宽）
UPPER_PREFIX_RE = re.compile(r'^(SH|SZ)(\d{6})$')


def _classify(raw, cache=None):
    """raw → (cls, new_code)。cls: canonical / autofix / human；autofix 时 new_code 非 None。

    ptrade2 只在裸 6 位（sh/sz 交易所前缀需规则源裁决）时调用，cache 按原始码去重。
    """
    raw = (raw or "").strip()
    if CANONICAL_STOCK_CODE_RE.fullmatch(raw):
        return "canonical", raw
    m = DOT_SUFFIX_RE.match(raw)
    if m:
        return "autofix", m.group(2).lower() + m.group(1)
    m = HK_SUFFIX_RE.match(raw)
    if m:
        return "autofix", "hk" + m.group(1)
    m = UPPER_PREFIX_RE.match(raw)
    if m:
        return "autofix", m.group(1).lower() + m.group(2)
    # 其余形态（裸 6 位 A股、4-5 位港股、美股裸名、中文名…）一律交 ptrade2 canon-code 裁决：
    # 能解析 → autofix；拒绝/不可用 → 留人工（不清洗，报告复核）
    if cache is not None and raw in cache:
        return cache[raw]
    try:
        canon = _cli_mod._canon_code(raw)
        result = ("autofix", canon) if CANONICAL_STOCK_CODE_RE.fullmatch(canon) else ("human", None)
    except (ValueError, RuntimeError):
        result = ("human", None)   # ptrade2 拒绝/不可用 → 留人工，不清洗
    if cache is not None:
        cache[raw] = result
    return result


# ---------- 扫描 + 计划 ----------

def _plan_event_stock(conn, cache=None):
    """event_stock 全表分类 → [{event_id, raw, cls, new_code, relevance}]。"""
    out = []
    for r in conn.execute("SELECT event_id, stock_code, relevance FROM event_stock "
                          "ORDER BY event_id, stock_code"):
        raw = (r["stock_code"] or "").strip()
        cls, tgt = _classify(raw, cache)
        out.append({"event_id": r["event_id"], "raw": raw, "cls": cls,
                    "new_code": tgt, "relevance": r["relevance"]})
    return out


def _plan_stocks(conn, cache=None):
    """stocks 全表分类 + 以 name 归组 → [{rowid, raw, name, cls, new_code, action}]。

    action: keep / update / delete / human（跨名撞码也归 human，不动待裁决）。
    """
    items = []
    for r in conn.execute("SELECT rowid, code, name FROM stocks ORDER BY rowid"):
        raw = (r["code"] or "").strip()
        cls, tgt = _classify(raw, cache)
        items.append({"rowid": r["rowid"], "raw": raw, "name": r["name"],
                      "cls": cls, "new_code": tgt, "action": "human" if cls == "human" else None})
    # 跨名撞码检测：同一目标 code 被 >1 个不同 name 映射 → 全部留人工
    names_by_target = {}
    for it in items:
        if it["cls"] != "human":
            names_by_target.setdefault(it["new_code"], set()).add(it["name"])
    conflict_targets = {c for c, names in names_by_target.items() if len(names) > 1}
    for it in items:
        if it["cls"] != "human" and it["new_code"] in conflict_targets:
            it["cls"], it["new_code"], it["action"] = "human", None, "human"
    # 以 name 为准去重：同 name 保留一条（优先已 canonical），其余 DELETE（human 行不入组）
    by_name = {}
    for it in items:
        if it["action"] == "human":
            continue
        by_name.setdefault(it["name"], []).append(it)
    for group in by_name.values():
        keeper = next((g for g in group if g["raw"] == g["new_code"]), None) or group[0]
        for g in group:
            if g is keeper:
                g["action"] = "keep" if g["raw"] == g["new_code"] else "update"
            else:
                g["action"] = "delete"
    return items


# ---------- 执行 ----------

def _apply_event_stock(conn, plan):
    """UPDATE 归一 + 同 event 同目标归并（relevance 取 max）。返回 (updated, deleted)。"""
    groups = {}
    for row in plan:
        if row["cls"] != "human":
            groups.setdefault((row["event_id"], row["new_code"]), []).append(row)
    updated = deleted = 0
    for (eid, tgt), rows in groups.items():
        max_rel = max(int(r["relevance"] or 0) for r in rows)
        keeper = next((r for r in rows if r["raw"] == tgt), None)
        if keeper is None:   # 全是旧码 → 第一条 UPDATE 为 canonical，其余 DELETE
            first, rest = rows[0], rows[1:]
            updated += conn.execute(
                "UPDATE event_stock SET stock_code=?, relevance=? "
                "WHERE event_id=? AND stock_code=?",
                (tgt, max_rel, eid, first["raw"])).rowcount
            for r in rest:
                deleted += conn.execute(
                    "DELETE FROM event_stock WHERE event_id=? AND stock_code=?",
                    (eid, r["raw"])).rowcount
        else:                # 已有 canonical 行 → 只抬 relevance，其余 DELETE
            if int(keeper["relevance"] or 0) != max_rel:
                conn.execute("UPDATE event_stock SET relevance=? "
                             "WHERE event_id=? AND stock_code=?", (max_rel, eid, tgt))
                updated += 1
            for r in rows:
                if r["raw"] != tgt:
                    deleted += conn.execute(
                        "DELETE FROM event_stock WHERE event_id=? AND stock_code=?",
                        (eid, r["raw"])).rowcount
    conn.commit()
    return updated, deleted


def _apply_stocks(conn, plan):
    """以 name 去重：先 DELETE 后 UPDATE（避开 PRIMARY KEY 冲突）。返回 (updated, deleted)。"""
    updated = deleted = 0
    for it in plan:
        if it["action"] == "delete":
            deleted += conn.execute("DELETE FROM stocks WHERE rowid=?", (it["rowid"],)).rowcount
    for it in plan:
        if it["action"] == "update":
            updated += conn.execute("UPDATE stocks SET code=? WHERE rowid=?",
                                    (it["new_code"], it["rowid"])).rowcount
    conn.commit()
    return updated, deleted


# ---------- 报告 + 残留统计 ----------

def _report_lines(event_plan, stocks_plan, applied=False):
    """逐行分类报告（stdout 与 --report 文件共用）。"""
    lines = [f"=== canon_migrate {'已执行' if applied else 'dry-run 预览（未改库）'} ==="]
    for label, plan, kind in (("event_stock", event_plan, "event"), ("stocks", stocks_plan, "stock")):
        humans = [it for it in plan if it["cls"] == "human"]
        fixed = [it for it in plan if it["cls"] == "autofix"]
        lines.append(f"[{label}] 共 {len(plan)} 行 | canonical {len(plan) - len(humans) - len(fixed)}"
                     f" | 可自动归一 {len(fixed)} | 需人工裁决 {len(humans)}")
        for it in fixed:
            if kind == "event":
                lines.append(f"  归一 event_id={it['event_id']} {it['raw']} → {it['new_code']} "
                             f"(rel={it['relevance']})")
            else:
                lines.append(f"  归一 {it['raw']} (name={it['name']}) → {it['new_code']} "
                             f"[{it['action']}]")
        for it in humans:
            if kind == "event":
                lines.append(f"  ⚠ 需人工裁决 event_id={it['event_id']} {it['raw']} "
                             f"(rel={it['relevance']})——未处理")
            else:
                lines.append(f"  ⚠ 需人工裁决 {it['raw']} (name={it['name']})——未处理")
    return lines


def _residual(conn):
    """清洗后残留格式统计：非 canonical 行数（应全部是需人工未处理行）。"""
    es_bad = [r["stock_code"] for r in conn.execute("SELECT stock_code FROM event_stock")
              if not CANONICAL_STOCK_CODE_RE.fullmatch(r["stock_code"] or "")]
    st_bad = [r["code"] for r in conn.execute("SELECT code FROM stocks")
              if not CANONICAL_STOCK_CODE_RE.fullmatch(r["code"] or "")]
    return es_bad, st_bad


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="存量清洗：event_stock/stocks 代码归一为 ptrade2 canonical（默认 dry-run）")
    parser.add_argument("--db", required=True, help="news.db 路径（一律用副本，勿指生产）")
    parser.add_argument("--apply", action="store_true", help="执行归一（缺省 dry-run 不改库）")
    parser.add_argument("--report", help="报告输出文件（缺省仅 stdout）")
    args = parser.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"❌ 数据库不存在: {db}")
        return 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cache = {}
    event_plan = _plan_event_stock(conn, cache)
    stocks_plan = _plan_stocks(conn, cache)

    lines = []
    backup = None
    if args.apply:
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        backup = Path(f"{db}.bak-canon-{ts}")
        shutil.copy2(db, backup)
        es_updated, es_deleted = _apply_event_stock(conn, event_plan)
        st_updated, st_deleted = _apply_stocks(conn, stocks_plan)
        lines = _report_lines(event_plan, stocks_plan, applied=True)
        es_bad, st_bad = _residual(conn)
        lines.append(f"[--apply 结果] event_stock: 归一/抬升 {es_updated} 行, 归并删除 {es_deleted} 行"
                     f" | stocks: 归一 {st_updated} 行, 去重删除 {st_deleted} 行")
        lines.append(f"[备份] {backup}")
        lines.append(f"[残留统计] event_stock 非 canonical {len(es_bad)} 行（应为全部需人工未处理行）"
                     f" | stocks 非 canonical {len(st_bad)} 行")
        if es_bad:
            lines.append(f"  event_stock 残留: {es_bad}")
        if st_bad:
            lines.append(f"  stocks 残留: {st_bad}")
    else:
        lines = _report_lines(event_plan, stocks_plan, applied=False)
        lines.append("[提示] 加 --apply 执行归一（执行前自动备份）；需人工裁决行不会动，留报告复核")

    conn.close()
    text = "\n".join(lines)
    print(text)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
        print(f"✓ 报告已写入 {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
