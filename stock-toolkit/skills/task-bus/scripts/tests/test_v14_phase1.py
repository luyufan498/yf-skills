"""执行层 Phase 1 回归锁（v14：side + 单边带 + 三出口 + 卖单仲裁/clamp + 组内联动失效）。

跑法（隔离；零生产库接触）：
    cd stock-toolkit/skills/task-bus/scripts && \
    <paper-trading venv>/bin/python3 -m pytest tests/test_v14_phase1.py -q

对应方案：plans/2026-09-10_110047-execution-layer-orders-decoupling.md（D4/D7/D10 + 附录 A7）。

隔离设计：pytest tmp_path 临时 workspace，watch_scan.POOL_DB/TASKS_DB 指向 tmp 库；
行情（fetch_price_any）与 ptrade2 全打桩（零触网、零真实下单）。

覆盖：
- A3/几何四行：涨破卖（band=[X,9.9e9]）在下跌中不动作；跌破卖（band=[0,X]）在上涨中不动作。
- A3/三出口：进带→sell 出行；TTL→expired 出行；取价失败→跳过（fail-closed）。
- D4/跳空：跨两档 → 两档全部兑现 + "同拍成交 N 档"留痕；只跨一档 → 只成交一张。
- A5/clamp：累计卖出 ≤ 段实时持仓（trades 汇总），超限 clamp 留痕。
- D7/卖单永不弃单：反向行情下无 expire 直调（打桩断言 ptrade2 零调用）。
- D10/联动失效：同组已有成交且段持仓归零 → 其余出行 group_closed；未耗尽/NULL 持仓不动；
  重复调用输出一致（幂等）；batch_id 更大（刚重挂）的单被豁免。
- 旧槽回归：side='buy' 走原四态（逐字节不变）。
"""
import os
import sqlite3
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER_SCRIPTS = ("/home/catmouse/Github_Project/yf-skills/stock-toolkit/"
                 "skills/paper-trading/scripts")
VENV_PY = os.path.join(PAPER_SCRIPTS, ".venv/bin/python3")
sys.path.insert(0, SCRIPTS_DIR)

import watch_scan  # noqa: E402

