"""任务书 C（2026-09-10）C1–C5 测试：watch_scan 消费/执行层改造。

跑法（隔离，零生产库接触——STOCK_TASKS_DB 指 /tmp 临时库，ptrade2 打桩）：
    cd stock-toolkit/skills/task-bus/scripts && \
    <paper-trading venv>/bin/python3 -m pytest tests/test_taskbook_c.py -q

验收映射（任务书 C）：
- C1（WP1 保护线同拍直调）：命中即调 `sell --price <检测价> --event-id <事件id>`（ptrade2 打桩
  断言参数，绝不在真实库触发下单）；失败码分流四类各有测试（stale_quote/halted 保持 active、
  insufficient_funds 连续 3 次 → suspended、no_position/already_fulfilled 归档、error 重试≤2 → 升级）。
- C2（WP3 写入侧）：WATCH_ALERT payload 带 exec/ref/creator/snapshot 5 字段快照；
  task_events handled_at/handled_by 列存在（幂等迁移）。
- C3（WP7 消费侧，生产现状+等待型跨带）：下穿 <band_min → band_break 弃单、上穿 >band_max
  无动作等回落（TTL 自然 expired）；等待型跨带 → band_skipped（发起方核价）；TTL → expired。
- C4（WP4 去重键）：_has_pending_event 键=(entity, cond_uid)——同 cond_uid 行 id 变化仍去重；
  窗口 pending+processing，failed 即释放。
- C5（WP5 消费侧）：check_watch_points 读 watch_points 表；触发消费写 consumed_at+
  trigger_event_id+status='consumed' 且行不删；buy 跌穿 min / sell 冲过 max → range_break；
  触发时漂移>5% → price_drifted 不执行；payload 带 creator。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
import watch_scan  # noqa: E402
import task_bus.db as tdb  # noqa: E402


@pytest.fixture(autouse=True)
def _legacy_executor(monkeypatch):
    """v14/Phase 4（2026-09-10）后 conditions 卖出口径缺省 = tracker（只追踪不直调）。

    本文件测的是 C1/WP1 的 **executor 机制**（现为回滚路径）→ 显式打开开关保持原覆盖；
    tracker 口径的用例在 tests/test_v14_phase4.py。
    """
    monkeypatch.setenv('PTRADE2_COND_SELL', 'executor')

PAPER_VENV = "/home/catmouse/Github_Project/yf-skills/stock-toolkit/skills/paper-trading/scripts/.venv/bin/python3"


# ---------- 隔离环境 ----------

class T:
    """隔离环境：/tmp 临时库 + ptrade2 打桩（零生产库、零真实下单）。"""

    def __init__(self, tmp):
        self.db = os.path.join(str(tmp), "tasks.db")
        os.makedirs(str(tmp), exist_ok=True)
        watch_scan.TASKS_DB = self.db
        watch_scan._ensure_task_table()
        tdb.ENV = "STOCK_TASKS_DB"
        os.environ["STOCK_TASKS_DB"] = self.db
        watch_scan._PRICE_CACHE.clear()
        watch_scan._PRE_CLOSE_CACHE.clear()
        watch_scan._QUOTE.clear()
        # kv 迁移标记清空（每次用例独立）
        conn = sqlite3.connect(self.db)
        conn.execute("DELETE FROM kv_store WHERE key='watch_points_migrated_at'")
        conn.commit()
        conn.close()

    def alerts(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM task_events WHERE type='WATCH_ALERT' ORDER BY id")]
        finally:
            conn.close()

    def ev_by_id(self, eid):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute("SELECT * FROM task_events WHERE id=?", (eid,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()


DDL = [
    """CREATE TABLE conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, cond_key TEXT,
        is_event INTEGER DEFAULT 0, cond_uid TEXT, type TEXT, name TEXT, price REAL,
        action TEXT, category TEXT, expiry_date TEXT, status TEXT,
        auto_link_cost INTEGER DEFAULT 0, peak_price REAL, created_at TEXT,
        modified_at TEXT, seq INTEGER, created_by TEXT DEFAULT '')""",
    """CREATE TABLE condition_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, condition_id INTEGER, old_price REAL,
        new_price REAL, reason TEXT, timestamp TEXT, level TEXT, override_triggers TEXT)""",
    """CREATE TABLE position (
        id INTEGER PRIMARY KEY, stock TEXT, code TEXT, strategy TEXT, status TEXT)""",
    """CREATE TABLE pool (
        stock TEXT, code TEXT, strategy TEXT, pool_status TEXT, pin INTEGER DEFAULT 0)""",
]


def _mk_pool(tmp_path, conds):
    """/tmp 隔离 pool 库（watch_scan.POOL_DB 指向），conds=[(aid,uid,type,name,price,action,creator)]。"""
    db = os.path.join(str(tmp_path), "master_pool.db")
    c = sqlite3.connect(db)
    for d in DDL:
        c.execute(d)
    for aid, uid, ctype, name, price, action, creator in conds:
        c.execute("INSERT INTO conditions (account_id, cond_uid, type, name, price, action, "
                  "category, status, created_by, created_at) VALUES (?,?,?,?,?,?,"
                  "'hard','active',?, '2026-09-10T10:00:00')",
                  (aid, uid, ctype, name, price, action, creator))
    # position 段（JOIN 用）+ pool 行（pool_stocks 兜底查询用）
    c.execute("INSERT INTO position (id, stock, code, strategy, status) "
              "VALUES (1, '测试股', 'sh600000', 'L1', 'open')")
    c.execute("INSERT INTO pool (stock, code, strategy, pool_status) "
              "VALUES ('测试股', 'sh600000', 'L1', 'active')")
    c.commit()
    c.close()
    watch_scan.POOL_DB = db
    return db


def _prices(mapping):
    """mock 取价（批量预取填充 _PRICE_CACHE → fetch_price 纯字典读）。"""
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update(mapping)


# ---------- C1：保护线同拍直调 ----------

def test_c1_hit_calls_sell_with_detected_price_and_event_id(tmp_path):
    """命中保护线 → 同拍调 ptrade2 sell --price <检测价> --event-id <事件id>（打桩断言）。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    captured = []
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: captured.append(list(a)) or "✅ 已卖出"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        lines = watch_scan.check_price_triggers()
    assert captured, f"命中必须同拍直调 ptrade2，实得 {captured}"
    call = captured[0]
    assert call[0] == "sell" and call[1] == "测试股", call
    assert "--price" in call and call[call.index("--price") + 1] == "8.80", call
    assert "--event-id" in call, call
    ev = t.alerts()
    assert len(ev) == 1, "事件仍写 WATCH_ALERT（留痕）"
    assert call[call.index("--event-id") + 1] == str(ev[0]["id"]), "event-id=本事件 id"
    assert any("SELL" in ln for ln in lines), lines


