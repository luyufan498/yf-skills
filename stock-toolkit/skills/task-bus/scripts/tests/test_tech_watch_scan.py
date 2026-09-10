"""任务书 D（2026-09-10）WP8 测试：tech_watch_scan.py monitor 脚本。

跑法（隔离，零生产库接触——/tmp 隔离库 + 全函数级打桩）：
    cd stock-toolkit/skills/task-bus/scripts && \\
    <paper-trading venv>/bin/python3 -m pytest tests/test_tech_watch_scan.py -q

验收映射（任务书 D 验收节）：
- ① 有未处置失败 → [EXEC-FAIL] 行含 id/creator/code/ref；
- ② 已 handled_at → 不出；
- ③ creator='msg-watch'/'atr-auto' → 不出（词表天然排除）；
- ④ 未知 creator（空/词表外）→ [CREATOR-UNKNOWN]（fail-closed 不丢弃）；
- ⑤ 非交易日 → 静默（IDLE）；
- ⑥ 首拍窗口（≥09:40）→ [OPEN-REVIEW] 且非首拍（<09:40）不输出；
- ⑦ 缺 handled_at 列 → 降级 [WARN] 不崩。
- 附：exec.result='deferred_agent' → 不出（C 批语义：数量非清仓已退普通 agent 路径）；
  异常 → 告警行不静默 IDLE；输出字节稳定（IDLE 零唤醒前提）。
"""
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime
from unittest.mock import patch

# 直接从 ~/.hermes/scripts 装载被测脚本（monitor 是单文件、无包依赖）
_SCRIPT = "/home/catmouse/.hermes/scripts/tech_watch_scan.py"
spec = importlib.util.spec_from_file_location("tech_watch_scan", _SCRIPT)
tws = importlib.util.module_from_spec(spec)
sys.modules["tech_watch_scan"] = tws
spec.loader.exec_module(tws)


# ---------- 隔离环境（/tmp 临时库） ----------

class T:
    """隔离环境：/tmp tasks.db + /tmp pool.db，tws 模块级路径重定向。"""

    def __init__(self, tmp):
        self.tmp = str(tmp)
        self.tasks_db = os.path.join(self.tmp, "tasks.db")
        self.pool_db = os.path.join(self.tmp, "master_pool.db")
        os.makedirs(self.tmp, exist_ok=True)
        conn = sqlite3.connect(self.tasks_db)
        conn.execute(TASKS_DDL)
        conn.commit()
        conn.close()
        self._mk_pool()
        tws.TASKS_DB = self.tasks_db
        tws.POOL_DB = self.pool_db
        tws._HAS_JSON1 = None  # 每用例重置惰性探测

    def _mk_pool(self):
        conn = sqlite3.connect(self.pool_db)
        conn.execute("CREATE TABLE position (id INTEGER PRIMARY KEY, stock TEXT, "
                     "code TEXT, strategy TEXT, status TEXT)")
        conn.execute("CREATE TABLE event_slots (event_key TEXT PRIMARY KEY, status TEXT)")
        conn.execute("CREATE TABLE event_slot_members (event_key TEXT, stock TEXT)")
        conn.execute("INSERT INTO position (stock, code, strategy, status) "
                     "VALUES ('测试股', 'sh600000', 'L1', 'open')")
        conn.commit()
        conn.close()

    def add_fail(self, creator: str, result: str = "failed", code: str = "insufficient_funds",
                 ref: str | dict = "watchpoint:wp:测试股:1", note: str = "资金不足拒单",
                 handled_at: str | None = None, eid: int | None = None) -> int:
        payload = {"exec": {"result": result, "code": code, "ref": ref,
                            "at": "2026-09-10T09:40:00", "note": note}}
        # 列存在性动态探测（⑦ 缺 handled_at 列用例：INSERT 不引用不存在的列）
        conn = sqlite3.connect(self.tasks_db)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
            fields = ["type", "status", "payload", "creator"]
            vals: list = ["WATCH_ALERT", "failed",
                          json.dumps(payload, ensure_ascii=False), creator]
            if eid is not None:
                fields.insert(0, "id")
                vals.insert(0, eid)
            if "handled_at" in cols:
                fields.append("handled_at")
                vals.append(handled_at)
            ph = ",".join("?" for _ in fields)
            cur = conn.execute(
                f"INSERT INTO task_events ({','.join(fields)}) VALUES ({ph})", vals)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def add_plain(self, creator: str = "", handled_at: str | None = None) -> int:
        conn = sqlite3.connect(self.tasks_db)
        try:
            cur = conn.execute(
                "INSERT INTO task_events (type, status, payload, creator, handled_at) "
                "VALUES ('WATCH_ALERT','failed',NULL,?,?)", (creator, handled_at))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def add_slot(self, event_key: str = "ND#1", status: str = "pending_order",
                 stock: str = "长飞光纤"):
        conn = sqlite3.connect(self.pool_db)
        try:
            conn.execute("INSERT INTO event_slots (event_key, status) VALUES (?,?)",
                         (event_key, status))
            conn.execute("INSERT INTO event_slot_members (event_key, stock) VALUES (?,?)",
                         (event_key, stock))
            conn.commit()
        finally:
            conn.close()


