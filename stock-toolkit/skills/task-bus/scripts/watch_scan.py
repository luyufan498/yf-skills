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
STATE_FILE = "/tmp/watch_scan_state.json"  # 兼容旧文件（已迁移到 kv_store，读时优先 kv）
KV_STATE_KEY = "watch_scan_state"
RECOVER_STALE_HOURS = 2.0
TRADE_START, TRADE_END = "09:30", "15:00"
BUY_WORDS = ("建仓", "买入", "加仓")
SELL_WORDS = ("清仓", "减仓", "止损", "止盈")


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def in_trade_hours() -> bool:
    return TRADE_START <= now_hhmm() <= TRADE_END


def load_state() -> dict:
    """从 kv_store 读状态（数据库持久化，重启不丢）。"""
    if not os.path.exists(TASKS_DB):
        return {}
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key=?", (KV_STATE_KEY,)).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        conn.close()


def save_state(st: dict):
    """状态写入 kv_store（upsert）。"""
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (KV_STATE_KEY, json.dumps(st, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


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
CREATE TABLE IF NOT EXISTS kv_store (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
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
    """待消费事件（排除 CALENDAR：定时回查由 check_calendar 到期才输出，未到期不唤醒）。"""
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
            "WHERE status='pending' AND type != 'CALENDAR' "
            "ORDER BY priority ASC, id DESC LIMIT 30").fetchall()]
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
def _has_pending_event(entity: str, direction: str, cond_id: int | None = None) -> bool:
    """去重：同实体同方向已有 pending/processing 事件则跳过。

    cond_id > 0 时进一步按条件 ID 精确去重（同条件不重复入队），
    防手动补录与自动触发叠加造成重复事件。
    """
    if not os.path.exists(TASKS_DB):
        return False
    conn = sqlite3.connect(TASKS_DB)
    try:
        if cond_id and cond_id > 0:
            row = conn.execute(
                "SELECT 1 FROM task_events WHERE entity=? AND status IN ('pending','processing') "
                "AND type='WATCH_ALERT' AND payload LIKE ? LIMIT 1",
                (entity, f"%\"cond_id\": {cond_id}%"),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM task_events WHERE entity=? AND status IN ('pending','processing') "
                "AND type='WATCH_ALERT' AND payload LIKE ? LIMIT 1",
                (entity, f"%{direction}%"),
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def _cond_active(cond_id: int) -> bool:
    """校验条件仍 active（防对已触发/已移除条件补录事件）。"""
    if not cond_id or cond_id <= 0 or not os.path.exists(POOL_DB):
        return False
    conn = sqlite3.connect(POOL_DB)
    try:
        row = conn.execute(
            "SELECT 1 FROM conditions WHERE id=? AND status='active'", (cond_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _write_alert(entity: str, code: str, direction: str, cond_id: int,
                 cond_name: str, trigger_price: float, current_price: float,
                 manual: bool = False, mode: str = "trade", budget: float | None = None) -> bool:
    """写 WATCH_ALERT 事件 + 原子标记条件为 triggered（触发即失效，防重复进入流程）。

    manual=True（手动补录路径）：仅当条件仍 active 才允许写入，已 triggered/不存在则拒绝，
    返回 False 由调用方记录跳过原因——防对旧条件重复补录（如"买点上沿"昨已触发今又补）。
    mode="eval"（L3 观察窗价格点）：不标记 conditions（无账户条件），payload 带 mode 供消费方区分。
    mode="buy"（L2 建仓点）：同 eval 不碰 conditions，额外带 budget（建仓预算）供消费方 allocate。
    """
    if cond_id and cond_id > 0 and not _cond_active(cond_id):
        print(f"  ⏭ 跳过补录：条件#{cond_id}[{cond_name}] 已非 active（可能已触发/已移除）", file=sys.stderr)
        return False
    if _has_pending_event(entity, direction, cond_id):
        return False  # 已有同向/同条件待处理事件，去重
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        payload = json.dumps({
            "mode": mode, "direction": direction, "cond_id": cond_id, "cond_name": cond_name,
            "trigger_price": trigger_price, "current_price": current_price,
            "budget": budget,
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
    # （仅 trade 模式；eval 模式的价格点由调用方负责移除）
    if mode == "trade" and os.path.exists(POOL_DB) and cond_id and cond_id > 0:
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
    return True


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
        if _has_pending_event(r["stock_name"], direction, r["id"]):
            continue  # 已有同向/同条件待处理事件，去重
        if not _write_alert(r["stock_name"], r["stock_code"], direction, r["id"],
                            action, r["price"], price):
            continue  # 条件已非 active（极端竞态），跳过
        triggers.append(
            f"🔔 {r['stock_name']}({r['stock_code']}) {direction.upper()} 触发: "
            f"现价¥{price:.2f} ≤ 条件¥{r['price']:.2f} [{action}]")
    return triggers


def audit_inconsistencies() -> list[str]:
    """对账：发现已 triggered 条件却仍有 pending/processing 事件的脏数据。

    正常情况下 `_write_alert` 写事件时即原子标记条件 triggered，事件消费完成（done）
    后条件保持 triggered 不复活。若出现"条件已 triggered + 事件仍挂起"，
    说明存在手动补录/消费遗漏，需要告警让 agent 处置，避免下一 tick 重复触发。
    """
    if not os.path.exists(TASKS_DB) or not os.path.exists(POOL_DB):
        return []
    warnings = []
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    pconn = sqlite3.connect(POOL_DB)
    pconn.row_factory = sqlite3.Row  # 必须设，否则 fetchone 返回 tuple 无法按列名取 status
    try:
        rows = conn.execute(
            "SELECT id, entity, payload, status FROM task_events "
            "WHERE type='WATCH_ALERT' AND status IN ('pending','processing')").fetchall()
        for r in rows:
            try:
                payload = json.loads(r["payload"] or "{}")
            except json.JSONDecodeError:
                continue
            cond_id = payload.get("cond_id") or 0
            if cond_id <= 0:
                warnings.append(
                    f"⚠️ 对账：事件#{r['id']} [{r['entity']}] 无有效 cond_id"
                    f"（{payload.get('cond_name','')}），可能为无凭证手动补录，消费前需核验")
                continue
            cond = pconn.execute(
                "SELECT status FROM conditions WHERE id=?", (cond_id,)).fetchone()
            if cond and cond["status"] != "active":
                warnings.append(
                    f"⚠️ 对账：事件#{r['id']} [{r['entity']}] 关联条件#{cond_id}"
                    f"[{payload.get('cond_name','')}] 已 {cond['status']}（非 active），"
                    f"疑似重复触发，消费时不得执行买入/卖出")
    finally:
        conn.close()
        pconn.close()
    return warnings


# ---------- 4. 动量异动扫描（原有） ----------
def pool_stocks() -> list[tuple[str, str]]:
    """池内 active 股票 (名称, code)。code 缺失时从 accounts 表兜底（pool.code 可能为 NULL）。"""
    if not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(POOL_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT p.stock AS stock, COALESCE(p.code, a.stock_code) AS code "
            "FROM pool p LEFT JOIN accounts a ON a.stock_name = p.stock "
            "WHERE p.pool_status='active'").fetchall()
        return [(r["stock"], r["code"]) for r in rows if r["code"]]
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


def _kv_get(key: str) -> dict:
    """通用 kv_store 读取（JSON dict）。"""
    if not os.path.exists(TASKS_DB):
        return {}
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
        if row is None:
            return {}
        v = json.loads(row[0])
        return v if isinstance(v, dict) else {}
    finally:
        conn.close()


def _kv_set(key: str, value: dict):
    """通用 kv_store 写入（upsert）。"""
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def check_calendar() -> list[str]:
    """CALENDAR 事件到期检测：due ≤ 今天 且 pending → 输出（monitor 变化 → 唤醒 agent 消费）。

    分析 agent 对"暂时不买/等财报/等催化"的股票写 CALENDAR 事件（payload.due 日期），
    到期时心跳唤醒重新评估（升级 L2 / 继续观察 / 移除）。未到期不输出（安静睡眠）。
    """
    if not os.path.exists(TASKS_DB):
        return []
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        rows = conn.execute(
            "SELECT id, entity, payload FROM task_events "
            "WHERE type='CALENDAR' AND status='pending'").fetchall()
    finally:
        conn.close()
    today = datetime.now().strftime("%Y-%m-%d")
    out = []
    for rid, entity, payload in rows:
        try:
            p = json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            continue
        due = (p.get("due") or "")[:10]
        if due and due <= today:
            out.append(f"📅 CALENDAR 到期 #{rid} {entity} due={due} [{p.get('event', '')}]")
    return out


def check_watch_points() -> list[str]:
    """价格点检测：现价 ≤ 价格点 → WATCH_ALERT + 移除价格点（触发即失效）。

    价格点由组合审查/分析 agent 用 taskbus watchpoint add 写入 kv_store('watch_points')。
    - mode=eval（L3 观察窗）→ WATCH_ALERT(mode=eval) 唤醒分析 agent 评估升级
    - mode=buy（L2 建仓点）→ WATCH_ALERT(mode=buy, budget=金额) 唤醒核验 → allocate → buy
    """
    if not in_trade_hours() or not os.path.exists(TASKS_DB):
        return []
    points = _kv_get("watch_points")
    if not points:
        return []
    pool = {name: code for name, code in pool_stocks()}
    alerts = []
    changed = False
    for entity, pts in list(points.items()):
        pts_code = next((p.get("code") for p in pts if p.get("code")), None)
        code = pts_code or pool.get(entity)
        if not code:
            continue  # 无 code（未入池且未传 --code），跳过
        price = fetch_price(code)
        if price is None:
            continue
        for p in pts:
            if price <= p["price"]:
                note = p.get("note", "")
                mode = p.get("mode", "eval")
                if mode == "buy":
                    cond_name = f"建仓点-{note}" if note else "建仓点"
                    budget = p.get("amount")
                    if _write_alert(entity, code, "buy", 0, cond_name, p["price"], price,
                                    mode="buy", budget=budget):
                        budget_txt = f" 预算¥{budget:,.0f}" if budget else "（⚠️无预算）"
                        alerts.append(f"🛒 {entity}({code}) L2建仓点触发: 现价¥{price} ≤ ¥{p['price']} "
                                      f"[{note}]{budget_txt} → 唤醒核验建仓")
                        points.pop(entity, None)  # 触发即失效
                        changed = True
                else:
                    cond_name = f"L3观察-{note}" if note else "L3观察"
                    if _write_alert(entity, code, "eval", 0, cond_name, p["price"], price,
                                    mode="eval"):
                        alerts.append(f"📌 {entity}({code}) L3价格点触发: 现价¥{price} ≤ ¥{p['price']} "
                                      f"[{note}] → 唤醒评估升级")
                        points.pop(entity, None)  # 触发即失效
                        changed = True
                break
    if changed:
        _kv_set("watch_points", points)
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
    lines.extend(check_watch_points())
    lines.extend(check_calendar())
    lines.extend(audit_inconsistencies())
    lines.extend(scan_moves())
    if not lines:
        print("IDLE")
        return 0
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