def _patch_prices(mapping):
    """mock 取价三件套：cache 预填 + fetch_price/fetch_price_any 纯字典读。

    同时 patch 取价函数本身——test_scan_moves_qfq 的 patcher 不回收（session 级泄漏），
    后跑的文件若只填 cache 会被泄漏的 fetch_price mock 覆盖（返回 10.10），这里显式
    覆盖并以 cache miss→None 兜底（不走子进程）。"""
    started = []

    class _P:
        def __enter__(self):
            watch_scan._PRICE_CACHE.clear()
            watch_scan._PRICE_CACHE.update(mapping)
            started.append(patch.object(watch_scan, "fetch_price",
                                        lambda code: mapping.get(code)))
            started.append(patch.object(watch_scan, "fetch_price_any",
                                        lambda code: mapping.get(code)))
            started.append(patch.object(watch_scan, "fetch_prices_batch",
                                        lambda codes: {}))
            for p in started:
                p.start()
            return self

        def __exit__(self, *a):
            for p in started:
                p.stop()
            return False
    return _P()


def test_c1_fail_stale_quote_keeps_active_no_escalation(tmp_path):
    """stale_quote（防线拒）→ 条件保持 active，下一拍重试，不升级。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    captured = []
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: captured.append(list(a))
                      or "❌ E3 行情防线拒绝按检测价成交：报价陈旧（>5min）"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        watch_scan.check_price_triggers()
    ev = t.alerts()
    assert ev, "事件仍写（留痕）"
    p = json.loads(ev[0]["payload"])
    assert p["exec"]["result"] == "failed" and p["exec"]["code"] == "stale_quote", p
    c = sqlite3.connect(watch_scan.POOL_DB)
    status = c.execute("SELECT status FROM conditions WHERE cond_uid='uid001'").fetchone()[0]
    c.close()
    assert status == "active", "stale_quote 条件保持 active（下一拍重试）"
    # 连拍重试不再洪泛升级
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: "❌ E3 行情防线拒绝按检测价成交：报价陈旧"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        watch_scan.check_price_triggers()
    c = sqlite3.connect(watch_scan.POOL_DB)
    status = c.execute("SELECT status FROM conditions WHERE cond_uid='uid001'").fetchone()[0]
    c.close()
    assert status == "active", "连拍 stale_quote 仍 active，不升级不挂起"


def test_c1_fail_insufficient_funds_three_times_suspends(tmp_path):
    """insufficient_funds 连续 3 次 → 条件置 suspended + 升级告警。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    lines: list[str] = []
    watch_scan.COND_EXEC_TICK_WINDOW = 0  # 模拟 3 个不同拍（真实拍间隔 15min > 窗口）
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: "❌ 资金不足。需要：¥900，可用：¥100，缺口：¥800"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        for i in range(3):
            lines = watch_scan.check_price_triggers()
    watch_scan.COND_EXEC_TICK_WINDOW = 600.0
    c = sqlite3.connect(watch_scan.POOL_DB)
    row = c.execute("SELECT status FROM conditions WHERE cond_uid='uid001'").fetchone()
    c.close()
    assert row and row[0] == "suspended", f"连续 3 次资金不足应 suspended，实得 {row}"
    assert any("升级" in ln or "suspended" in ln for ln in lines), lines


