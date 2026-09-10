"""任务书 F1 检验 ②④⑤⑦ 回归锁（条件/挂单执行链 v3，2026-09-10）。

跑法（隔离；零生产库接触）：
    cd stock-toolkit/skills/task-bus/scripts && \
    <paper-trading venv>/bin/python3 -m pytest tests/test_v13_checks.py -q

隔离设计：pytest tmp_path 临时 workspace，watch_scan.POOL_DB/TASKS_DB 指向 tmp 库；行情与
ptrade2 全打桩（零触网、零真实下单）；⑤ 走真独立子进程且子进程 env 钉在 tmp 库。

覆盖：
- ② 失败码四类回放：(条件终态, exec.result, 事件 status, handled_at) 真值表 + deferred_agent 语义。
- ④ WP5 已消费点不得再触发：consumed 后行保留、同价再跑不产生第二条事件、改回 active 可再触发。
- ⑤ 并发压测：2 独立进程 × N 次 wp_add/wp_remove + wp_list 轮询：零 'database is locked'、
  行数精确、wp_id 无重复。
- ⑦ 节拍竞态：同刻并发命中同一条件只应 1 条事件 + 1 次 sell 直调；check_price_orders 三态并发成立。

⚠️ 已知（潜在）缺陷（⑦，本文件以 xfail 锁定）：
   去重非原子——_has_pending_event 只读检查 与 _write_alert 的 INSERT 不在同一事务
   （_mark_triggered_family 亦在 INSERT 之后）→ 两侧都判"无 pending 事件"。
   实测（两个并发调用 check_price_triggers）：真两进程忙等同刻起跑 **20/20 轮** 写 2 条
   WATCH_ALERT + 2 次 sell 直调（其中 18/20 轮两条直调携带**不同 event_id** → trades 层按
   event_id 的幂等键拦不住）；进程内两线程 **20/20 轮**双事件双直调（14/20 不同 id）。
   最小复现：/tmp/f1_race_proc.py 20（真两进程，忙等同刻起跑）、/tmp/f1_race_probe.py 20。
   **当前生产暴露面有限（潜在而非在燃）**：卖出链唯一消费者 = stock-price-watch 心跳
   （watch_scan_price.py → watch_scan.check_price_triggers）；check-open 跑 tech_watch_scan.py
   （不触卖出链）；同一 job 的重叠被 Hermes 调度器 try_register_running_job 短路；recover 只是
   一条 in-band UPDATE（stale processing → pending，跑在调用者进程内），不是独立线程。
   触发条件 = 出现**第二个并发调用者**（人工/agent 手跑 watch_scan_price.py、未来新增第二个
   价格扫描 job、或绕过调度器守卫的重复实例）。
   修复（INSERT ... ON CONFLICT 部分唯一索引 / BEGIN IMMEDIATE 单事务去重）后本用例转 XPASS，届时删 xfail。
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER_SCRIPTS = ("/home/catmouse/Github_Project/yf-skills/stock-toolkit/"
                 "skills/paper-trading/scripts")
VENV_PY = os.path.join(PAPER_SCRIPTS, ".venv/bin/python3")
sys.path.insert(0, SCRIPTS_DIR)

import watch_scan  # noqa: E402
import task_bus.db as tdb  # noqa: E402

POOL_DDL = """
CREATE TABLE IF NOT EXISTS conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, cond_key TEXT,
    is_event INTEGER DEFAULT 0, cond_uid TEXT, type TEXT, name TEXT, price REAL,
    action TEXT, category TEXT, expiry_date TEXT, status TEXT,
    auto_link_cost INTEGER DEFAULT 0, peak_price REAL, created_at TEXT,
    modified_at TEXT, seq INTEGER, created_by TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS condition_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, condition_id INTEGER, old_price REAL,
    new_price REAL, reason TEXT, timestamp TEXT, level TEXT, override_triggers TEXT);
