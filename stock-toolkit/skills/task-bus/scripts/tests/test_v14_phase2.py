"""执行层 Phase 2 回归锁 · task-bus 侧（系统兜底单扫描：影子不执行 / 逐票白名单 / 无 TTL）。

跑法（隔离；零生产库、零触网、零真实下单）：
    cd stock-toolkit/skills/task-bus/scripts && \
    <paper-trading venv>/bin/python3 -m pytest tests/test_v14_phase2.py -q

对应方案：plans/2026-09-10_1306-phase2-protect-orders.md（§0.1 无 TTL / §4 决策①②④ / §5 测试）。

覆盖：
- 无 TTL：``order_ttl=NULL`` 的兜底单**不得**产生 expired 行（对照：agent 挂单到点必须出）。
- 影子期铁律：mode=shadow（或缺省 off）→ 命中只留痕 + shadow_log，**零 ptrade2 调用**。
- 执行期：mode=orders **且该票在白名单** → 同拍直调 ``ptrade2 sell <名> --qty N``；
  白名单外 → 仍只留痕（禁止全局翻转）。
- fail-closed：标的名/qty 不可判定 → 不调用 ptrade2，出行说明。
- 几何：现价 > 保护线（反向）→ 无任何输出（卖单只等，不弃单）。
"""
import json
import os
import sqlite3
import sys
from unittest.mock import patch

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

import watch_scan  # noqa: E402

BIG = 9.9e9
STOCK, CODE = "测试股", "sh600000"
STOCK2, CODE2 = "白名单外票", "sh600001"

DDL = """
CREATE TABLE IF NOT EXISTS event_slots (
    event_key TEXT PRIMARY KEY, status TEXT, opened_at TEXT, closed_at TEXT, budget REAL,
    realized REAL, news_kind TEXT, title TEXT, members_json TEXT, invalidation TEXT,
    topup_locked INTEGER, orig_budget REAL, migrated_at TEXT, migrated_stock TEXT,
    fill_status TEXT, fill_at TEXT, note TEXT, band_min REAL, band_max REAL,
    anchor_price REAL, order_ttl TEXT, order_id TEXT, rejudge_count INTEGER,
    created_by TEXT, placed_px REAL, band_out_count INTEGER DEFAULT 0,
    side TEXT DEFAULT 'buy', qty INTEGER, group_key TEXT, batch_id INTEGER);
CREATE TABLE IF NOT EXISTS event_slot_members (
    event_key TEXT, stock TEXT, weight REAL, joined_at TEXT, exited_at TEXT, migrated_at TEXT);
CREATE TABLE IF NOT EXISTS position (
    id INTEGER PRIMARY KEY, stock TEXT, code TEXT, strategy TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, seq INTEGER,
    operation TEXT, stock_code TEXT, quantity INTEGER, price REAL, total_cost REAL,
    timestamp TEXT, note TEXT DEFAULT '', event_id TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS shadow_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, key TEXT, payload TEXT,
    payoff REAL, created_at TEXT, filled_at TEXT);
"""


class Iso:
    def __init__(self, root):
        self.root = str(root)
        self.ws = os.path.join(self.root, "ws")
        self.pool = os.path.join(self.ws, ".paper-trading", "master_pool.db")
        self.tasks = os.path.join(self.ws, "data", "tasks", "tasks.db")
        os.makedirs(os.path.dirname(self.pool), exist_ok=True)
        os.makedirs(os.path.dirname(self.tasks), exist_ok=True)
        c = sqlite3.connect(self.pool)
        c.executescript(DDL)
        c.execute("INSERT OR REPLACE INTO position (id, stock, code, strategy, status) "
                  "VALUES (1, ?, ?, 'TECH', 'open')", (STOCK, CODE))
        c.execute("INSERT OR REPLACE INTO position (id, stock, code, strategy, status) "
                  "VALUES (2, ?, ?, 'TECH', 'open')", (STOCK2, CODE2))
        c.commit()
        c.close()
        c = sqlite3.connect(self.tasks)
        c.execute("CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "type TEXT, status TEXT, creator TEXT, payload TEXT, created_at TEXT, "
                  "handled_at TEXT)")
        c.commit()
        c.close()

    def protect_slot(self, code=CODE, line=10.0, qty=300, ttl=None, status="pending_order",
                     batch_id=20260910):
        """系统兜底单槽（自带槽，无成员段、无 TTL）。"""
        key = f"protect:{code}"
        band_lo = 0.0        # 跌破卖几何：[哨兵下沿 0, 保护线]
        c = sqlite3.connect(self.pool)
        c.execute(
            "INSERT OR REPLACE INTO event_slots (event_key, status, opened_at, budget, "
            "fill_status, band_min, band_max, anchor_price, order_ttl, band_out_count, "
            "placed_px, side, qty, group_key, batch_id, created_by, note) "
            "VALUES (?,?,'2026-09-10T09:31:00',0,'pending',?,?,?,?,0,?,'sell',?,?,?,'atr-auto',"
            "' [兜底单 kind=cost]')",
            (key, status, band_lo, line, line, ttl, line, qty, f"{code}:protect", batch_id))
        c.commit()
        c.close()
        return key

    def shadow_kinds(self):
        c = sqlite3.connect(self.pool)
        try:
            return [r[0] for r in c.execute("SELECT kind FROM shadow_log ORDER BY id")]
        finally:
            c.close()

    def exec_lines(self, out):
        return [l for l in out if "PROTECT-ORDER" in l]