def test_c1_same_tick_repeated_fails_count_once(tmp_path):
    """同一拍窗口内重复失败不重复计数（防 cron/手动重叠双计，2026-09-10 审计补）。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: "❌ 资金不足。需要：¥900，可用：¥100，缺口：¥800"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        for i in range(3):
            watch_scan.check_price_triggers()  # 同拍窗口内 3 次调用
    # 计数只 +1（同拍窗口守卫），远未到 3 次阈值 → 条件不得 suspended
    c = sqlite3.connect(watch_scan.POOL_DB)
    row = c.execute("SELECT status FROM conditions WHERE cond_uid='uid001'").fetchone()
    c.close()
    assert row and row[0] == "active", f"同拍重复失败只计 1 次，不得挂起，实得 {row}"
    st = watch_scan.load_state()
    ent = (st.get(watch_scan.COND_EXEC_FAILS_KEY) or {}).get("uid001") or {}
    assert ent.get("count") == 1, f"同拍窗口内重复失败应只计 1，实得 {ent}"
    # 同一拍内直接再调计数器也不加（守卫单测）
    n2 = watch_scan._record_cond_exec_fail("uid001", "insufficient_funds")
    assert n2 == 1, f"同拍窗口内 _record_cond_exec_fail 不得双计，实得 {n2}"


def test_c1_fail_no_position_archives(tmp_path):
    """no_position / already_fulfilled → 条件归档（archived）。"""
    for i, reason in enumerate(("❌ 当前无持仓",
                                "❌ event_id 123 已成交（幂等拒绝，同请求不重复执行）")):
        t = T(os.path.join(str(tmp_path), f"np{i}"))
        _mk_pool(os.path.join(str(tmp_path), f"np{i}"),
                 [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
        with patch.object(watch_scan, "ptrade2", lambda *a, **k: reason), \
             patch.object(watch_scan, "in_trade_hours", lambda: True), \
             _patch_prices({"sh600000": 8.80}):
            watch_scan.check_price_triggers()
        c = sqlite3.connect(watch_scan.POOL_DB)
        row = c.execute("SELECT status FROM conditions WHERE cond_uid='uid001'").fetchone()
        c.close()
        assert row and row[0] == "archived", f"{reason} 应归档，实得 {row}"


def test_c1_fail_error_retries_twice_then_escalates(tmp_path):
    """error（异常/超时）→ 重试 ≤2 次 → 升级（条件保持 active，升级告警）。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    lines: list[str] = []
    watch_scan.COND_EXEC_TICK_WINDOW = 0  # 模拟 3 个不同拍（真实拍间隔 15min > 窗口）
    with patch.object(watch_scan, "ptrade2", lambda *a, **k: ""), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        for i in range(3):
            lines = watch_scan.check_price_triggers()
    watch_scan.COND_EXEC_TICK_WINDOW = 600.0
    c = sqlite3.connect(watch_scan.POOL_DB)
    status = c.execute("SELECT status FROM conditions WHERE cond_uid='uid001'").fetchone()[0]
    c.close()
    assert status == "active", "error 重试期条件保持 active"
    assert any("升级" in ln for ln in lines), f"重试耗尽应升级，实得 {lines}"