TASKS_DDL = """CREATE TABLE task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 3,
    source TEXT, entity TEXT, payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    claimed_at TEXT, done_at TEXT, note TEXT,
    creator TEXT NOT NULL DEFAULT '',
    handled_at TEXT, handled_by TEXT)"""


# 隔离库 DDL（与生产 task_events 同构——含 handled_at/handled_by 列；测试自建表）
TASKS_DDL = """CREATE TABLE task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 3,
    source TEXT, entity TEXT, payload TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    claimed_at TEXT, done_at TEXT, note TEXT,
    creator TEXT NOT NULL DEFAULT '',
    handled_at TEXT, handled_by TEXT)"""


def _force_trading_day(mon: bool = True):
    """打桩交易日历：dict 真源（避免碰真 JSON；bool 返回值语义一致）。"""
    return {"markets": {"CN_A_SHARE": {"years": {"2026": [int(d) for d in
             (["20260910", "20260911"] if mon else [])]}}}}


class FakeDT(datetime):
    """可替换的 now（首拍 09:41 / 交易日 2026-09-10）。"""

    @classmethod
    def now(cls) -> "FakeDT":
        return cls(2026, 9, 10, 9, 41, 0)


class FakeDTEarly(datetime):
    """非首拍（09:17 < 09:40）。"""

    @classmethod
    def now(cls) -> "FakeDTEarly":
        return cls(2026, 9, 10, 9, 17, 0)


class FakeDTWeekend(datetime):
    """非交易日（周六 2026-09-12）。"""

    @classmethod
    def now(cls) -> "FakeDTWeekend":
        return cls(2026, 9, 12, 9, 41, 0)


# ---------- 验收用例 ----------

def test_1_execfail_line_contains_id_creator_code_ref(tmp_path):
    """① 有未处置失败 → [EXEC-FAIL] 行含 id/creator/code/ref。"""
    t = T(tmp_path)
    eid = t.add_fail(creator="analysis-watch", code="insufficient_funds",
                     ref="watchpoint:wp:测试股:1")
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    line = [ln for ln in out.splitlines() if ln.startswith("[EXEC-FAIL]")]
    assert line, f"应有 [EXEC-FAIL] 行，实际输出：\n{out}"
    assert f"#{eid}" in line[0]
    assert "creator=analysis-watch" in line[0]
    assert "code=insufficient_funds" in line[0]
    assert "ref=watchpoint:wp:测试股:1" in line[0]


def test_2_handled_at_suppressed(tmp_path):
    """② 已 handled_at（消费方已处置）→ 不出 [EXEC-FAIL]。"""
    t = T(tmp_path)
    t.add_fail(creator="analysis-watch", handled_at="2026-09-10T10:00:00")
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    assert "[EXEC-FAIL]" not in out, f"已处置事件不应出现：\n{out}"


def test_3_msg_watch_and_atr_auto_excluded(tmp_path):
    """③ creator='msg-watch'/'atr-auto' → 不出（词表天然排除）。"""
    t = T(tmp_path)
    t.add_fail(creator="msg-watch", code="gate_reject", ref="slot:ND#1")
    t.add_fail(creator="atr-auto", code="protection_hit", ref="condition:44")
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    assert "[EXEC-FAIL]" not in out, f"词表外 creator 不应出现：\n{out}"
    assert "[CREATOR-UNKNOWN]" not in out  # 词表外归各自链路，不是 fail-closed 对象


def test_4_unknown_creator_fail_closed(tmp_path):
    """④ 未知 creator（空 / 词表外但非 msg-watch/atr-auto 语义）→ [CREATOR-UNKNOWN]。"""
    t = T(tmp_path)
    eid_empty = t.add_fail(creator="")
    eid_new = t.add_fail(creator="new-agent-x", code="error", ref="slot:ND#2")
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    assert "[CREATOR-UNKNOWN]" in out, f"未知 creator 应 fail-closed：\n{out}"
    assert f"#{eid_empty}" in out and "(空)" in out
    assert f"#{eid_new}" in out and "new-agent-x" in out
    # fail-closed = 绝不静默丢弃：行里有 id/creator 可回查
    assert "code=error" in out