@pytest.fixture
def iso(tmp_path):
    it = Iso(tmp_path)
    saved_env = {k: os.environ.get(k) for k in
                 ("STOCK_ANALYSIS_WORKSPACE", "STOCK_TASKS_DB", "PTRADE2_EXEC_LAYER_FILE",
                  "PTRADE2_PROTECT_ORDERS")}
    saved = (watch_scan.POOL_DB, watch_scan.TASKS_DB, dict(watch_scan._PRICE_CACHE))
    os.environ["STOCK_ANALYSIS_WORKSPACE"] = it.ws
    os.environ["STOCK_TASKS_DB"] = it.tasks
    os.environ.pop("PTRADE2_PROTECT_ORDERS", None)
    watch_scan.POOL_DB, watch_scan.TASKS_DB = it.pool, it.tasks
    watch_scan._PRICE_CACHE.clear()
    yield it
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    watch_scan.POOL_DB, watch_scan.TASKS_DB = saved[0], saved[1]
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update(saved[2])


def _scan(calls, px_map):
    """跑 check_price_orders（行情/ptrade2 全打桩）。返回 (输出行, ptrade2 调用列表)。"""
    def stub(*a, **k):
        calls.append([str(x) for x in a])
        return "✅ 已卖出"

    with patch.object(watch_scan, "fetch_price_any", lambda code: px_map.get(code)), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "_slot_member_code", lambda ek: None), \
         patch.object(watch_scan, "ptrade2", stub):
        return watch_scan.check_price_orders()


# ------------------------------------------------------------------ 无 TTL
def test_protect_slot_without_ttl_never_expires(iso):
    """兜底单 order_ttl=NULL → 不得出 expired 行（对照：agent 挂单到点必须出）。"""
    iso.protect_slot(ttl=None)
    out = _scan([], {CODE: 9.8})           # 跌破线 → 带内 → 影子留痕
    assert not [l for l in out if "expired" in l], f"无 TTL 不得过期：{out}"
    assert iso.exec_lines(out), "带内命中应有留痕行"
    # 对照：agent 挂单带 TTL → 到点出 expired
    c = sqlite3.connect(iso.pool)
    c.execute("INSERT OR REPLACE INTO event_slots (event_key, status, opened_at, budget, "
              "fill_status, band_min, band_max, order_ttl, placed_px, side, qty, created_by) "
              "VALUES ('ND#1','pending_order','2026-09-10T09:00:00',100000,'pending',"
              "10.0,11.0,'2020-01-01T15:00:00',10.5,'sell',100,'msg-watch')")
    c.execute("INSERT OR REPLACE INTO event_slot_members (event_key, stock, weight, joined_at) "
              "VALUES ('ND#1', ?, 1.0, '2026-09-10T09:00:00')", (STOCK,))
    c.commit()
    c.close()
    out2 = _scan([], {CODE: 10.5})
    assert [l for l in out2 if "expired" in l], "agent 挂单到点仍须出 expired（语义未变）"


# ------------------------------------------------------------ 影子期铁律
def test_shadow_mode_hit_never_calls_ptrade2(iso, tmp_path):
    cfg = tmp_path / "exec_layer.json"
    cfg.write_text(json.dumps({"protect_orders": {"mode": "shadow"}}), encoding="utf-8")
    os.environ["PTRADE2_EXEC_LAYER_FILE"] = str(cfg)
    iso.protect_slot(line=10.0, qty=300)
    calls = []
    out = _scan(calls, {CODE: 9.8})       # 现价 ≤ 保护线 → 命中
    lines = iso.exec_lines(out)
    assert len(lines) == 1 and "影子期" in lines[0], out
    assert calls == [], f"影子期铁律：零 ptrade2 调用，实得 {calls}"
    assert "protect_hit" in iso.shadow_kinds(), "命中必须留痕（影子账）"


