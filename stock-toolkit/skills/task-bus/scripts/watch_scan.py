#!/usr/bin/env python3
"""watch_scan.py — 心跳监控脚本（Hermes cron monitor-script 模式）

每个 tick（30 分钟，零 LLM 成本）：
1. taskbus pending 事件检查 + processing 超时 recover
2. atr-sync：每日交易时段首次 tick，对持仓股自动更新止损位（减少 agent 工作）
3. 价格条件触发检测（交易时段）：读 conditions active 条件 vs 实时价
   - 买入类（action 含 建仓/买入/加仓）：现价 ≤ 触发价 → WATCH_ALERT(buy)
   - 止损类（hard / action 含 清仓/减仓/止损）：现价 ≤ 触发价 → WATCH_ALERT(sell)
   - 去重：同实体同方向已有 pending/processing → 跳过
4. 动量异动扫描（池内，甜点区/追高/单日异动）

输出契约：无情况 → IDLE（稳定睡眠）；有情况 → 变化摘要（唤醒 agent）。
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

TASKS_DB = os.environ.get("STOCK_TASKS_DB") or os.path.join(os.getcwd(), "data", "tasks", "tasks.db")
WS = os.environ.get("STOCK_ANALYSIS_WORKSPACE", os.path.join(os.getcwd(), ".paper-trading"))
POOL_DB = os.path.join(WS, "master_pool.db")
STATE_FILE = "/tmp/watch_scan_state.json"
RECOVER_STALE_HOURS = 2.0
TRADE_START, TRADE_END = "09:30", "15:00"
BUY_WORDS = ("建仓", "买入", "加仓")
SELL_WORDS = ("清仓", "减仓", "止损", "止盈")


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def in_trade_hours() -> bool:
    return TRADE_START <= now_hhmm() <= TRADE_END


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f)


def ptrade2(*args, timeout=90) -> str:
    env = dict(os.environ)
    env.setdefault("STOCK_ANALYSIS_WORKSPACE", WS)
    try:
        return subprocess.run(["ptrade2", *args], capture_output=True, text=True,
                              timeout=timeout, env=env).stdout
    except Exception:
        return ""


def fetch_price(code: str) -> float | None:
    out = ptrade2("fetch-price", code)
    m = re.search(r"当前价格:\s*¥([\d.]+)", out)
    return float(m.group(1)) if m else None


TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    priority    INTEGER NOT NULL DEFAULT 3,
    source      TEXT,
    entity      TEXT,
    payload     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    claimed_at  TEXT,
    done_at     TEXT,
    note        TEXT
);
"""


def _ensure_task_table():
    """确保任务表存在（脚本独立运行时不依赖 taskbus init）。"""
    os.makedirs(os.path.dirname(TASKS_DB), exist_ok=True)
    conn = sqlite3.connect(TASKS_DB)
    try:
        conn.executescript(TASKS_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------- 1. 任务事件检查 ----------
def check_tasks() -> list[dict]:
    if not os.path.exists(TASKS_DB):
        return []
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "UPDATE task_events SET status='pending', claimed_at=NULL, "
            "note=COALESCE(note,'') || '[recover: 超时重置]' "
            "WHERE status='processing' AND claimed_at IS NOT NULL "
            "AND (julianday('now','localtime') - julianday(claimed_at)) * 24 > ?",
            (RECOVER_STALE_HOURS,),
        )
        conn.commit()
        return [dict(r) for r in conn.execute(
            "SELECT id, type, entity, priority, source FROM task_events "
            "WHERE status='pending' ORDER BY priority ASC, id DESC LIMIT 30").fetchall()]
    finally:
        conn.close()