# ---------- C2：payload exec/ref/creator/snapshot + handled 列 ----------

def test_c2_alert_payload_has_exec_ref_creator_snapshot(tmp_path):
    """WATCH_ALERT payload 带 exec / ref={kind,id} / creator / snapshot 5 字段。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    with patch.object(watch_scan, "ptrade2", lambda *a, **k: "✅ 已卖出"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        watch_scan.check_price_triggers()
    p = json.loads(t.alerts()[0]["payload"])
    assert "exec" in p and "result" in p["exec"] and "at" in p["exec"], p
    assert p["ref"] == {"kind": "condition", "id": "uid001"}, p
    assert p["creator"] == "atr-auto", p
    snap = p["snapshot"]
    assert set(snap) == {"entity", "direction", "threshold", "price", "code"}, snap
    assert snap["entity"] == "测试股" and snap["direction"] == "sell", snap


def test_c2_empty_creator_fails_closed_keeps_pending(tmp_path):
    """created_by 为空 → fail-closed：不直调执行，事件保持 pending 晚审+通知。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "")])
    captured = []
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: captured.append(list(a)) or "✅"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        watch_scan.check_price_triggers()
    assert not captured, "creator 为空必须 fail-closed 不执行（晚审+通知）"
    ev = t.alerts()
    assert ev, "事件仍写（fail-closed 留痕）"
    assert ev[0]["status"] == "pending", "空 creator 事件保持 pending（晚审消费）"


def test_c2_handled_columns_exist_and_migrate(tmp_path):
    """task_events.handled_at / handled_by 列存在；旧库（无列）自动补列。"""
    t = T(tmp_path)
    conn = sqlite3.connect(t.db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    conn.close()
    assert {"handled_at", "handled_by"} <= cols, cols
    # 旧库补列
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
    conn = sqlite3.connect(old)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    conn.close()
    assert "handled_at" not in cols, "前置：旧库应无 handled_at"
    conn = watch_scan._handled_columns_ensure(old)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    conn.close()
    assert {"handled_at", "handled_by"} <= cols, "旧库应自动补列"


# ---------- C3：挂单 band 连续 2 拍 + 等待型跨带 ----------

def _mk_slot_db(tmp_path, band_min, band_max, order_ttl="2026-12-31T15:00:00",
                band_out_count=0, placed_px=None, created_price_in_band=True):
    db = os.path.join(str(tmp_path), "slots.db")
    c = sqlite3.connect(db)
    c.executescript(
        "CREATE TABLE event_slots (event_key TEXT PRIMARY KEY, status TEXT, opened_at TEXT,"
        " closed_at TEXT, budget REAL, realized REAL, news_kind TEXT, title TEXT,"
        " members_json TEXT, invalidation TEXT, topup_locked INTEGER, orig_budget REAL,"
        " migrated_at TEXT, migrated_stock TEXT, fill_status TEXT, fill_at TEXT, note TEXT,"
        " band_min REAL, band_max REAL, anchor_price REAL, order_ttl TEXT, order_id TEXT,"
        " rejudge_count INTEGER, created_by TEXT, placed_px REAL, band_out_count INTEGER DEFAULT 0);"
        "CREATE TABLE event_slot_members (event_key TEXT, stock TEXT, weight REAL,"
        " joined_at TEXT, exited_at TEXT, migrated_at TEXT);"
        "CREATE TABLE position (id INTEGER PRIMARY KEY, stock TEXT, code TEXT,"
        " strategy TEXT, status TEXT);")
    anchor = band_min + (band_max - band_min) / 2 if created_price_in_band else band_min * 0.9
    c.execute("INSERT INTO event_slots (event_key, status, opened_at, budget, fill_status,"
              " band_min, band_max, anchor_price, order_ttl, band_out_count, placed_px) "
              "VALUES ('ND#C3', 'pending_order', '2026-09-10T09:35:00', 100000, 'pending',"
              " ?, ?, ?, ?, ?, ?)", (band_min, band_max, anchor, order_ttl, band_out_count,
                                     placed_px if placed_px else anchor))
    c.execute("INSERT INTO event_slot_members (event_key, stock, weight, joined_at) "
              "VALUES ('ND#C3', '测试股', 1.0, '2026-09-10T09:35:00')")
    c.execute("INSERT INTO position (id, stock, code, strategy, status) "
              "VALUES (1, '测试股', 'sh600000', 'NEWS', 'open')")
    c.commit()
    c.close()
    watch_scan.POOL_DB = db
    return db


def _slot_row():
    c = sqlite3.connect(watch_scan.POOL_DB)
    c.row_factory = sqlite3.Row
    try:
        return dict(c.execute("SELECT * FROM event_slots WHERE event_key='ND#C3'").fetchone())
    finally:
        c.close()


def _expire_stub(captured: list):
    """打桩 sleeve-order-expire CLI：捕获参数 + 模拟 CLI 效果（pending_order → pending_rejudge）。"""

    def _stub(*args, timeout=90):
        a = list(args)
        captured.append(a)
        if a and a[0] == "sleeve-order-expire":
            ek = a[1]
            c = sqlite3.connect(watch_scan.POOL_DB)
            try:
                c.execute("UPDATE event_slots SET status='pending_rejudge' WHERE event_key=?", (ek,))
                c.commit()
            finally:
                c.close()
        return "✅ 已弃单"
    return _stub


# ---------- C3：挂单 band 语义（生产现状 + 等待型跨带） ----------

def test_c3_in_band_emits_fill_line(tmp_path):
    """价 ∈ band → 触带行（sleeve-order-fill --price 检测价，不变）。"""
    _mk_slot_db(tmp_path, band_min=10.0, band_max=11.0)
    captured: list = []
    with _patch_prices({"sh600000": 10.50}), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "ptrade2", _expire_stub(captured)):
        lines = watch_scan.check_price_orders()
    assert any("sleeve-order-fill" in ln and "--price 10.50" in ln for ln in lines), lines
    assert not captured, "带内不弃单"