CREATE TABLE IF NOT EXISTS position (
    id INTEGER PRIMARY KEY, stock TEXT, code TEXT, strategy TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS pool (
    stock TEXT, code TEXT, strategy TEXT, pool_status TEXT, pin INTEGER DEFAULT 0);
"""

SLOT_DDL = """
CREATE TABLE IF NOT EXISTS event_slots (
    event_key TEXT PRIMARY KEY, status TEXT, opened_at TEXT, closed_at TEXT, budget REAL,
    realized REAL, news_kind TEXT, title TEXT, members_json TEXT, invalidation TEXT,
    topup_locked INTEGER, orig_budget REAL, migrated_at TEXT, migrated_stock TEXT,
    fill_status TEXT, fill_at TEXT, note TEXT, band_min REAL, band_max REAL,
    anchor_price REAL, order_ttl TEXT, order_id TEXT, rejudge_count INTEGER,
    created_by TEXT, placed_px REAL, band_out_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS event_slot_members (
    event_key TEXT, stock TEXT, weight REAL, joined_at TEXT, exited_at TEXT, migrated_at TEXT);
CREATE TABLE IF NOT EXISTS position (
    id INTEGER PRIMARY KEY, stock TEXT, code TEXT, strategy TEXT, status TEXT);
"""

COND_SQL = ("INSERT INTO conditions (account_id, cond_uid, type, name, price, action, "
            "category, status, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,'hard','active',?, '2026-09-10T10:00:00')")

# ⑦ 同拍直调用例：cost_protection 条件 9.0，现价 8.80 ≤ 9.0 且 action=清仓 → 涨破/跌破触发
SELL_COND = (1, "uid-race", "cost_protection", "成本保护", 9.0, "清仓", "analysis-watch")


class Iso:
    """隔离 workspace：tmp 库 + 环境变量钉定（防 ptrade2-db-path-trap 落回生产库）。"""

    def __init__(self, root):
        self.root = str(root)
        self.ws = os.path.join(self.root, "ws")
        self.pool = os.path.join(self.ws, ".paper-trading", "master_pool.db")
        self.tasks = os.path.join(self.ws, "data", "tasks", "tasks.db")
        os.makedirs(os.path.dirname(self.tasks), exist_ok=True)
        os.makedirs(os.path.dirname(self.pool), exist_ok=True)

    def connect(self, path, row=True):
        c = sqlite3.connect(path)
        if row:
            c.row_factory = sqlite3.Row
        return c

    def seed_pool(self, conds=(SELL_COND,), slot_mode=False):
        c = self.connect(self.pool)
        c.executescript(SLOT_DDL if slot_mode else POOL_DDL)
        c.execute("DELETE FROM conditions")
        if not slot_mode:
            c.execute("DELETE FROM position")
            c.execute("DELETE FROM pool")
        for rec in conds:
            c.execute(COND_SQL, rec)
        if slot_mode:
            # 三槽同带 [10,11]（立即型 placed_px=10.5）：in 带内 / above 上穿 / below 下穿
            for ek in ("ND#in", "ND#above", "ND#below"):
                c.execute("INSERT INTO event_slots (event_key, status, opened_at, budget, "
                          "fill_status, band_min, band_max, anchor_price, order_ttl, "
                          "band_out_count, placed_px) VALUES (?, 'pending_order', "
                          "'2026-09-10T09:35:00', 100000, 'pending', 10.0, 11.0, 10.5, "
                          "'2026-12-31T15:00:00', 0, 10.5)", (ek,))
                c.execute("INSERT INTO event_slot_members (event_key, stock, weight, joined_at) "
                          "VALUES (?, '测试股', 1.0, '2026-09-10T09:35:00')", (ek,))
            c.execute("INSERT OR REPLACE INTO position (id, stock, code, strategy, status) "
                      "VALUES (1, '测试股', 'sh600000', 'NEWS', 'open')")
        else:
            # 有持仓（no_position 之外的三类码都需要它）
            c.execute("INSERT OR REPLACE INTO position (id, stock, code, strategy, status) "
                      "VALUES (1, '测试股', 'sh600000', 'L1', 'open')")
            c.execute("INSERT OR REPLACE INTO pool (stock, code, strategy, pool_status) "
                      "VALUES ('测试股', 'sh600000', 'L1', 'active')")
        c.commit()
        c.close()

    def events(self):
        c = self.connect(self.tasks)
        rows = [dict(r) for r in c.execute(
            "SELECT id, status, creator, payload, handled_at FROM task_events "
            "WHERE type='WATCH_ALERT' ORDER BY id")]
        c.close()
        return rows

    def cond_status(self, uid):
        c = self.connect(self.pool)
        r = c.execute("SELECT status FROM conditions WHERE cond_uid=?", (uid,)).fetchone()
        c.close()
        return r["status"] if r else None


@pytest.fixture
def iso(tmp_path):
    iso = Iso(tmp_path)
    saved_env = {k: os.environ.get(k) for k in
                 ("STOCK_ANALYSIS_WORKSPACE", "STOCK_TASKS_DB")}
    saved_glob = (watch_scan.POOL_DB, watch_scan.TASKS_DB, dict(watch_scan._PRICE_CACHE))
    os.environ["STOCK_ANALYSIS_WORKSPACE"] = iso.ws
    os.environ["STOCK_TASKS_DB"] = iso.tasks
    watch_scan.POOL_DB = iso.pool
    watch_scan.TASKS_DB = iso.tasks
    watch_scan._PRICE_CACHE.clear()
    iso.seed_pool(conds=())
    watch_scan._ensure_task_table()
    yield iso
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    watch_scan.POOL_DB, watch_scan.TASKS_DB, _ = saved_glob
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update(saved_glob[2])


def _ev_of(events, uid):
    for e in events:
        if json.loads(e["payload"] or "{}").get("cond_uid") == uid:
            return e
    return None


# ---------------------------------------------------------------- ② 失败码四类回放
FAILCODE_CASES = [
    ("stale_quote", "❌ E3 行情防线拒绝按检测价成交：报价陈旧（>5min）", 1,
     {"cond": "active", "exec_result": "failed", "ev_status": "pending"}),
    ("stale_quote", "❌ E3 行情防线拒绝按检测价成交：报价陈旧（>5min）", 3,
     {"cond": "active", "exec_result": "failed", "ev_status": "pending"}),
    ("insufficient_funds", "❌ 资金不足。需要：¥900，可用：¥100，缺口：¥800", 3,
     {"cond": "suspended", "exec_result": "failed", "ev_status": "failed"}),
    ("no_position", "❌ 当前无持仓", 1,
     {"cond": "archived", "exec_result": "done", "ev_status": "done"}),
    ("error", "（超时）", 3,   # 非空且非 ✅ → error 码
     {"cond": "active", "exec_result": "failed", "ev_status": "failed"}),
]


@pytest.mark.parametrize("code,stub_out,ticks,expect", FAILCODE_CASES)
def test_check2_failure_code_roundtrip(iso, code, stub_out, ticks, expect):
    """四类失败码回放：条件终态 / exec.result / 事件 status / handled_at 必须一致。"""
    uid = f"uid-{code}"
    iso.seed_pool(conds=[(1, uid, "cost_protection", "成本保护", 9.0, "清仓",
                          "analysis-watch")])
    watch_scan.POOL_DB = iso.pool
    watch_scan.TASKS_DB = iso.tasks
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update({"sh600000": 8.80})
    old_win = watch_scan.COND_EXEC_TICK_WINDOW
    watch_scan.COND_EXEC_TICK_WINDOW = 0.0 if ticks > 1 else 600.0
    try:
        with patch.object(watch_scan, "ptrade2", lambda *a, **k: stub_out), \
             patch.object(watch_scan, "fetch_price", lambda code: 8.80), \
             patch.object(watch_scan, "in_trade_hours", lambda: True):
            for _ in range(ticks):
                watch_scan.check_price_triggers()
    finally:
        watch_scan.COND_EXEC_TICK_WINDOW = old_win

    ev = _ev_of(iso.events(), uid)
    assert ev is not None, f"{code}: 未产出 WATCH_ALERT 事件"
    ex = (json.loads(ev["payload"] or "{}").get("exec") or {})
    got = {"cond": iso.cond_status(uid), "exec_result": ex.get("result"),
           "ev_status": ev["status"]}
    assert ev["handled_at"] is None, "执行方不得写 handled_at（消费方才标记）"
    assert got == expect, f"{code}: 实得 {got}，期望 {expect}"


def test_check2_deferred_agent_keeps_event_pending(iso):
    """1/3 止盈语义 → exec.result=deferred_agent，事件保持 pending，等 C1 接管。"""
    uid = "uid-defer"
    iso.seed_pool(conds=[(1, uid, "take_profit_1", "分批止盈①+30%卖1/3", 8.0, "分批止盈①",
                          "analysis-watch")])
    watch_scan.POOL_DB, watch_scan.TASKS_DB = iso.pool, iso.tasks
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update({"sh600000": 8.80})
    with patch.object(watch_scan, "ptrade2",
                      lambda *a, **k: "✅ 已卖出 1/3"), \
         patch.object(watch_scan, "fetch_price", lambda code: 8.80), \
         patch.object(watch_scan, "in_trade_hours", lambda: True):
        watch_scan.check_price_triggers()
    ev = _ev_of(iso.events(), uid)
    assert ev is not None
    ex = (json.loads(ev["payload"] or "{}").get("exec") or {})
    assert ex.get("result") == "deferred_agent", f"实得 {ex}"
    assert ev["status"] == "pending" and ev["handled_at"] is None


# ------------------------------------------------- ④ WP5 已消费点不得再触发
def test_check4_consumed_watchpoint_not_retriggered(iso):
    """WP5：消费过的点不得再触发——consumed 行保留、同价再跑不产生第二条事件；
    改回 active 能再次触发（证明是状态列在工作，不是巧合）。"""
    n = tdb.migrate_watch_points()
    assert n >= 0
    tdb.wp_insert("测试卖股", 12.0, mode="sell", code="sh600000",
                  note="带内限价卖", created_by="analysis-watch")
    c = iso.connect(iso.tasks)
    row = dict(c.execute("SELECT wp_id, entity, price, mode, code FROM watch_points "
                         "WHERE status='active' AND mode='sell' AND code IS NOT NULL "
                         "LIMIT 1").fetchone())
    c.close()
    assert row["entity"] == "测试卖股"

    def run_scan():
        with patch.object(watch_scan, "in_trade_hours", lambda: True), \
             patch.object(watch_scan, "fetch_price", lambda code: 12.2), \
             patch.object(watch_scan, "pool_stocks", lambda: [("测试股", "sh600000")]), \
             patch.object(watch_scan, "ptrade2", lambda *a, **k: "✅ 已卖出"):
            return watch_scan.check_watch_points()

    def wp_row():
        c = iso.connect(iso.tasks)
        r = dict(c.execute("SELECT * FROM watch_points WHERE wp_id=?",
                           (row["wp_id"],)).fetchone())
        c.close()
        return r

    alerts1 = run_scan()
    r1 = wp_row()
    assert r1["status"] == "consumed", f"消费后应 consumed，实得 {r1['status']}"
    assert r1["consumed_at"] and r1["trigger_event_id"], f"消费戳未写全: {r1}"
    assert alerts1, "首次命中应有告警"

    def ev_n():
        c = iso.connect(iso.tasks)
        n = c.execute("SELECT COUNT(*) FROM task_events WHERE type='WATCH_ALERT' "
                      "AND entity=?", (row["entity"],)).fetchone()[0]
        c.close()
        return n

    n_before = ev_n()
    run_scan()
    r2 = wp_row()
    assert r2["status"] == "consumed", "已消费点不得复活"
    assert r2["consumed_at"] == r1["consumed_at"], "重复扫描不得刷新消费戳"
    assert ev_n() == n_before, f"已消费点不得再产出事件（{n_before} → {ev_n()}）"

    # 附加：消费事件 done（窗口释放）+ 手工改回 active → 应能再次触发
    c = iso.connect(iso.tasks)
    c.execute("UPDATE task_events SET status='done' WHERE id=?", (r2["trigger_event_id"],))
    c.execute("UPDATE watch_points SET status='active' WHERE wp_id=?", (row["wp_id"],))
    c.commit()
    c.close()
    alerts3 = run_scan()
    assert alerts3, "改回 active 后应再次触发"
    assert ev_n() == n_before + 1, f"应 +1 条事件，实得 {n_before} → {ev_n()}"


# ------------------------------------------------------------- ⑤ 并发压测
WORKER_TMPL = '''
import json, os, sqlite3, sys, time
sys.path.insert(0, "@@TB@@")
os.environ["STOCK_ANALYSIS_WORKSPACE"] = "@@ISO@@"
os.environ["STOCK_TASKS_DB"] = "@@TK@@"
import task_bus.db as tdb

N = @@N@@
role = sys.argv[1]
done_flag = sys.argv[2]
lock_err = other_err = 0
first = ""


def rec(e):
    global lock_err, other_err, first
    if "locked" in str(e).lower():
        lock_err += 1
    else:
        other_err += 1
        if not first:
            first = type(e).__name__ + ": " + str(e)


if role == "list":
    iters = 0
    t0 = time.time()
    while time.time() - t0 < @@CAP@@:
        try:
            tdb.wp_list(active_only=False)
            iters += 1
        except Exception as e:
            rec(e)
        if os.path.exists(done_flag):
            for _ in range(50):
                try:
                    tdb.wp_list(active_only=False)
                    iters += 1
                except Exception as e:
                    rec(e)
            break
        time.sleep(0.005)
    print(json.dumps({"role": "lister", "iters": iters, "lock": lock_err,
                      "other": other_err, "first": first}))
    sys.exit(0)

ops = 0
for i in range(N):
    try:
        tdb.wp_insert("压测股" + role, 10.0 + (i % 50), mode="eval", code="sh600000",
                      note=role + "-" + str(i),
                      added_at="%02d-%02d:%02d" % ((i // 1440) % 28 + 1,
                                                   (i % 1440) // 60, i % 60))
        tdb.wp_remove("压测股" + role)
        ops += 1
    except Exception as e:
        rec(e)
open(done_flag, "w").write(role)
print(json.dumps({"role": "writer" + role, "ops": ops, "lock": lock_err,
                  "other": other_err, "first": first}))
'''


def test_check5_concurrent_writers_no_lock_and_no_loss(iso, tmp_path):
    """防抢事件：2 独立进程 × N 次 wp_add/wp_remove + wp_list 轮询——零 database is locked、
    最终行数精确、wp_id 无重复（真跨进程，非线程近似）。"""
    N = 2500
    base = os.path.join(iso.root, "conc")
    os.makedirs(base, exist_ok=True)
    tk_c = os.path.join(base, "tasks.db")
    import shutil
    shutil.copy2(iso.tasks, tk_c)
    flag_a = os.path.join(base, "doneA.flag")
    flag_b = os.path.join(base, "doneB.flag")
    worker = os.path.join(base, "worker.py")
    with open(worker, "w") as f:
        f.write(WORKER_TMPL.replace("@@TB@@", SCRIPTS_DIR)
                .replace("@@ISO@@", iso.ws).replace("@@TK@@", tk_c)
                .replace("@@N@@", str(N)).replace("@@CAP@@", "180"))
    env = dict(os.environ)
    env["STOCK_ANALYSIS_WORKSPACE"] = iso.ws
    env["STOCK_TASKS_DB"] = tk_c
    t0 = time.time()
    procs = {
        "A": subprocess.Popen([VENV_PY, worker, "A", flag_a], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
        "B": subprocess.Popen([VENV_PY, worker, "B", flag_b], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
        "list": subprocess.Popen([VENV_PY, worker, "list", flag_a], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
    }
    res = {}
    for name, p in procs.items():
        so, se = p.communicate(timeout=600)
        d = None
        for ln in (so or "").splitlines():
            if ln.startswith("{"):
                d = json.loads(ln)
        assert d is not None, f"{name} 子进程无输出; stderr={se[-400:]}"
        res[d["role"]] = d
    dur = time.time() - t0
    locks = sum(v["lock"] for v in res.values())
    others = sum(v["other"] for v in res.values())
    assert locks == 0, f"出现 {locks} 次 'database is locked'：{res}"
    assert others == 0, f"非锁异常：{res}"
    c = sqlite3.connect(tk_c)
    rows_a = c.execute("SELECT COUNT(*) FROM watch_points WHERE entity='压测股A'").fetchone()[0]
    rows_b = c.execute("SELECT COUNT(*) FROM watch_points WHERE entity='压测股B'").fetchone()[0]
    dup = c.execute("SELECT COUNT(*) FROM (SELECT wp_id FROM watch_points "
                    "GROUP BY wp_id HAVING COUNT(*) > 1)").fetchone()[0]
    c.close()
    assert rows_a == N, f"writerA 行数 {rows_a} != {N}（丢写）"
    assert rows_b == N, f"writerB 行数 {rows_b} != {N}（丢写）"
    assert dup == 0, f"wp_id 重复 {dup} 组"
    print(f"[⑤] {dur:.0f}s N={N} lock=0 other=0 lister_iters={res['lister']['iters']}")


# ------------------------------------------------------------- ⑦ 节拍竞态
def test_check7_same_tick_dual_scan_single_event_single_sell(iso):
    """⑦ 主检验：同一条件被两个并发调用者同刻命中 → 只应写 1 条事件、只应直调 1 次 sell。

    ⚠️ 当前为 **xfail（已复现，潜在缺陷）**：去重是 check-then-act，两侧都判"无 pending 事件"。
    证据：真两进程忙等同刻起跑 20/20 轮双事件 + 双直调（18/20 轮不同 event_id）；
    进程内两线程 20/20 轮（14/20 不同 id）。生产当前只有 price-watch 一个卖出消费者且同 job
    重叠被调度器短路 → 触发需第二个并发调用者（见模块头注释）。
    """
    iso.seed_pool()
    watch_scan.POOL_DB, watch_scan.TASKS_DB = iso.pool, iso.tasks
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update({"sh600000": 8.80})
    calls, lock, errs = [], threading.Lock(), []

    def stub(*a, **k):
        with lock:
            calls.append([str(x) for x in a])
        return "✅ 已卖出"

    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        try:
            watch_scan.check_price_triggers()
        except Exception as e:  # noqa: BLE001
            errs.append(f"{type(e).__name__}: {e}")

    ths = [threading.Thread(target=run) for _ in range(2)]
    # patch 必须父作用域一次性生效：线程内各自 patch.object 时后进线程把前者 mock 当"原值"
    # 保存，退出时互相 restore → mock 中途失效（伪失败/伪通过）。
    with patch.object(watch_scan, "ptrade2", stub), \
         patch.object(watch_scan, "fetch_price", lambda code: 8.80), \
         patch.object(watch_scan, "in_trade_hours", lambda: True):
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    assert not errs, errs
    sells = [c for c in calls if c and c[0] == "sell"]
    evs = iso.events()
    assert len(evs) == 1, f"同槽只应写 1 条事件，实得 {len(evs)} 条（去重非原子）"
    assert len(sells) == 1, f"同刻双跑直调 sell {len(sells)} 次（期望 1，超卖风险）"


xfail_same_tick = pytest.mark.xfail(
    reason="已复现（潜在）：两个并发调用同刻命中时去重非原子（真两进程 20/20 双事件+双直调，"
           "18/20 双 event_id；进程内 20/20）→ /tmp/f1_race_proc.py 20、/tmp/f1_race_probe.py 20",
    strict=False)
test_check7_same_tick_dual_scan_single_event_single_sell = xfail_same_tick(
    test_check7_same_tick_dual_scan_single_event_single_sell)


def test_check7_sequential_replay_after_first_beat_no_extra_sell(iso):
    """⑦ 安全网：第一拍完成后**顺序**重放（recover 重跑）不得再直调——已验证成立，
    是当前生产不双卖的兜底（同 event_id 落库 → trades 层幂等键拒）。"""
    iso.seed_pool()
    watch_scan.POOL_DB, watch_scan.TASKS_DB = iso.pool, iso.tasks
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update({"sh600000": 8.80})
    calls = []

    def stub(*a, **k):
        calls.append([str(x) for x in a])
        return "✅ 已卖出"

    with patch.object(watch_scan, "ptrade2", stub), \
         patch.object(watch_scan, "fetch_price", lambda code: 8.80), \
         patch.object(watch_scan, "in_trade_hours", lambda: True):
        first = watch_scan.check_price_triggers()
        n1 = len([c for c in calls if c[0] == "sell"])
        second = watch_scan.check_price_triggers()
        n2 = len([c for c in calls if c[0] == "sell"])
    assert n1 == 1, f"第一拍应直调 1 次，实得 {n1}；lines={first}"
    assert n2 == n1, f"顺序重放不得再直调（{n1} → {n2}）；lines={second}"
    assert len(iso.events()) == 1, "顺序重放不得写第二条事件"


def test_check7_price_orders_three_states_under_race(iso):
    """⑦ 附加：挂单三态在并发下成立——带内 → fill 行；上穿 → 无动作；
    下穿 → band_break 弃单（条件 UPDATE 守卫：并发双跑只一方生效，状态不越界）。"""
    iso.seed_pool(conds=(), slot_mode=True)
    watch_scan.POOL_DB = iso.pool
    code_map = {"ND#in": "sh600000", "ND#above": "sh600001", "ND#below": "sh600002"}
    px_map = {"sh600000": 10.5, "sh600001": 11.8, "sh600002": 9.5}
    calls, lock = [], threading.Lock()

    def stub(*a, **k):
        with lock:
            calls.append([str(x) for x in a])
        if a and a[0] == "sleeve-order-expire":
            c = sqlite3.connect(iso.pool)
            cur = c.execute("UPDATE event_slots SET status='pending_rejudge' "
                            "WHERE event_key=? AND status='pending_order'", (a[1],))
            n = cur.rowcount
            c.commit()
            c.close()
            return "✅ 已弃单" if n else "已处理"
        return "✅ 已挂单"

    barrier = threading.Barrier(2)
    outs = []

    def run():
        barrier.wait()
        outs.append(watch_scan.check_price_orders())

    watch_scan._PRICE_CACHE.clear()
    ths = [threading.Thread(target=run) for _ in range(2)]
    with patch.object(watch_scan, "_slot_member_code", lambda ek: code_map.get(ek)), \
         patch.object(watch_scan, "fetch_price_any", lambda code: px_map.get(code)), \
         patch.object(watch_scan, "in_price_scan_window", lambda: True), \
         patch.object(watch_scan, "ptrade2", stub):
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    assert len(outs) == 2, f"两拍都应有输出，实得 {len(outs)}"
    fills = [len([ln for ln in o if "sleeve-order-fill" in ln]) for o in outs]
    above = [len([ln for ln in o if "ND#above" in ln]) for o in outs]
    breaks = [len([ln for ln in o if "band_break" in ln]) for o in outs]
    assert fills == [1, 1], f"每拍应各出 1 条 fill 行，实得 {fills}：{outs}"
    assert above == [0, 0], f"上穿槽不得产生任何行，实得 {above}"
    assert all(b >= 1 for b in breaks), f"下穿槽每拍都应有 band_break 行，实得 {breaks}"
    c = sqlite3.connect(iso.pool)
    st = dict(c.execute("SELECT event_key, status FROM event_slots").fetchall())
    c.close()
    assert st["ND#in"] == "pending_order", f"带内槽不得被弃单：{st}"
    assert st["ND#above"] == "pending_order", f"上穿槽不得被弃单：{st}"
    assert st["ND#below"] == "pending_rejudge", f"下穿槽应弃单到 pending_rejudge：{st}"


# --------------------------------------------- ⑦ Phase 0：整拍互斥锁（真两进程）
LOCK_CHILD = '''
import json, os, sys, time
sys.path.insert(0, "@@TB@@")
os.environ["STOCK_ANALYSIS_WORKSPACE"] = "@@ISO@@"
os.environ["STOCK_TASKS_DB"] = "@@TK@@"
os.environ["WATCH_SCAN_LOCK_DIR"] = "@@ISO@@"
from unittest.mock import patch
import watch_scan
watch_scan.TASKS_DB = "@@TK@@"
watch_scan.POOL_DB = "@@MP@@"
watch_scan._ensure_task_table()
watch_scan._PRICE_CACHE.clear()
start = float(sys.argv[1])
while time.time() < start:      # 忙等同一绝对时刻，制造最强同刻竞态
    pass
if watch_scan.acquire_scan_lock("price") is None:
    print(json.dumps({"role": "skipped"}))
    sys.exit(0)
calls = []
def stub(*a, **k):
    calls.append(list(a))
    return "✅ 已卖出"
with patch.object(watch_scan, "ptrade2", stub), \
     patch.object(watch_scan, "in_trade_hours", lambda: True), \
     patch.object(watch_scan, "fetch_price", lambda code: 8.80):
    watch_scan.check_price_triggers()
print(json.dumps({"role": "ran", "calls": calls}))
'''

HOLD_CHILD = '''
import os, sys, time
sys.path.insert(0, "@@TB@@")
os.environ["WATCH_SCAN_LOCK_DIR"] = "@@LD@@"
import watch_scan
print("acquired" if watch_scan.acquire_scan_lock("price") else "denied", flush=True)
time.sleep(float(sys.argv[1]))
'''


def test_check7_scan_lock_excludes_and_releases_on_kill(tmp_path):
    """Phase 0 锁语义：同 scope 第二进程取不到；持锁进程被 SIGKILL 后自动释放（无锁泄露）。"""
    import signal
    ld = str(tmp_path / "locks")
    hold = HOLD_CHILD.replace("@@TB@@", SCRIPTS_DIR).replace("@@LD@@", ld)
    p1 = subprocess.Popen([VENV_PY, "-c", hold, "3"], stdout=subprocess.PIPE, text=True)
    assert p1.stdout.readline().strip() == "acquired", "第一个进程应取到锁"
    p2 = subprocess.run([VENV_PY, "-c", hold, "0"], capture_output=True, text=True,
                        timeout=60)
    assert p2.stdout.strip() == "denied", f"第二进程应被拒（实得 {p2.stdout.strip()!r}）"
    os.kill(p1.pid, signal.SIGKILL)
    p1.wait()
    time.sleep(0.3)
    p3 = subprocess.run([VENV_PY, "-c", hold, "0"], capture_output=True, text=True,
                        timeout=60)
    assert p3.stdout.strip() == "acquired", "持锁进程死亡后锁应已释放（内核回收）"


def test_check7_two_processes_same_instant_single_sell(iso):
    """⑦ Phase 0 真两进程验收：同刻只允许一拍执行 → 恰好 1 次 sell 直调、1 条新事件，
    另一拍整体跳过（模拟生产 monitor 的 IDLE 拍）。"""
    import shutil
    iso.seed_pool()
    tk = os.path.join(iso.root, "shared_tasks.db")
    shutil.copy2(iso.tasks, tk)
    import watch_scan as _ws
    _ws.TASKS_DB = tk
    _ws._ensure_task_table()
    n0 = sqlite3.connect(tk).execute(
        "SELECT COUNT(*) FROM task_events WHERE type='WATCH_ALERT'").fetchone()[0]
    child = (LOCK_CHILD.replace("@@TB@@", SCRIPTS_DIR).replace("@@ISO@@", iso.root)
             .replace("@@TK@@", tk).replace("@@MP@@", iso.pool))
    env = dict(os.environ)
    env.update({"STOCK_ANALYSIS_WORKSPACE": iso.root, "STOCK_TASKS_DB": tk,
                "WATCH_SCAN_LOCK_DIR": iso.root})
    start = time.time() + 2.5
    procs = [subprocess.Popen([VENV_PY, "-c", child, repr(start)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(2)]
    outs = []
    for p in procs:
        so, se = p.communicate(timeout=180)
        d = None
        for ln in (so or "").splitlines():
            if ln.startswith("{"):
                d = json.loads(ln)
        assert d is not None, f"子进程无输出; stderr={se[-400:]}"
        outs.append(d)
    sells = [c for o in outs for c in o.get("calls", []) if c and c[0] == "sell"]
    n1 = sqlite3.connect(tk).execute(
        "SELECT COUNT(*) FROM task_events WHERE type='WATCH_ALERT'").fetchone()[0]
    n_skip = len([o for o in outs if o["role"] == "skipped"])
    assert len(sells) == 1, f"同刻只应直调 1 次 sell，实得 {len(sells)}"
    assert n1 - n0 == 1, f"只应新增 1 条事件，实得 {n1 - n0}"
    assert n_skip == 1, f"应恰好一拍跳过，实得 {n_skip}"