BIG = 9.9e9          # 哨兵上沿（≥X 语义）
STOCK, CODE = "测试股", "sh600000"

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
"""

FUTURE = "2026-12-31T15:00:00"
PAST = "2020-01-01T15:00:00"


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
                  "VALUES (1, ?, ?, 'NEWS', 'open')", (STOCK, CODE))
        c.commit()
        c.close()
        c = sqlite3.connect(self.tasks)
        c.execute("CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "type TEXT, status TEXT, creator TEXT, payload TEXT, created_at TEXT, "
                  "handled_at TEXT)")
        c.commit()
        c.close()

    def slot(self, key, band_min, band_max, *, side="sell", qty=333, ttl=FUTURE,
             placed_px=12.0, fill_status="pending", status="pending_order",
             group_key=None, batch_id=None, code=CODE):
        c = sqlite3.connect(self.pool)
        c.execute(
            "INSERT OR REPLACE INTO event_slots (event_key, status, opened_at, budget, "
            "fill_status, band_min, band_max, anchor_price, order_ttl, band_out_count, "
            "placed_px, side, qty, group_key, batch_id) "
            "VALUES (?,?,'2026-09-10T09:35:00',100000,?,?,?,?,?,0,?,?,?,?,?)",
            (key, status, fill_status, band_min, band_max, placed_px, ttl, placed_px,
             side, qty, group_key, batch_id))
        c.execute("INSERT OR REPLACE INTO event_slot_members (event_key, stock, weight, "
                  "joined_at) VALUES (?, ?, 1.0, '2026-09-10T09:35:00')", (key, STOCK))
        c.commit()
        c.close()

    def trades(self, rows, account_id=1):
        """rows=[('buy', 1000, 65.4), ('sell', 300, 80.0)] → 段持仓 = buy−sell。"""
        c = sqlite3.connect(self.pool)
        c.execute("DELETE FROM trades WHERE account_id=?", (account_id,))
        for i, (op, q, px) in enumerate(rows):
            c.execute("INSERT INTO trades (account_id, seq, operation, stock_code, quantity, "
                      "price, total_cost, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                      (account_id, i + 1, op, CODE, q, px, q * px, "2026-09-10T10:00:00"))
        c.commit()
        c.close()

    def row(self, key, col):
        c = sqlite3.connect(self.pool)
        r = c.execute(f"SELECT {col} FROM event_slots WHERE event_key=?", (key,)).fetchone()
        c.close()
        return r[0] if r else None


@pytest.fixture
def iso(tmp_path):
    it = Iso(tmp_path)
    saved_env = {k: os.environ.get(k) for k in ("STOCK_ANALYSIS_WORKSPACE", "STOCK_TASKS_DB")}
    saved = (watch_scan.POOL_DB, watch_scan.TASKS_DB, dict(watch_scan._PRICE_CACHE))
    os.environ["STOCK_ANALYSIS_WORKSPACE"] = it.ws
    os.environ["STOCK_TASKS_DB"] = it.tasks
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


@pytest.fixture
def calls():
    """ptrade2 打桩调用记录（断言"不得直调"用）。"""
    box = []

    def _stub(*a, **k):
        box.append(list(a))
        return "✅ 已卖出"

    return box, _stub


def _scan(px, stub=None):
    """跑一拍挂单扫描（现价注入；ptrade2 打桩）。"""
    stub = stub or (lambda *a, **k: "✅ 已卖出")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(watch_scan, "in_price_scan_window", lambda: True)
        mp.setattr(watch_scan, "fetch_price_any", lambda code, *a, **k: px)
        mp.setattr(watch_scan, "ptrade2", stub)
        return watch_scan.check_price_orders()


# ------------------------------------------------------- A3 几何：涨破卖 / 跌破卖
def test_rise_sell_waits_below_and_fires_on_break(iso):
    """涨破卖 band=[15,9.9e9]：现价 12（下方）→ 不动作；现价 16 → sell 出行（按现价成交）。"""
    iso.trades([("buy", 1000, 65.4)])
    iso.slot("ND#rise", 15.0, BIG, qty=333, placed_px=12.0)
    assert _scan(12.0) == [], "现价未到阈值不得动作（也不得弃单）"
    out = _scan(16.0)
    assert len(out) == 1 and "ptrade2 sell" in out[0]
    assert "测试股" in out[0] and "--qty 333" in out[0] and "--price 16.00" in out[0]
    assert "--event-id ND#rise" in out[0]


def test_fall_sell_waits_above_and_fires_on_break(iso):
    """跌破卖 band=[0,8]：现价 9（上方）→ 不动作；现价 7 → sell 出行。"""
    iso.trades([("buy", 1000, 65.4)])
    iso.slot("ND#fall", 0.0, 8.0, qty=333, placed_px=9.0)
    assert _scan(9.0) == []
    out = _scan(7.0)
    assert len(out) == 1 and "--qty 333" in out[0] and "--price 7.00" in out[0]


def test_sell_never_expires_on_adverse_move(iso, calls):
    """D7：反向行情（离阈值越来越远）→ 卖单只等，**永不弃单**（ptrade2 零调用）。"""
    box, stub = calls
    iso.slot("ND#rise", 15.0, BIG, qty=333, placed_px=12.0)
    out = _scan(5.0, stub)
    assert out == []
    assert box == [], f"卖单在反向行情不得直调 expire，实得 {box}"


# ------------------------------------------------------------- A3 三出口其余两个
def test_sell_ttl_returns_to_agent(iso):
    """TTL 到期 → expired 出行（回 agent 重挂）。"""
    iso.slot("ND#ttl", 15.0, BIG, qty=333, ttl=PAST)
    out = _scan(12.0)
    assert len(out) == 1 and "--reason expired" in out[0]


def test_sell_price_fetch_failure_is_fail_closed(iso):
    """取价失败 → 明确跳过行（不成交不弃单）。"""
    iso.slot("ND#nopx", 15.0, BIG, qty=333)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(watch_scan, "in_price_scan_window", lambda: True)
        mp.setattr(watch_scan, "fetch_price_any", lambda code, *a, **k: None)
        mp.setattr(watch_scan, "ptrade2", lambda *a, **k: "✅")
        out = watch_scan.check_price_orders()
    assert len(out) == 1 and "取价失败" in out[0]


# ---------------------------------------------------------- D4 跳空：跨档全部兑现
def test_gap_across_two_levels_fills_both(iso):
    """10→21 跨两档（15/20 各 1/3）：两档全部兑现 + 同拍留痕；只跨一档（16）只成交一张。"""
    iso.trades([("buy", 1000, 65.4)])
    iso.slot("ND#L1", 15.0, BIG, qty=333, placed_px=10.0)
    iso.slot("ND#L2", 20.0, BIG, qty=333, placed_px=10.0)
    out = _scan(21.0)
    sells = [l for l in out if "ptrade2 sell" in l]
    assert len(sells) == 2, f"跳空跨两档应全部兑现，实得 {out}"
    assert sum(int(l.split("--qty ")[1].split()[0]) for l in sells) == 666
    assert any("同拍成交 2 档" in l for l in out), f"缺跨档留痕：{out}"
    # 只跨一档
    out = _scan(16.0)
    sells = [l for l in out if "ptrade2 sell" in l]
    assert len(sells) == 1 and "--event-id ND#L1" in sells[0]


def test_clamp_cumulative_never_exceeds_position(iso):
    """A5：两档各 333 但段持仓只有 400 → 第一张 333、第二张 clamp 到 67 并留痕。"""
    iso.trades([("buy", 400, 65.4)])
    iso.slot("ND#L1", 15.0, BIG, qty=333, placed_px=10.0)
    iso.slot("ND#L2", 20.0, BIG, qty=333, placed_px=10.0)
    out = _scan(21.0)
    sells = [l for l in out if "ptrade2 sell" in l]
    assert "--qty 333" in sells[0] and "--event-id ND#L1" in sells[0]
    assert "--qty 67" in sells[1] and "--event-id ND#L2" in sells[1]
    assert "clamp 333→67" in sells[1], f"clamp 必须留痕：{sells[1]}"


def test_sell_without_qty_is_fail_closed(iso):
    """qty 缺失（比例语义没算成数字）→ fail-closed 出行，不执行。"""
    iso.slot("ND#noqty", 15.0, BIG, qty=None)
    out = _scan(16.0)
    assert len(out) == 1 and "fail-closed" in out[0] and "ptrade2 sell" not in out[0]


# --------------------------------------------------------- D10 组内联动失效
def test_group_invalidation_when_position_exhausted(iso):
    """同组已有成交 + 段持仓归零 → 其余挂单出行 group_closed；重复调用输出一致（幂等）。"""
    iso.trades([("buy", 1000, 65.4), ("sell", 1000, 90.0)])
    iso.slot("ND#g1", 15.0, BIG, qty=333, group_key="测试股:tp", batch_id=1,
             fill_status="filled", status="open")
    iso.slot("ND#g2", 20.0, BIG, qty=333, group_key="测试股:tp", batch_id=1)
    out1 = watch_scan.sync_order_groups()
    out2 = watch_scan.sync_order_groups()
    assert len(out1) == 1 and "ND#g2" in out1[0] and "group_closed" in out1[0]
    assert out1 == out2, "重复检测必须幂等（同一输出）"


def test_group_invalidation_skipped_when_position_remains(iso):
    """段仓位未耗尽 → 阶梯各档独立，不联动失效。"""
    iso.trades([("buy", 1000, 65.4), ("sell", 300, 90.0)])
    iso.slot("ND#g1", 15.0, BIG, qty=333, group_key="测试股:tp", batch_id=1,
             fill_status="filled", status="open")
    iso.slot("ND#g2", 20.0, BIG, qty=333, group_key="测试股:tp", batch_id=1)
    assert watch_scan.sync_order_groups() == []


def test_group_invalidation_spares_newer_batch(iso):
    """batch_id 更大的（刚重挂的新批次）被豁免，不被旧批次联动误伤。"""
    iso.trades([("buy", 1000, 65.4), ("sell", 1000, 90.0)])
    iso.slot("ND#g1", 15.0, BIG, qty=333, group_key="测试股:tp", batch_id=1,
             fill_status="filled", status="open")
    iso.slot("ND#g2", 20.0, BIG, qty=333, group_key="测试股:tp", batch_id=1)
    iso.slot("ND#g3", 22.0, BIG, qty=333, group_key="测试股:tp", batch_id=2)
    out = watch_scan.sync_order_groups()
    assert len(out) == 1 and "ND#g2" in out[0]
    assert not any("ND#g3" in l for l in out), f"新批次不得被误伤：{out}"


def test_group_invalidation_noop_without_filled(iso):
    """组内没有成交单 → 不联动（避免"只是挂在同一组"就互相失效）。"""
    iso.trades([("buy", 1000, 65.4), ("sell", 1000, 90.0)])
    iso.slot("ND#g2", 20.0, BIG, qty=333, group_key="测试股:tp", batch_id=1)
    assert watch_scan.sync_order_groups() == []


# ------------------------------------------------------------- 旧槽回归（买侧不变）
def test_legacy_buy_slot_path_unchanged(iso):
    """side='buy' 双边带走原四态：进带 → sleeve-order-fill 出行（措辞与 v13 一致）。"""
    iso.slot("ND#buy", 10.0, 11.0, side="buy", qty=None, placed_px=10.5)
    out = _scan(10.5)
    assert len(out) == 1
    assert "sleeve-order-fill ND#buy --price 10.50" in out[0]
    assert "ptrade2 sell" not in out[0]
    # placed ∈ band 且 px < band_min → 原样 band_break 直调
    iso2 = iso
    iso2.slot("ND#buy2", 10.0, 11.0, side="buy", qty=None, placed_px=10.5)
    box = []
    out = _scan(9.0, lambda *a, **k: box.append(list(a)) or "✅ 已弃单")
    assert any("band_break" in str(c) for c in box), f"买侧下穿应直调 band_break，实得 {box}"
    assert any("band_break" in l for l in out)