def test_c3_above_band_no_action_waits_for_fall_back(tmp_path):
    """上穿 > band_max → 无动作不输出（挂单等回落，TTL 到期自然 expired；生产现状）。"""
    _mk_slot_db(tmp_path, band_min=10.0, band_max=11.0)
    captured: list = []
    with _patch_prices({"sh600000": 11.80}), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "ptrade2", _expire_stub(captured)):
        lines = watch_scan.check_price_orders()
    assert lines == [], f"上穿应无动作不输出，实得 {lines}"
    assert not captured, "上穿不得弃单"
    row = _slot_row()
    assert row["status"] == "pending_order", "上穿挂单等回落"


def test_c3_below_band_break_expires(tmp_path):
    """下穿 < band_min → 同拍直调 sleeve-order-expire --reason band_break（生产现状）。"""
    _mk_slot_db(tmp_path, band_min=10.0, band_max=11.0)
    captured: list = []
    with _patch_prices({"sh600000": 9.50}), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "ptrade2", _expire_stub(captured)):
        lines = watch_scan.check_price_orders()
    row = _slot_row()
    assert row["status"] == "pending_rejudge", f"下穿应弃单，实得 {row}"
    assert any("band_break" in ln for ln in lines), lines
    assert captured and captured[0][0] == "sleeve-order-expire", captured
    assert "--reason" in captured[0] \
        and captured[0][captured[0].index("--reason") + 1] == "band_break", captured


def test_c3_waiting_type_cross_band_skipped(tmp_path):
    """等待型（创建价 ∉ band）：现价跨到另一侧 → 同拍直调 --reason band_skipped。"""
    # 创建价 9.0 < band_min=10.0（等待型）；现价 11.5 > band_max（跨到另一侧）
    _mk_slot_db(tmp_path, band_min=10.0, band_max=11.0, placed_px=9.0,
                created_price_in_band=False)
    captured: list = []
    with _patch_prices({"sh600000": 11.50}), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "ptrade2", _expire_stub(captured)):
        lines = watch_scan.check_price_orders()
    row = _slot_row()
    assert row["status"] == "pending_rejudge", f"等待型跨带应弃单，实得 {row}"
    assert any("band_skipped" in ln for ln in lines), lines
    assert captured and "--reason" in captured[0] \
        and captured[0][captured[0].index("--reason") + 1] == "band_skipped", captured


def test_c3_waiting_type_same_side_waits(tmp_path):
    """等待型仍在创建价同侧 → 继续等（TTL 到期才 expired）。"""
    _mk_slot_db(tmp_path, band_min=10.0, band_max=11.0, placed_px=9.0,
                created_price_in_band=False)
    captured: list = []
    with _patch_prices({"sh600000": 9.50}), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "ptrade2", _expire_stub(captured)):
        lines = watch_scan.check_price_orders()
    row = _slot_row()
    assert row["status"] == "pending_order", "同侧应继续等"
    assert lines == [], "同侧无输出不唤醒"
    assert not captured, "同侧不得弃单"