def test_default_off_writes_no_shadow_line_but_still_no_exec(iso):
    """缺省（无配置文件）= off：不执行、只留痕（历史保护槽不会因关开关而失控）。"""
    iso.protect_slot(line=10.0)
    calls = []
    out = _scan(calls, {CODE: 9.8})
    assert calls == [], "off 更不得执行"
    assert "影子期" in iso.exec_lines(out)[0]


# ------------------------------------------------------------ 逐票白名单
def test_orders_mode_executes_only_whitelisted_stock(iso, tmp_path):
    cfg = tmp_path / "exec_layer.json"
    cfg.write_text(json.dumps({"protect_orders": {"mode": "orders",
                                                  "exec_stocks": [STOCK]}}), encoding="utf-8")
    os.environ["PTRADE2_EXEC_LAYER_FILE"] = str(cfg)
    iso.protect_slot(code=CODE, line=10.0, qty=300)
    iso.protect_slot(code=CODE2, line=20.0, qty=500)
    calls = []
    out = _scan(calls, {CODE: 9.8, CODE2: 19.5})
    # 白名单内 → 同拍直调；白名单外 → 只留痕
    assert len(calls) == 1, f"只允许白名单票执行，实得 {calls}"
    assert calls[0][:2] == ["sell", STOCK] and "--qty" in calls[0]
    assert "300" in calls[0] and "--price" in calls[0]
    assert "--event-id" in calls[0] and calls[0][-1] == f"protect:{CODE}"
    outside = [l for l in iso.exec_lines(out) if STOCK2 in l]
    assert outside and "影子期" in outside[0], out
    assert "protect_exec" in iso.shadow_kinds()


def test_orders_mode_missing_qty_is_fail_closed(iso, tmp_path):
    cfg = tmp_path / "exec_layer.json"
    cfg.write_text(json.dumps({"protect_orders": {"mode": "orders",
                                                  "exec_stocks": [STOCK]}}), encoding="utf-8")
    os.environ["PTRADE2_EXEC_LAYER_FILE"] = str(cfg)
    iso.protect_slot(line=10.0, qty=None)
    calls = []
    out = _scan(calls, {CODE: 9.8})
    assert calls == [], "qty 不可判定 → 不卖（fail-closed）"
    assert "执行被拒" in iso.exec_lines(out)[0]


def test_orders_mode_unknown_stock_name_is_fail_closed(iso, tmp_path):
    """slots 有、position 段没有 → 标的名不可判定 → 不卖。"""
    cfg = tmp_path / "exec_layer.json"
    cfg.write_text(json.dumps({"protect_orders": {"mode": "orders",
                                                  "exec_stocks": [STOCK]}}), encoding="utf-8")
    os.environ["PTRADE2_EXEC_LAYER_FILE"] = str(cfg)
    iso.protect_slot(code="sh600999", line=10.0)      # position 里没有该 code
    calls = []
    out = _scan(calls, {"sh600999": 9.8})
    assert calls == [], "标的名解析不到 → 不卖"
    assert "执行被拒" in iso.exec_lines(out)[0]


# ------------------------------------------------------------------ 几何
def test_protect_slot_above_line_is_silent(iso):
    """现价 > 保护线 → 无输出（卖单只等；不弃单、不唤醒）。"""
    iso.protect_slot(line=10.0)
    out = _scan([], {CODE: 10.5})
    assert iso.exec_lines(out) == [], out


def test_protect_mode_helper_contract(iso, tmp_path):
    assert watch_scan._protect_mode(STOCK) == "off"          # 缺文件=off
    cfg = tmp_path / "exec_layer.json"
    cfg.write_text(json.dumps({"protect_orders": {"mode": "orders",
                                                  "exec_stocks": [STOCK]}}), encoding="utf-8")
    os.environ["PTRADE2_EXEC_LAYER_FILE"] = str(cfg)
    assert watch_scan._protect_mode(STOCK) == "orders"
    assert watch_scan._protect_mode(STOCK2) == "shadow"
    os.environ["PTRADE2_PROTECT_ORDERS"] = "off"
    assert watch_scan._protect_mode(STOCK) == "off"