def test_5_non_trading_day_silent(tmp_path):
    """⑤ 非交易日 → 静默（IDLE），有失败事件也不出。"""
    t = T(tmp_path)
    t.add_fail(creator="analysis-watch")
    t.add_slot("ND#9")
    with patch.object(tws, "datetime", FakeDTWeekend), \
         patch.object(tws, "is_trading_day", return_value=False):
        out = tws.build_output()
    assert out == "IDLE", f"非交易日应静默 IDLE，实际：\n{out}"


def test_6_first_shoot_window(tmp_path):
    """⑥ 首拍窗口（≥09:40）→ [OPEN-REVIEW] 带日期；非首拍（<09:40）不输出。"""
    t = T(tmp_path)
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    assert "[OPEN-REVIEW] 2026-09-10" in out, f"首拍应出 OPEN-REVIEW：\n{out}"
    # 紧凑上下文：未处置失败数 / 持仓只数 / 今日 pending_order 槽数（三个数，无槽明细）
    assert "未处置失败=0" in out and "持仓只数=1" in out and "今日pending_order槽数=0" in out
    assert "event_key" not in out.replace("今日pending_order槽数", "")  # 不点名槽（归 C1 消费）
    # 非首拍（09:17 < 09:40）→ 不输出
    with patch.object(tws, "datetime", FakeDTEarly), \
         patch.object(tws, "is_trading_day", return_value=True):
        out_early = tws.build_output()
    assert "[OPEN-REVIEW]" not in out_early, f"非首拍不应出 OPEN-REVIEW：\n{out_early}"


def test_7_missing_handled_at_column_degrades(tmp_path):
    """⑦ 缺 handled_at 列 → 降级 [WARN] 不崩（只统计）。"""
    t = T(tmp_path)
    conn = sqlite3.connect(t.tasks_db)
    conn.execute("ALTER TABLE task_events DROP COLUMN handled_at")
    conn.commit()
    conn.close()
    t.add_fail(creator="analysis-watch", eid=77)
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    assert "[WARN]" in out and "handled_at" in out, f"应降级 WARN：\n{out}"
    assert "#77" in out  # 只统计：事件仍列出（无 handled_at 可过滤）


def test_extra_deferred_agent_excluded(tmp_path):
    """附：exec.result='deferred_agent' → 不出（C 批：数量非清仓已退普通 agent 路径）。"""
    t = T(tmp_path)
    t.add_fail(creator="analysis-watch", result="deferred_agent",
               code="qty_not_full_exit", ref="condition:44")
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    assert "[EXEC-FAIL]" not in out, f"deferred_agent 不应出现：\n{out}"


def test_extra_exception_alerts_not_idle(tmp_path):
    """附：异常 → 告警行（不静默 IDLE）。"""
    t = T(tmp_path)
    with patch.object(tws, "build_output", side_effect=RuntimeError("boom")):
        rc, out = tws.main.__wrapped__() if hasattr(tws.main, "__wrapped__") else (None, None)
    # main() 捕获异常 → 输出 ⚠️ 行；直接调 main 验证
    import io
    import contextlib
    buf = io.StringIO()
    with patch.object(tws, "build_output", side_effect=RuntimeError("boom")), \
         contextlib.redirect_stdout(buf):
        rc = tws.main()
    assert rc == 0
    assert "⚠️" in buf.getvalue() and "boom" in buf.getvalue()
    assert buf.getvalue().strip() != "IDLE"


def test_extra_byte_stable_idle(tmp_path):
    """附：空库两次输出字节相同（IDLE 稳定 = 零唤醒前提）。"""
    T(tmp_path)
    with patch.object(tws, "datetime", FakeDTWeekend), \
         patch.object(tws, "is_trading_day", return_value=False):
        a = tws.build_output()
        b = tws.build_output()
    assert a == b == "IDLE"


def test_extra_ref_dict_shape(tmp_path):
    """附：ref 为 JSON dict 形状（'{"kind":"condition","id":44}'）→ 渲染 kind:id。"""
    t = T(tmp_path)
    t.add_fail(creator="portfolio-review", code="error", ref={"kind": "condition", "id": 44})
    with patch.object(tws, "datetime", FakeDT), \
         patch.object(tws, "is_trading_day", return_value=True):
        out = tws.build_output()
    assert "ref=condition:44" in out, f"dict ref 应渲染为 kind:id：\n{out}"