def test_c3_ttl_expired_unchanged(tmp_path):
    """TTL 过期 → --reason expired（不变，输出行由消费方执行）。"""
    _mk_slot_db(tmp_path, band_min=10.0, band_max=11.0,
                order_ttl=(datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"))
    captured: list = []
    with _patch_prices({"sh600000": 10.50}), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "ptrade2", _expire_stub(captured)):
        lines = watch_scan.check_price_orders()
    assert any("expired" in ln for ln in lines), lines


# ---------- C4：去重键 (entity, cond_uid) ----------

def test_c4_dedupe_by_cond_uid_not_row_id(tmp_path):
    """同 cond_uid 但行 id 变化（DELETE+INSERT 漂移）→ 不重复入队。"""
    t = T(tmp_path)
    db = _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    c = sqlite3.connect(db)
    row_id = c.execute("SELECT id FROM conditions WHERE cond_uid='uid001'").fetchone()[0]
    c.close()
    ok = watch_scan._write_alert("测试股", "sh600000", "sell", row_id,
                                 "成本保护", 9.0, 8.80, mode="trade")
    assert ok, "首次写入应成功"
    # 行 id 漂移（conditions_manager.save = DELETE+INSERT，id 变化）
    c = sqlite3.connect(db)
    c.execute("DELETE FROM conditions WHERE cond_uid='uid001'")
    c.execute("INSERT INTO conditions (account_id, cond_uid, type, name, price, action, "
              "category, status, created_by, created_at) VALUES (1,'uid001','cost_protection',"
              "'成本保护',9.0,'清仓','hard','active','atr-auto','2026-09-10T10:00:00')")
    c.commit()
    new_id = c.execute("SELECT id FROM conditions WHERE cond_uid='uid001'").fetchone()[0]
    c.close()
    assert new_id != row_id, "前置：行 id 应已漂移"
    # 旧键（payload LIKE cond_id）查不到 → 会重复入队；新键 (entity, cond_uid) 应去重
    dup = watch_scan._write_alert("测试股", "sh600000", "sell", new_id,
                                  "成本保护", 9.0, 8.80, mode="trade")
    assert not dup, "同 cond_uid 行 id 变化不得重复入队"
    assert len(t.alerts()) == 1, "去重后仍只有 1 条事件"


def test_c4_failed_releases_window(tmp_path):
    """failed 事件不占用去重窗口（pending+processing 才占）。"""
    t = T(tmp_path)
    db = _mk_pool(tmp_path, [(1, "uid001", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    c = sqlite3.connect(db)
    row_id = c.execute("SELECT id FROM conditions WHERE cond_uid='uid001'").fetchone()[0]
    c.close()
    ok = watch_scan._write_alert("测试股", "sh600000", "sell", row_id,
                                 "成本保护", 9.0, 8.80, mode="trade")
    assert ok
    conn = sqlite3.connect(t.db)
    eid = conn.execute("SELECT id FROM task_events WHERE type='WATCH_ALERT'").fetchone()[0]
    conn.execute("UPDATE task_events SET status='failed' WHERE id=?", (eid,))
    conn.commit()
    conn.close()
    assert watch_scan._has_pending_event("测试股", "sell", row_id) is False, \
        "failed 应释放去重窗口"
    # 模拟生产 C1 失败分流：_restore_active 撤销触发即失效 → 条件回 active
    watch_scan._restore_active(row_id, "uid001", "exec失败恢复（failed 释放窗口）")
    # processing 仍占窗口
    ok2 = watch_scan._write_alert("测试股", "sh600000", "sell", row_id,
                                  "成本保护", 9.0, 8.80, mode="trade")
    assert ok2
    conn = sqlite3.connect(t.db)
    conn.execute("UPDATE task_events SET status='processing' "
                 "WHERE id=(SELECT MAX(id) FROM task_events WHERE type='WATCH_ALERT')")
    conn.commit()
    conn.close()
    assert watch_scan._has_pending_event("测试股", "sell", row_id) is True, \
        "processing 应占用去重窗口"


# ---------- C5：check_watch_points 读表消费 ----------

WP_DDL = """CREATE TABLE watch_points (
    wp_id TEXT PRIMARY KEY, entity TEXT NOT NULL, code TEXT, price REAL NOT NULL,
    min REAL, mode TEXT NOT NULL DEFAULT 'eval', amount REAL, note TEXT DEFAULT '',
    created_by TEXT DEFAULT '', added_at TEXT, status TEXT NOT NULL DEFAULT 'active',
    consumed_at TEXT, trigger_event_id INTEGER)"""


def _mk_wp(tmp, rows):
    conn = sqlite3.connect(watch_scan.TASKS_DB)
    conn.execute(WP_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wp_entity ON watch_points(entity, status)")
    for r in rows:
        conn.execute("INSERT INTO watch_points (wp_id, entity, code, price, min, mode, amount,"
                     " note, created_by, added_at, status) VALUES (?,?,?,?,?,?,?,?,?,?, 'active')", r)
    conn.commit()
    conn.close()


def _wp_row(wp_id):
    conn = sqlite3.connect(watch_scan.TASKS_DB)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM watch_points WHERE wp_id=?", (wp_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def test_c5_consume_marks_not_deletes(tmp_path):
    """触发消费：consumed_at+trigger_event_id+status='consumed'，行不得删除。"""
    t = T(tmp_path)
    _mk_wp(tmp_path, [("wp:卖:1", "卖出股", "sh600000", 12.0, 12.5, "sell", None,
                       "带内限价卖", "analysis-watch", "09-10 10:00")])
    with _patch_prices({"sh600000": 12.2}), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         patch.object(watch_scan, "ptrade2", lambda *a, **k: "✅ 已卖出"):
        alerts = watch_scan.check_watch_points()
    assert any("卖出点触发" in a for a in alerts), alerts
    row = _wp_row("wp:卖:1")
    assert row is not None, "行不得删除（触发即失效=状态置 consumed）"
    assert row["status"] == "consumed", row
    assert row["consumed_at"], row
    assert row["trigger_event_id"], row
    ev = [e for e in t.alerts() if e["id"] == row["trigger_event_id"]]
    assert ev, "trigger_event_id 应指向本事件"
    p = json.loads(ev[0]["payload"])
    assert p["creator"] == "analysis-watch", p
    assert p["ref"] == {"kind": "watchpoint", "id": "wp:卖:1"}, p


def test_c5_buy_range_below_min_range_break(tmp_path):
    """buy 区间价跌穿 min → expired:range_break（不触发消费）。"""
    t = T(tmp_path)
    _mk_wp(tmp_path, [("wp:买:1", "区间股", "sh600001", 10.0, 9.5, "buy", 100000.0,
                       "区间建仓", "analysis-watch", "09-10 10:00")])
    with _patch_prices({"sh600001": 9.0}), \
         patch.object(watch_scan, "in_trade_hours", lambda: True):
        alerts = watch_scan.check_watch_points()
    assert alerts == [] or not any("建仓点触发" in a for a in alerts), alerts
    row = _wp_row("wp:买:1")
    assert row["status"] == "expired:range_break", row
    assert row["trigger_event_id"] is None, "range_break 不消费不触发"


def test_c5_sell_range_above_max_range_break(tmp_path):
    """sell 区间价冲过 max（min 列=上沿）→ expired:range_break。"""
    t = T(tmp_path)
    _mk_wp(tmp_path, [("wp:卖:2", "带外股", "sh600002", 12.0, 12.5, "sell", None,
                       "带内限价卖", "analysis-watch", "09-10 10:00")])
    with _patch_prices({"sh600002": 12.6}), \
         patch.object(watch_scan, "in_trade_hours", lambda: True):
        alerts = watch_scan.check_watch_points()
    assert not any("卖出点触发" in a for a in alerts), alerts
    row = _wp_row("wp:卖:2")
    assert row["status"] == "expired:range_break", row


def test_c5_price_drifted_no_execution(tmp_path):
    """触发时 |现价−挂点价|/挂点价 > 5% → price_drifted，不执行消费。"""
    t = T(tmp_path)
    _mk_wp(tmp_path, [("wp:卖:3", "脱靶股", "sh600003", 10.0, None, "sell", None,
                       "单值卖出", "analysis-watch", "09-10 10:00")])
    with _patch_prices({"sh600003": 10.80}), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         patch.object(watch_scan, "ptrade2", lambda *a, **k: "✅ 已卖出"):
        alerts = watch_scan.check_watch_points()
    assert not any("卖出点触发" in a for a in alerts), alerts
    row = _wp_row("wp:卖:3")
    assert row["status"] == "price_drifted", row
    assert row["trigger_event_id"] is None, "脱靶不消费"
    assert t.alerts() == [] or all(
        "卖出点触发" not in json.dumps(e["payload"]) for e in t.alerts()), "不得写消费事件"


def test_c5_drift_within_threshold_consumes(tmp_path):
    """漂移 ≤5% 正常消费（边界回归）。"""
    t = T(tmp_path)
    _mk_wp(tmp_path, [("wp:卖:4", "贴价股", "sh600004", 10.0, None, "sell", None,
                       "单值卖出", "analysis-watch", "09-10 10:00")])
    with _patch_prices({"sh600004": 10.40}), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         patch.object(watch_scan, "ptrade2", lambda *a, **k: "✅ 已卖出"):
        alerts = watch_scan.check_watch_points()
    assert any("卖出点触发" in a for a in alerts), alerts
    row = _wp_row("wp:卖:4")
    assert row["status"] == "consumed", row


if __name__ == "__main__":
    import shutil
    import tempfile

    failures = 0
    for name, fn in sorted((n, f) for n, f in list(globals().items())
                           if n.startswith("test_") and callable(f)):
        d = tempfile.mkdtemp(prefix="taskbook_c_")
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


# ---------- 审计补丁（2026-09-10 主代理 R1.5）：非清仓数量语义不得直调 ----------

def test_audit_tp_ladder_third_defers_to_agent(tmp_path):
    """TP 阶梯 action="次日卖出1/3（收盘确认触发）" → 禁 --all，不直调，交 agent。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid001", "take_profit_1", "分批止盈①+30%卖1/3", 9.0,
                         "次日卖出1/3（收盘确认触发）", "analysis-watch")])
    captured = []
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: captured.append(list(a)) or "✅ 已卖出"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 9.50}):
        lines = watch_scan.check_price_triggers()
    assert not captured, f"1/3 语义不得直调 --all（超卖 3 倍），实得 {captured}"
    ev = t.alerts()
    assert ev, "事件仍写（留痕 + 交 agent 消费）"
    p = json.loads(ev[0]["payload"])
    assert p["exec"]["result"] == "deferred_agent", p
    assert p["exec"]["code"] == "qty_not_full_exit", p
    assert ev[0]["status"] == "pending", "事件保持 pending 等 agent"
    assert ev[0]["handled_at"] is None, "handled_at 是消费方标记，执行方不得写"
    assert any("交 agent" in ln for ln in lines), lines


def test_audit_ambiguous_action_defers_to_agent(tmp_path):
    """trailing_stop action="执行"（两级减半语义）→ 同样不直调。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid002", "trailing_stop", "移动止损", 9.0,
                         "执行", "atr-auto")])
    captured = []
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: captured.append(list(a)) or "✅"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        lines = watch_scan.check_price_triggers()
    assert not captured, f"模糊数量语义不得直调，实得 {captured}"
    ev = t.alerts()
    p = json.loads(ev[0]["payload"])
    assert p["exec"]["result"] == "deferred_agent" and p["exec"]["code"] == "qty_not_full_exit", p


def test_audit_full_exit_still_direct_calls(tmp_path):
    """清仓语义 → 仍同拍直调 --all（机械腿覆盖 38/115 条，不放宽）。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid003", "cost_protection", "sleeve成本保护-2.0×ATR", 9.0,
                         "清仓", "atr-auto")])
    captured = []
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: captured.append(list(a)) or "✅ 已卖出"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        watch_scan.check_price_triggers()
    assert captured and captured[0][0] == "sell" and "--all" in captured[0], captured


def test_audit_exec_write_leaves_handled_at_null(tmp_path):
    """执行方写 exec 时不得写 handled_at（否则 tech-watch 查不到未处置失败）。"""
    t = T(tmp_path)
    _mk_pool(tmp_path, [(1, "uid004", "cost_protection", "成本保护", 9.0, "清仓", "atr-auto")])
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: "❌ E3 行情防线拒绝按检测价成交：报价陈旧（>5min）"), \
         patch.object(watch_scan, "in_trade_hours", lambda: True), \
         _patch_prices({"sh600000": 8.80}):
        watch_scan.check_price_triggers()
    ev = t.alerts()
    assert ev and json.loads(ev[0]["payload"])["exec"]["code"] == "stale_quote"
    assert ev[0]["handled_at"] is None, "执行方不得写 handled_at（消费方 tech-watch 才写）"