# ---------- 2. atr-sync 每日维护 ----------
def atr_sync_daily() -> list[str]:
    """交易时段首次 tick：对持仓股（position open）跑 atr-sync 更新止损位。"""
    if not in_trade_hours() or not os.path.exists(POOL_DB):
        return []
    st = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if st.get("last_atr_date") == today:
        return []
    conn = sqlite3.connect(POOL_DB)
    try:
        stocks = [r[0] for r in conn.execute("SELECT stock FROM position WHERE status='open'")]
    finally:
        conn.close()
    if not stocks:
        return []
    done = []
    for s in stocks:
        out = ptrade2("atr-sync", s, timeout=60)
        done.append(f"📐 {s} atr-sync {'✓' if out else '✗'}")
    st["last_atr_date"] = today
    save_state(st)
    return done


# ---------- 3. 价格条件触发检测 ----------
def _has_pending_event(entity: str, direction: str) -> bool:
    """去重：同实体同方向已有 pending/processing 事件则跳过。"""
    if not os.path.exists(TASKS_DB):
        return False
    conn = sqlite3.connect(TASKS_DB)
    try:
        row = conn.execute(
            "SELECT 1 FROM task_events WHERE entity=? AND status IN ('pending','processing') "
            "AND type='WATCH_ALERT' AND payload LIKE ? LIMIT 1",
            (entity, f"%{direction}%"),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _write_alert(entity: str, code: str, direction: str, cond_id: int,
                 cond_name: str, trigger_price: float, current_price: float) -> None:
    """写 WATCH_ALERT 事件 + 原子标记条件为 triggered（触发即失效，防重复进入流程）。"""
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        payload = json.dumps({
            "direction": direction, "cond_id": cond_id, "cond_name": cond_name,
            "trigger_price": trigger_price, "current_price": current_price,
        }, ensure_ascii=False)
        conn.execute(
            "INSERT INTO task_events (type, entity, source, priority, payload) "
            "VALUES ('WATCH_ALERT', ?, 'heartbeat-scan', 1, ?)",
            (entity, payload),
        )
        conn.commit()
    finally:
        conn.close()
    # 触发即失效：把该股票该方向的所有 active 条件标记为 triggered
    # （同组区间捕捉的 下沿/中沿/上沿 一并失效，避免事件消费后剩余条件重复触发）
    if os.path.exists(POOL_DB):
        if direction == "buy":
            cond_filter = ("(action LIKE '%建仓%' OR action LIKE '%买入%' OR action LIKE '%加仓%')")
        else:
            cond_filter = ("(action LIKE '%清仓%' OR action LIKE '%减仓%' OR action LIKE '%止损%' "
                           "OR action LIKE '%止盈%' OR category='hard')")
        pconn = sqlite3.connect(POOL_DB)
        try:
            pconn.execute(
                f"UPDATE conditions SET status='triggered' "
                f"WHERE account_id=(SELECT account_id FROM conditions WHERE id=?) "
                f"AND status='active' AND {cond_filter}",
                (cond_id,),
            )
            pconn.commit()
        finally:
            pconn.close()


def check_price_triggers() -> list[str]:
    """读 active 条件 vs 实时价，穿越触发 → 写 WATCH_ALERT（去重）。"""
    if not in_trade_hours() or not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(POOL_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT cn.id, a.stock_name, a.stock_code, cn.price, cn.action, cn.category, cn.type "
            "FROM conditions cn JOIN accounts a ON cn.account_id=a.id "
            "WHERE cn.status='active' AND cn.price IS NOT NULL").fetchall()
    finally:
        conn.close()
    triggers = []
    for r in rows:
        action = r["action"] or ""
        is_buy = any(w in action for w in BUY_WORDS)
        is_sell = any(w in action for w in SELL_WORDS) or r["category"] == "hard"
        if not (is_buy or is_sell):
            continue
        price = fetch_price(r["stock_code"])
        if price is None:
            continue
        hit = price <= r["price"]
        if not hit:
            continue
        direction = "buy" if is_buy else "sell"
        if _has_pending_event(r["stock_name"], direction):
            continue  # 已有同向待处理事件，去重
        _write_alert(r["stock_name"], r["stock_code"], direction, r["id"],
                     action, r["price"], price)
        triggers.append(
            f"🔔 {r['stock_name']}({r['stock_code']}) {direction.upper()} 触发: "
            f"现价¥{price:.2f} ≤ 条件¥{r['price']:.2f} [{action}]")
    return triggers


# ---------- 4. 动量异动扫描（原有） ----------
def pool_stocks() -> list[tuple[str, str]]:
    if not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(POOL_DB)
    conn.row_factory = sqlite3.Row
    try:
        return [(r["stock"], r["code"]) for r in conn.execute(
            "SELECT stock, code FROM pool WHERE pool_status='active' AND code IS NOT NULL")]
    finally:
        conn.close()


def fetch_kline_closes(code: str) -> list[float]:
    out = ptrade2("fetch-kline", code, "--type", "day", "--count", "15")
    closes = []
    for line in out.splitlines():
        m = re.search(r"收:\s*([\d.]+)", line)
        if m:
            closes.append(float(m.group(1)))
    return closes  # 新→旧


def scan_moves() -> list[str]:
    """池内股票异动扫描（状态机 + 滞回：状态变化才输出，防反复唤醒）。

    每只股票维护 last_state（normal/sweet/chase/daymove）：
    - 状态不变 → 不输出（monitor 哈希稳定 → 睡眠）
    - 状态跃迁 → 输出一次（唤醒 agent 处理）
    - 滞回边界：进入甜点区 15% / 退出 14%；单日异动触发 7% / 复位 6.5%
    """
    if not in_trade_hours():
        return []
    st = load_state()
    states = st.get("move_states", {})  # {name: last_state}
    alerts = []
    for name, code in pool_stocks():
        closes = fetch_kline_closes(code)
        if len(closes) < 11:
            continue
        today, day_ago, ten_ago = closes[0], closes[1], closes[10]
        # round 防浮点精度（14.999999999999991 >= 15 判定失败）
        day_chg = round((today / day_ago - 1) * 100, 2) if day_ago else 0.0
        ten_chg = round((today / ten_ago - 1) * 100, 2) if ten_ago else 0.0

        last = states.get(name, "normal")
        # 滞回判定（进入阈值 > 退出阈值，边界抖动不反复切换）
        if ten_chg > 25:
            cur = "chase"
        elif ten_chg >= 15 or (last == "sweet" and ten_chg >= 14):
            cur = "sweet"
        elif abs(day_chg) >= 7 or (last == "daymove" and abs(day_chg) >= 6.5):
            cur = "daymove"
        else:
            cur = "normal"

        if cur != last:  # 状态跃迁才输出
            if cur == "sweet":
                alerts.append(f"⚡ {name}({code}) 进入动量甜点区 近10日+{ten_chg:.1f}%")
            elif cur == "chase":
                alerts.append(f"⚠️ {name}({code}) 进入追高区 近10日+{ten_chg:.1f}% (>25%)")
            elif cur == "daymove":
                alerts.append(f"🔔 {name}({code}) 单日{day_chg:+.1f}% 异动")
            elif last != "normal" and cur == "normal":
                alerts.append(f"↩️ {name}({code}) 异动回落（{last}→正常）")
        states[name] = cur
    st["move_states"] = states
    save_state(st)
    return alerts


def main() -> int:
    lines = []
    tasks = check_tasks()
    if tasks:
        latest = tasks[0]
        lines.append(f"[EVENT] pending={len(tasks)} 个 | 最新 #{latest['id']} "
                     f"[{latest['type']}] {latest['entity']} (p{latest['priority']})")
        for t in tasks:
            lines.append(f"  #{t['id']} [{t['type']}] {t['entity']} p{t['priority']} src={t['source']}")
    lines.extend(atr_sync_daily())
    lines.extend(check_price_triggers())
    lines.extend(scan_moves())
    if not lines:
        print("IDLE")
        return 0
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
