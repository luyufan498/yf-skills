"""任务书 B（2026-09-10）四项测试：B1 watch_points 表化+迁移+双写+kv 原子读改写、
B2 失败码通道（fail --code/--ref/--result → payload.exec）、B3 task_events.creator 列、
B4 迁移幂等 + 对账。全部 /tmp 隔离库（STOCK_TASKS_DB 指 tmp_path），零生产库接触。

跑法（隔离，零生产库接触）：
    cd stock-toolkit/skills/task-bus/scripts && \
    <paper-trading venv>/bin/python3 -m pytest tests/test_taskbook_b.py -q

验收映射（任务书 B）：
- B1：迁移导入+存量回填 created_by、wp_id 稳定/碰撞、kv_update 原子读改写、
      CLI add 双写（表+kv 旧形状）、remove 软删+kv 删除、list 显示 wp_id/created_by/status
- B2：db.fail_event / CLI fail --code/--ref/--result 落 payload.exec={result,code,ref,at}
- B3：task_events.creator 列存在（含旧库自动补列）且可写
- B4：迁移连跑两次行数不变、reconcile 对账表↔kv
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import task_bus.db as tdb


def setup_db(tmp) -> str:
    """隔离库：STOCK_TASKS_DB 指 tmp 下 tasks.db（零生产库接触）。"""
    db = os.path.join(str(tmp), "tasks.db")
    os.environ["STOCK_TASKS_DB"] = db
    tdb.ENV = "STOCK_TASKS_DB"
    return db


def wp_rows(db) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM watch_points ORDER BY wp_id")]
    finally:
        conn.close()


def runner():
    from typer.testing import CliRunner
    from task_bus.cli import app
    return CliRunner(), app


# ---------- B1：迁移导入 + 存量回填 ----------

def test_b1_migration_imports_and_backfills(tmp_path):
    db = setup_db(tmp_path)
    tdb.kv_set("watch_points", {
        "工业富联": [{"code": "601138", "price": 55.0, "note": "回踩企稳", "mode": "eval",
                      "amount": None, "min": None, "added_at": "08-24 11:09"}],
        "赛力斯": [{"code": "sh601127", "price": 24.5, "note": "建仓10%", "mode": "buy",
                    "amount": 200000.0, "min": 23.0, "added_at": "08-25 10:00"}],
    })
    n = tdb.migrate_watch_points()
    assert n == 2, f"应导入 2 行，实际 {n}"
    rows = wp_rows(db)
    assert len(rows) == 2
    by = {r["entity"]: r for r in rows}
    assert by["工业富联"]["wp_id"].startswith("wp:工业富联:")
    assert by["工业富联"]["created_by"] == "analysis-watch"  # 方案 v3 §1.1 存量回填
    assert by["工业富联"]["status"] == "active"
    assert by["赛力斯"]["min"] == 23.0 and by["赛力斯"]["mode"] == "buy"
    assert by["赛力斯"]["amount"] == 200000.0
    assert tdb.kv_get("watch_points_migrated_at")  # 迁移标记已写


# ---------- B1/B4：迁移幂等（连跑两次行数不变）+ kv-only 增量补迁 ----------

def test_b4_migration_idempotent_rerun(tmp_path):
    db = setup_db(tmp_path)
    p1 = {"code": "sh600000", "price": 10.0, "note": "", "mode": "eval",
          "amount": None, "min": None, "added_at": "08-24 11:09"}
    tdb.kv_set("watch_points", {"A": [p1]})
    assert tdb.migrate_watch_points() == 1
    assert tdb.migrate_watch_points() == 0, "重入应按内容去重 0 导入"
    assert tdb.migrate_watch_points() == 0
    assert len(wp_rows(db)) == 1, "连跑三次行数必须不变"
    # kv 新增点（模拟 kv 直写方如 master_pool）→ 再跑迁移只导入新点
    p2 = {"code": "sh600001", "price": 20.0, "note": "", "mode": "buy",
          "amount": 1000.0, "min": None, "added_at": "08-25 09:00"}
    tdb.kv_set("watch_points", {"A": [p1, p2]})
    assert tdb.migrate_watch_points() == 1
    assert tdb.migrate_watch_points() == 0
    assert len(wp_rows(db)) == 2


# ---------- B1：wp_id 稳定 + 同秒碰撞 ----------

def test_b1_wp_id_stable_and_collision(tmp_path):
    ids = []
    for i in range(2):
        d = os.path.join(str(tmp_path), f"stable{i}")
        db = setup_db(d)
        tdb.kv_set("watch_points", {"X": [{"code": None, "price": 5.0, "note": "", "mode": "eval",
                                           "amount": None, "min": None, "added_at": "08-24 11:09"}]})
        tdb.migrate_watch_points()
        ids.append(wp_rows(db)[0]["wp_id"])
    assert ids[0] == ids[1], "同输入 wp_id 必须稳定（跨库一致）"
    assert ids[0].startswith("wp:X:")
    # 同实体同 added_at 两个不同价 → 都入表且 wp_id 不冲突（碰撞后缀）
    db2 = setup_db(os.path.join(str(tmp_path), "collide"))
    tdb.kv_set("watch_points", {"Y": [
        {"code": None, "price": 5.0, "note": "", "mode": "eval", "amount": None,
         "min": None, "added_at": "08-24 11:09"},
        {"code": None, "price": 6.0, "note": "", "mode": "eval", "amount": None,
         "min": None, "added_at": "08-24 11:09"}]})
    tdb.migrate_watch_points()
    rows = wp_rows(db2)
    assert len(rows) == 2 and len({r["wp_id"] for r in rows}) == 2, "同秒双点 wp_id 不得冲突"


# ---------- B1：kv_update 原子读改写入口 ----------

def test_b1_kv_update_atomic_entry(tmp_path):
    setup_db(tmp_path)
    out = tdb.kv_update("watch_points", lambda p: {**p, "A": [{"price": 1.0}]})
    assert out["A"][0]["price"] == 1.0
    assert tdb.kv_get("watch_points")["A"][0]["price"] == 1.0
    # 叠加读改写（非覆盖）
    tdb.kv_update("watch_points", lambda p: {**p, "B": [{"price": 2.0}]})
    assert set(tdb.kv_get("watch_points").keys()) == {"A", "B"}
    # 非 dict 旧值 → 按 {} 处理不崩
    tdb.kv_set("weird", "plain-string")
    assert tdb.kv_update("weird", lambda p: {**p, "k": 1}) == {"k": 1}


# ---------- B1：CLI add 双写（表 + kv 旧形状）+ list 三字段 ----------

def test_b1_cli_add_dual_write_and_list(tmp_path):
    db = setup_db(tmp_path)
    runner_, app = runner()
    r = runner_.invoke(app, ["watchpoint", "add", "测试股", "--price", "12.0",
                             "--mode", "buy", "--amount", "200000", "--code", "sh600000",
                             "--note", "建仓10%", "--creator", "analysis-watch"])
    assert r.exit_code == 0, r.output
    assert "wp=wp:测试股:" in r.output, r.output
    # kv 双写保持旧形状（watch_scan/master_pool 兼容）
    kv = tdb.kv_get("watch_points")
    assert kv["测试股"][0]["price"] == 12.0 and kv["测试股"][0]["mode"] == "buy"
    assert set(kv["测试股"][0].keys()) == {"code", "price", "note", "mode", "amount", "min", "added_at"}
    # 表行 active + created_by
    rows = wp_rows(db)
    assert len(rows) == 1 and rows[0]["status"] == "active"
    assert rows[0]["created_by"] == "analysis-watch"
    assert rows[0]["wp_id"] in r.output
    # list 显示 wp_id / created_by / status
    r2 = runner_.invoke(app, ["watchpoint", "list"])
    assert r2.exit_code == 0, r2.output
    assert "wp=wp:测试股:" in r2.output and "by=analysis-watch" in r2.output and "st=active" in r2.output


# ---------- B1：CLI remove 软删 + kv 删除 ----------

def test_b1_cli_remove_soft_delete(tmp_path):
    db = setup_db(tmp_path)
    runner_, app = runner()
    runner_.invoke(app, ["watchpoint", "add", "测试股", "--price", "12.0",
                         "--code", "sh600000", "--note", "n"])
    r = runner_.invoke(app, ["watchpoint", "remove", "测试股"])
    assert r.exit_code == 0, r.output
    assert "测试股" not in (tdb.kv_get("watch_points") or {}), "kv 实体应已删除"
    rows = wp_rows(db)
    assert rows, "表行必须保留（软删非物理删）"
    assert all(x["status"] == "removed" for x in rows), rows
    r2 = runner_.invoke(app, ["watchpoint", "list"])
    assert "测试股" not in r2.output, "默认 list 只列 active"
    r3 = runner_.invoke(app, ["watchpoint", "list", "--all"])
    assert "测试股" in r3.output and "st=removed" in r3.output


# ---------- B2：db.fail_event 失败码通道 ----------

def test_b2_fail_code_ref_result(tmp_path):
    setup_db(tmp_path)
    eid = tdb.add("DEEP_DIVE", "ND#B2", source="test")
    assert tdb.claim(eid)
    assert tdb.fail_event(eid, note="无仓位", code="no_position",
                          ref="watchpoint:wp:X:123", result="cancelled")
    row = [e for e in tdb.list_events(limit=10) if e["id"] == eid][0]
    assert row["status"] == "failed", "cancelled 行状态归 failed（状态机白名单），语义由 exec.result 携带"
    ex = json.loads(row["payload"])["exec"]
    assert ex["result"] == "cancelled" and ex["code"] == "no_position"
    assert ex["ref"] == "watchpoint:wp:X:123" and ex["at"]
    # --result done：already_fulfilled 语义归档（行状态恒归 failed——fail 命令不改状态机，
    # 处置语义由 exec.result 携带，消费方按 payload 路由）
    eid2 = tdb.add("DEEP_DIVE", "ND#B2b")
    tdb.claim(eid2)
    assert tdb.fail_event(eid2, code="already_fulfilled", result="done")
    row2 = [e for e in tdb.list_events(limit=10) if e["id"] == eid2][0]
    assert row2["status"] == "failed"
    assert json.loads(row2["payload"])["exec"]["result"] == "done"
    # --ref JSON → 解析为对象落库
    eid3 = tdb.add("DEEP_DIVE", "ND#B2c")
    tdb.claim(eid3)
    tdb.fail_event(eid3, code="gate_reject", ref='{"kind":"condition","id":44}')
    row3 = [e for e in tdb.list_events(limit=10) if e["id"] == eid3][0]
    assert json.loads(row3["payload"])["exec"]["ref"] == {"kind": "condition", "id": 44}
    # 非法 result → ValueError
    eid4 = tdb.add("DEEP_DIVE", "ND#B2d")
    tdb.claim(eid4)
    try:
        tdb.fail_event(eid4, result="bogus")
        raise AssertionError("非法 result 应 ValueError")
    except ValueError:
        pass


# ---------- B2：CLI fail 冒烟（含 --note 兼容 + 默认 failed） ----------

def test_b2_cli_fail_smoke(tmp_path):
    setup_db(tmp_path)
    runner_, app = runner()
    r0 = runner_.invoke(app, ["add", "DEEP_DIVE", "ND#CLI", "--source", "test"])
    assert r0.exit_code == 0, r0.output
    tid = tdb.list_events(limit=1)[0]["id"]
    assert runner_.invoke(app, ["claim", str(tid)]).exit_code == 0
    r = runner_.invoke(app, ["fail", str(tid), "--note", "跳空脱靶", "--code", "price_drifted",
                             "--ref", "watchpoint:wp:Y:1", "--result", "cancelled"])
    assert r.exit_code == 0, r.output
    row = [e for e in tdb.list_events(limit=10) if e["id"] == tid][0]
    ex = json.loads(row["payload"])["exec"]
    assert ex == {**ex, "result": "cancelled", "code": "price_drifted", "ref": "watchpoint:wp:Y:1"}
    assert ex["at"]
    # 默认 result=failed，--note 兼容（旧用法不受影响）
    runner_.invoke(app, ["add", "DEEP_DIVE", "ND#CLI2", "--source", "test"])
    tid2 = tdb.list_events(limit=1)[0]["id"]
    runner_.invoke(app, ["claim", str(tid2)])
    r2 = runner_.invoke(app, ["fail", str(tid2), "--note", "旧用法失败"])
    assert r2.exit_code == 0 and "已标记失败" in r2.output, r2.output
    row2 = [e for e in tdb.list_events(limit=10) if e["id"] == tid2][0]
    assert row2["status"] == "failed"
    assert json.loads(row2["payload"])["exec"]["result"] == "failed"


# ---------- B3：creator 列（存在、可写、旧库自动补列） ----------

def test_b3_creator_column(tmp_path):
    db = setup_db(tmp_path)
    tdb.add("DEEP_DIVE", "ND#warm")  # connect 触发建列
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    conn.close()
    assert "creator" in cols, "task_events.creator 列必须存在"
    eid = tdb.add("DEEP_DIVE", "ND#B3", source="x-scan", creator="msg-watch")
    row = [e for e in tdb.list_events(limit=10) if e["id"] == eid][0]
    assert row["creator"] == "msg-watch"
    eid2 = tdb.add("DEEP_DIVE", "ND#B3b")
    row2 = [e for e in tdb.list_events(limit=10) if e["id"] == eid2][0]
    assert row2["creator"] == ""
    # 旧库（无 creator 列）连库自动补列（幂等迁移，B4）
    old = os.path.join(str(tmp_path), "old.db")
    c = sqlite3.connect(old)
    c.executescript(
        "CREATE TABLE IF NOT EXISTS task_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 3,"
        " source TEXT, entity TEXT, payload TEXT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), claimed_at TEXT,"
        " done_at TEXT, note TEXT);"
        "CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT,"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));")
    c.commit()
    c.close()
    os.environ["STOCK_TASKS_DB"] = old
    try:
        eid3 = tdb.add("DEEP_DIVE", "ND#B3old", creator="check-open")
        assert eid3 > 0
        conn = sqlite3.connect(old)
        conn.row_factory = sqlite3.Row
        r3 = conn.execute("SELECT creator FROM task_events WHERE id=?", (eid3,)).fetchone()
        conn.close()
        assert r3["creator"] == "check-open"
    finally:
        setup_db(tmp_path)  # 还原隔离库指向


# ---------- B4：reconcile 对账（表 ↔ kv 一致性） ----------

def test_b4_reconcile(tmp_path):
    db = setup_db(tmp_path)
    runner_, app = runner()
    runner_.invoke(app, ["watchpoint", "add", "对账股", "--price", "9.0", "--code", "sh600000"])
    runner_.invoke(app, ["watchpoint", "add", "对账股", "--price", "11.0", "--mode", "sell",
                         "--code", "sh600000"])
    r = runner_.invoke(app, ["watchpoint", "reconcile"])
    assert r.exit_code == 0, r.output
    assert "一致" in r.output, r.output
    # 制造漂移：kv 被外部直删 → 表有 kv 无 → reconcile 报不一致（exit 1）
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM kv_store WHERE key='watch_points'")
    conn.commit()
    conn.close()
    r2 = runner_.invoke(app, ["watchpoint", "reconcile"])
    assert r2.exit_code == 1, r2.output
    assert "仅表" in r2.output, r2.output


# ---------- 审计补丁（2026-09-10 主代理 R1.5 抓到）：迁移必须有生产触发点 ----------
# 缺陷：migrate_watch_points() 只有单测调用，生产路径（CLI list/add、init）都不触发
# → 存量 48 点永不进表；C 批切读表后全部挂点静默失效。
# 修复：wp_list() 在标记缺失时自动补迁一次（幂等），并补 CLI `watchpoint migrate`。

def test_audit_wp_list_autotriggers_migration(tmp_path):
    db = setup_db(tmp_path)
    tdb.kv_set("watch_points", {
        "工业富联": [{"code": "601138", "price": 55.0, "note": "x", "mode": "eval",
                      "amount": None, "min": None, "added_at": "08-24 11:09"}],
    })
    assert wp_rows(db) == [], "前置：表应为空"
    rows = tdb.wp_list()                       # 纯读路径
    assert len(rows) == 1, "wp_list 应自动触发存量迁移（否则迁移函数是死代码）"
    assert tdb.kv_get("watch_points_migrated_at")
    assert len(tdb.wp_list()) == 1, "再次调用不得重复导入"


def test_audit_cli_migrate_action(tmp_path):
    db = setup_db(tmp_path)
    tdb.kv_set("watch_points", {
        "赛力斯": [{"code": "sh601127", "price": 24.5, "note": "y", "mode": "buy",
                    "amount": 200000.0, "min": 23.0, "added_at": "08-25 10:00"}],
    })
    cli, app = runner()
    r = cli.invoke(app, ["watchpoint", "migrate", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "1" in r.output and wp_rows(db) == [], "dry-run 不得写入"
    r2 = cli.invoke(app, ["watchpoint", "migrate"])
    assert r2.exit_code == 0, r2.output
    assert len(wp_rows(db)) == 1, "apply 应导入"
    r3 = cli.invoke(app, ["watchpoint", "migrate"])
    assert r3.exit_code == 0
    assert len(wp_rows(db)) == 1, "重复 migrate 幂等"


if __name__ == "__main__":
    import shutil
    import tempfile

    failures = 0
    for name, fn in sorted((n, f) for n, f in list(globals().items())
                           if n.startswith("test_") and callable(f)):
        d = tempfile.mkdtemp(prefix="taskbook_b_")
        try:
            fn(d)
            print(f"✅ {name}")
        except Exception as e:
            failures += 1
            print(f"❌ {name}: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    if failures:
        raise SystemExit(f"{failures} 个用例失败")
    print("ALL PASS")
