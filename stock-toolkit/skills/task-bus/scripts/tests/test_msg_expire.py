"""MSG_EXPIRE 论点失效清退令事件准入测试（2026-09-09，新事件类型准入四件套）。

跑法（隔离，零生产库接触——STOCK_TASKS_DB 指 /tmp 临时库）：
    cd stock-toolkit/skills/task-bus/scripts && python3 -m pytest tests/test_msg_expire.py -v
或无 pytest 时：python3 tests/test_msg_expire.py（内建 main 直跑）。

四件套断言：
1. db.add("MSG_EXPIRE") 成功入队；未登记类型（MSG_EXPrie 拼错）→ ValueError
2. claim 硬门：无 consumer / consumer=morning-audit → PermissionError；
   consumer=msg-watch → 成功且 payload.claimed_by=msg-watch
3. news_pending_lines()：pending MSG_EXPIRE → 返回行含该事件与
   "claim --consumer msg-watch" 提示（C2 monitor 持久可见，防积压死锁）
4. check_tasks()：pending MSG_EXPIRE 不出现在 legacy 心跳返回清单
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watch_scan
import task_bus.db as tdb


class T:
    """临时任务库（STOCK_TASKS_DB 隔离），每用例重建。"""

    def __init__(self, tmp):
        self.db = os.path.join(str(tmp), "tasks.db")
        os.makedirs(str(tmp), exist_ok=True)
        watch_scan.TASKS_DB = self.db           # watch_scan 读模块级 TASKS_DB
        watch_scan._ensure_task_table()
        tdb.ENV = "STOCK_TASKS_DB"
        os.environ["STOCK_TASKS_DB"] = self.db  # task_bus.db 走 env 优先

    def raw_add(self, type_, entity="ND#9001", status="pending", payload=None) -> int:
        """绕过 add 白名单直插（隔离 claim/可见性断言与入队白名单）。"""
        conn = sqlite3.connect(self.db)
        try:
            cur = conn.execute(
                "INSERT INTO task_events (type, entity, status, priority, source, payload) "
                "VALUES (?,?,?,?,?,?)",
                (type_, entity, status, 1, "evening-audit",
                 json.dumps(payload, ensure_ascii=False) if payload else None))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def rows(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM task_events ORDER BY id")]
        finally:
            conn.close()
        return []


# ---------- 1. 入队白名单 ----------

def test_add_msg_expire_ok_and_typo_rejected(tmp_path):
    t = T(tmp_path)
    eid = tdb.add("MSG_EXPIRE", "ND#9001", source="evening-audit", priority=1,
                  payload={"event_key": "ND#9001", "stock": "测试股", "code": "sh600000"})
    row = [r for r in t.rows() if r["id"] == eid][0]
    assert row["type"] == "MSG_EXPIRE" and row["status"] == "pending", row
    # 未登记类型（拼错）→ ValueError
    try:
        tdb.add("MSG_EXPrie", "ND#9002")
        raise AssertionError("MSG_EXPrie 拼错类型应 ValueError")
    except ValueError:
        pass


# ---------- 2. claim 硬门（唯一消费者=msg-watch） ----------

def test_claim_gate_requires_msg_watch(tmp_path):
    t = T(tmp_path)
    eid = t.raw_add("MSG_EXPIRE", "ND#9001")
    # 无 consumer（缺省）→ PermissionError（fail-closed）
    try:
        tdb.claim(eid)
        raise AssertionError("无 consumer 应 PermissionError")
    except PermissionError:
        pass
    # consumer=morning-audit（存量晨审心跳）→ PermissionError
    try:
        tdb.claim(eid, consumer="morning-audit")
        raise AssertionError("consumer=morning-audit 应 PermissionError")
    except PermissionError:
        pass
    # consumer=msg-watch（唯一消费者）→ 成功且 payload.claimed_by=msg-watch
    row = tdb.claim(eid, consumer="msg-watch")
    assert row is not None and row["status"] == "processing", row
    p = json.loads(row["payload"])
    assert p.get("claimed_by") == "msg-watch", p


# ---------- 3. news scope 唤醒层可见性（防积压死锁件） ----------

def test_news_pending_lines_includes_msg_expire(tmp_path):
    t = T(tmp_path)
    eid = t.raw_add("MSG_EXPIRE", "ND#9001", status="pending")
    lines = watch_scan.news_pending_lines()
    joined = "\n".join(lines)
    assert any(f"#{eid}" in ln and "[MSG_EXPIRE]" in ln for ln in lines), joined
    assert f"claim {eid} --consumer msg-watch" in joined, joined


# ---------- 4. legacy 心跳隔离（check_tasks 不可见） ----------

def test_check_tasks_excludes_msg_expire(tmp_path):
    t = T(tmp_path)
    expire_id = t.raw_add("MSG_EXPIRE", "ND#9001", status="pending")
    control_id = t.raw_add("SLEEVE_FILL", "ND#9002", status="pending")  # 对照组：legacy 应可见
    rows = watch_scan.check_tasks()
    ids = {r["id"] for r in rows}
    types = {r["type"] for r in rows}
    assert control_id in ids, f"对照组 SLEEVE_FILL 应出现在 legacy 清单: {rows}"
    assert expire_id not in ids and "MSG_EXPIRE" not in types, \
        f"MSG_EXPIRE 不应出现在 legacy 清单: {rows}"


if __name__ == "__main__":
    import shutil
    import tempfile

    failures = 0
    for name, fn in sorted((n, f) for n, f in list(globals().items())
                           if n.startswith("test_") and callable(f)):
        d = tempfile.mkdtemp(prefix="msg_expire_")
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
