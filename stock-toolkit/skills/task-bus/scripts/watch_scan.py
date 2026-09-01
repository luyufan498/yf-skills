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
# A股节假日（休市日）——2026 年已知，后续年份需更新
MARKET_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-04-06", "2026-05-01", "2026-05-04",
    "2026-05-05", "2026-06-19", "2026-09-25", "2026-10-01", "2026-10-02",
    "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
}

# 交易日历单一真源：工作区 data/trading_calendar.json（上交所休市安排，2026-08-27 接入）
STOCK_WS_ROOT = os.environ.get("STOCK_ANALYSIS_WORKSPACE_ROOT",
                               "/home/catmouse/Github_Project/daily-stock-workspace")
if STOCK_WS_ROOT not in sys.path:
    sys.path.insert(0, STOCK_WS_ROOT)


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def is_trading_day(d: datetime | None = None) -> bool:
    """判断是否 A 股交易日：非周末 + 非节假日。

    主路径：工作区 trading_calendar.py（data/trading_calendar.json 单一真源，
    含周末判断 + 节假日 + 临时休市补丁）。
    fallback：JSON/模块不可用时退回本文件旧表（周末必休 + 表内必休）。
    """
    if d is None:
        d = datetime.now()
    try:
        from trading_calendar import is_trading_day as _cal_day
        return _cal_day(d.date())
    except Exception:
        if d.weekday() >= 5:
            return False
        return d.strftime("%Y-%m-%d") not in MARKET_HOLIDAYS_2026


def in_trade_hours() -> bool:
    """交易时段 = 交易日 + 9:30-11:30 / 13:00-15:00（排除午休 11:30-13:00）。

    非交易日（周末/节假日）返回 False，避免用上一交易日收盘价触发价格条件（伪触发）。
    """
    if not is_trading_day():
        return False
    hhmm = now_hhmm()
    return ("09:30" <= hhmm <= "11:30") or ("13:00" <= hhmm <= "15:00")


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
            "WHERE status='pending' AND type NOT IN ('CALENDAR','L3_SNAPSHOT','NEWS_SNAPSHOT') "
            "ORDER BY priority ASC, id DESC LIMIT 30").fetchall()]
    finally:
        conn.close()


# ---------- 2. atr-sync 每日维护 ----------
def atr_sync_daily() -> list[str]:
    """交易时段首次 tick：对持仓股（position open）跑 atr-sync 更新止损位。

    例行维护静默：成功不输出（monitor 判定无变化 → 不唤醒 agent），
    仅失败输出告警（止损位未同步是需要 agent 关注的异常）。
    """
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
    alerts = []
    for s in stocks:
        out = ptrade2("atr-sync", s, timeout=60)
        if not out:
            alerts.append(f"⚠️ {s} atr-sync 失败（止损位未同步，需人工核验）")
    st["last_atr_date"] = today
    save_state(st)
    return alerts


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
                 manual: bool = False, mode: str = "trade", budget: float | None = None,
                 tp_only: bool = False) -> bool:
    """写 WATCH_ALERT 事件 + 原子标记条件为 triggered（触发即失效，防重复进入流程）。

    manual=True（手动补录路径）：仅当条件仍 active 才允许写入，已 triggered/不存在则拒绝，
    返回 False 由调用方记录跳过原因——防对旧条件重复补录（如"买点上沿"昨已触发今又补）。
    mode="eval"（技术组 L2 待命复检价格点）：不标记 conditions（无账户条件），payload 带 mode 供消费方区分。
    mode="buy"（技术组 L2 建仓点）：同 eval 不碰 conditions，额外带 budget（建仓预算）供消费方 allocate。
    tp_only=True（止盈阶梯，2026-08-30）：**只标记触发的 TP 条件本身**，不做 family 标记——
      阶梯只卖 1/3、仓位存续，family UPDATE（category='hard'）会连坐清掉
      cost_protection/trailing_stop，造成余仓裸奔。
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
    # tp_only（止盈阶梯）：只标记本条件——阶梯卖1/3后仓位存续，禁止连坐保护线（2026-08-30）
    if mode == "trade" and os.path.exists(POOL_DB) and cond_id and cond_id > 0:
        cond_params = ()
        if tp_only:
            cond_filter = "id=?"
            cond_params = (cond_id,)
        elif direction == "buy":
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
                (cond_id,) + cond_params,
            )
            pconn.commit()
        finally:
            pconn.close()
    return True


def check_naked_conditions() -> list[str]:
    """裸奔检测（2026-08-27 加，防 8/21 中芯裸奔 6 天教训）：
    position 表 open 段的持仓，若无任何 active 的 cost_protection/trailing_stop
    条件 → 告警（agent 需补建保护线）。触发后每日同文案 hash 不变不再重复唤醒。
    """
    if not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(POOL_DB)
    conn.row_factory = sqlite3.Row
    try:
        # open 段的股票（持仓股）
        open_stocks = [r["stock"] for r in conn.execute(
            "SELECT stock FROM position WHERE status='open'").fetchall()]
        if not open_stocks:
            return []
        alerts = []
        for stock in open_stocks:
            # 该股 active 的保护类条件
            # v9（M1.6 账户层退役）：段即账户——conditions join position 段行
            cnt = conn.execute(
                "SELECT COUNT(*) AS n FROM conditions c JOIN position a ON a.id=c.account_id "
                "WHERE a.stock=? AND c.status='active' "
                "AND c.type IN ('cost_protection','trailing_stop')", (stock,)
            ).fetchone()["n"]
            if cnt == 0:
                alerts.append(f"[ALERT] 裸奔：{stock} 持仓但无活动成本保护/移动止损（触发后未重建？心跳需补建保护线）")
                continue
            # 错位检测（2026-08-27 加，中芯 8/27 真实病态：active 但 price>现价 = 设置即触发）：
            # active cost_protection 且 price > 现价×1.02 → 告警；排除深跌期补执行
            # （现价 < 成本×88% 时 88% 底线故意高于现价——语义正确不报）
            code_row = conn.execute(
                "SELECT p.code, p.id AS aid FROM position p "
                "WHERE p.stock=? AND p.status='open' "
                "AND COALESCE(p.strategy,'')!='NEWS' ORDER BY p.id DESC LIMIT 1", (stock,)
            ).fetchone()
            if code_row:
                px = fetch_price(code_row["code"])
                if px:
                    avg_cost = conn.execute(
                        "SELECT SUM(CASE WHEN pos.operation='buy' THEN pos.total_cost "
                        "ELSE -pos.total_cost END)/NULLIF(SUM(CASE WHEN pos.operation='buy' THEN "
                        "pos.quantity ELSE -pos.quantity END),0) AS c FROM trades pos "
                        "WHERE pos.account_id=?", (code_row["aid"],)
                    ).fetchone()["c"]
                    deep_period = avg_cost is not None and px < avg_cost * 0.88
                    for c in conn.execute(
                        "SELECT price FROM conditions c JOIN position a ON a.id=c.account_id "
                        "WHERE a.stock=? AND c.status='active' AND c.type='cost_protection'",
                        (stock,)).fetchall():
                        if c["price"] and px and c["price"] > px * 1.02 and not deep_period:
                            alerts.append(
                                f"[ALERT] 错位：{stock} 成本保护 ¥{c['price']:.2f} > 现价 ¥{px:.2f}×1.02"
                                f"——设置即触发风险（非深跌补执行期），需检查保护线合理性")
        return alerts
    finally:
        conn.close()


def check_price_triggers() -> list[str]:
    """读 active 条件 vs 实时价，穿越触发 → 写 WATCH_ALERT（去重）。"""
    if not in_trade_hours() or not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(POOL_DB)
    conn.row_factory = sqlite3.Row
    try:
        # v9：段即账户——条件表 join position 段行（stock/code 即段字段）
        rows = conn.execute(
            "SELECT cn.id, a.stock AS stock_name, a.code AS stock_code, cn.price, cn.action, "
            "cn.category, cn.type, cn.name, cn.is_event "
            "FROM conditions cn JOIN position a ON cn.account_id=a.id "
            "WHERE cn.status='active' AND cn.price IS NOT NULL").fetchall()
    finally:
        conn.close()
    triggers = []
    for r in rows:
        action = r["action"] or ""
        ctype = r["type"] or ""
        cname = r["name"] or ""
        # 方向判定与 conditions_manager._condition_direction 同优先级（2026-08-30）：
        # name 关键字 > type > action 文字（原纯按 action 猜——恒申"成本保护-5%(建仓点
        # 试探仓)"含"建仓"被误判 buy，跌破保护线会发加仓告警而非清仓告警）。
        is_up = (ctype in ("take_profit_1", "take_profit_2")
                 or any(k in cname for k in ("止盈", "目标")))
        if not is_up and (ctype in ("cost_protection", "trailing_stop")
                          or any(k in cname for k in ("止损", "保护", "破位"))):
            direction = "sell"       # 跌破触发（现价 ≤）
        elif is_up:
            direction = "sell"       # 涨破触发（现价 ≥）：止盈阶梯/目标类
        elif r["is_event"]:
            # 事件条件（type 统一无法按型区分）：action 文字兜底
            direction = "buy" if any(w in action for w in BUY_WORDS) else "sell"
        else:
            is_buy = any(w in action for w in BUY_WORDS)
            is_sell = any(w in action for w in SELL_WORDS) or r["category"] == "hard"
            if not (is_buy or is_sell):
                continue
            direction = "buy" if is_buy else "sell"
        price = fetch_price(r["stock_code"])
        if price is None:
            continue
        hit = price >= r["price"] if is_up else price <= r["price"]
        if not hit:
            continue
        if _has_pending_event(r["stock_name"], direction, r["id"]):
            continue  # 已有同向/同条件待处理事件，去重
        if not _write_alert(r["stock_name"], r["stock_code"], direction, r["id"],
                            action, r["price"], price, tp_only=is_up):
            continue  # 条件已非 active（极端竞态），跳过
        arrow = "≥" if is_up else "≤"
        triggers.append(
            f"🔔 {r['stock_name']}({r['stock_code']}) {direction.upper()} 触发: "
            f"现价¥{price:.2f} {arrow} 条件¥{r['price']:.2f} [{action}]")
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


# ---------- 4. 大盘异动检测（MARKET_SHOCK）----------
MARKET_INDICES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
}
# 触发阈值（单日跌幅%）：任一指数跌破即触发
MARKET_SHOCK_THRESHOLDS = {"sh000001": 2.0, "sz399001": 3.5, "sz399006": 4.0, "sh000688": 5.0}
# 同交易日去重：一天最多触发 1 次深度研究（避免盘中反复唤醒）
MARKET_SHOCK_STATE_KEY = "market_shock_last"


def fetch_index_day_chg(code: str) -> float | None:
    """拉指数单日涨跌幅（%）。用 fetch-price 返回的涨跌幅字段。"""
    out = ptrade2("fetch-price", code)
    m = re.search(r"涨跌幅:\s*([+-]?[\d.]+)%", out)
    return float(m.group(1)) if m else None


def check_market_shock() -> list[str]:
    """大盘指数异动检测：任一指数单日跌幅超阈值 → 写 MARKET_SHOCK 事件。

    触发后 agent 做深度研究（新闻收集 + 社区声音 + 逻辑链条整理）。
    同交易日只触发一次（kv_store 记录日期），防盘中反复唤醒。
    时间窗口：09:30-16:00（收盘后 1 小时宽限，覆盖尾盘大跌）。
    """
    hhmm = now_hhmm()
    if not (TRADE_START <= hhmm <= "16:00") or not os.path.exists(TASKS_DB):
        return []
    st = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if st.get(MARKET_SHOCK_STATE_KEY) == today:
        return []  # 今天已触发过
    shocks = []
    for code, name in MARKET_INDICES.items():
        chg = fetch_index_day_chg(code)
        if chg is None:
            continue
        thr = MARKET_SHOCK_THRESHOLDS.get(code, 5.0)
        if chg <= -thr:
            shocks.append({"index": name, "code": code, "chg": round(chg, 2), "threshold": thr})
    if not shocks:
        return []
    # 写 MARKET_SHOCK 事件（一天一次）
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    try:
        payload = json.dumps({
            "indices": shocks, "date": today,
            "research": "深度研究：新闻收集+社区声音+逻辑链条整理+组合影响评估",
        }, ensure_ascii=False)
        conn.execute(
            "INSERT INTO task_events (type, entity, source, priority, payload) "
            "VALUES ('MARKET_SHOCK', '大盘', 'heartbeat-scan', 1, ?)",
            (payload,),
        )
        conn.commit()
    finally:
        conn.close()
    st[MARKET_SHOCK_STATE_KEY] = today
    save_state(st)
    names = "、".join(f"{s['index']}{s['chg']:+.2f}%" for s in shocks)
    return [f"📉 大盘异动: {names} 触发 MARKET_SHOCK 深度研究"]


# ---------- 5. 动量异动扫描（原有） ----------
def pool_stocks() -> list[tuple[str, str]]:
    """池内 active 股票 (名称, code)。code 缺失时从 position 段表兜底（v9：accounts 已退役，
    pool.code 可能为 NULL）。

    sleeve-m1（方案 3.4）：排除 strategy='NEWS'（消息组信号缓冲）——甜点区/追高检测
    对消息票产噪音告警，消息组不走技术档位语言。
    """
    if not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(POOL_DB)
    conn.row_factory = sqlite3.Row
    try:
        # v9：段即账户——code 兜底源=position 段行（accounts 退役）
        rows = conn.execute(
            "SELECT p.stock AS stock, COALESCE(p.code, "
            "(SELECT code FROM position WHERE stock=p.stock AND code IS NOT NULL LIMIT 1)) AS code "
            "FROM pool p "
            "WHERE p.pool_status='active' AND COALESCE(p.strategy,'') != 'NEWS'").fetchall()
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
    """CALENDAR 事件到期检测：到期时刻 ≤ now 且 pending → 输出（monitor 变化 → 唤醒 agent 消费）。

    分析 agent 对"暂时不买/等财报/等催化"的股票写 CALENDAR 事件（payload.due），
    到期时心跳唤醒重新评估（进技术组 L2 待命复检 / 继续观察 / 移除）。未到期不输出（安静睡眠）。

    到期时刻语义（2026-08-18 修复，防凌晨空触发）：
    - 纯日期 "2026-08-20" → 视为当天 **15:30（收盘后）** 到期——等财报/等公告的事件
      不会被当天凌晨唤醒（那时报告还没出），收盘后数据/公告才齐。
    - 带时间 "2026-08-20T10:00"（或含空格）→ 精确到该时刻触发（紧急事项显式写时间）。
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
    now = datetime.now()
    out = []
    for rid, entity, payload in rows:
        try:
            p = json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            continue
        due_raw = str(p.get("due") or "")
        due_str = due_raw[:10]
        if not due_str:
            continue
        # 解析到期时刻：带时间 → 原样；纯日期 → 当天 15:30
        try:
            if "T" in due_raw or " " in due_raw:
                due_dt = datetime.fromisoformat(due_raw[:19].replace(" ", "T"))
            else:
                due_dt = datetime.strptime(due_str, "%Y-%m-%d").replace(hour=15, minute=30)
        except ValueError:
            continue  # due 格式非法 → 跳过该事件（不触发也不崩溃）
        if due_dt <= now:
            out.append(f"📅 CALENDAR 到期 #{rid} {entity} due={due_dt:%Y-%m-%d %H:%M} [{p.get('event', '')}]")
    return out


def check_watch_points() -> list[str]:
    """价格点检测：现价 ≤ 价格点 → WATCH_ALERT + 移除价格点（触发即失效）。

    价格点由组合审查/分析 agent 用 taskbus watchpoint add 写入 kv_store('watch_points')。
    - mode=eval（技术组 L2 待命复检点）→ WATCH_ALERT(mode=eval) 唤醒分析 agent 复检
      （升 L1 挂 conditions / 继续观察 / 移除——原"评估升级"语义，L3 已并 L2）
    - mode=buy（技术组 L2 建仓点）→ WATCH_ALERT(mode=buy, budget=金额) 唤醒核验 → allocate → buy
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
            # 区间触发：配了 min 则现价 ∈ [min, price] 才触发；单值保持 现价 ≤ price
            min_price = p.get("min")
            if min_price is not None:
                hit = min_price <= price <= p["price"]
            else:
                hit = price <= p["price"]
            if hit:
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
                    cond_name = f"L2复检-{note}" if note else "L2复检"
                    if _write_alert(entity, code, "eval", 0, cond_name, p["price"], price,
                                    mode="eval"):
                        alerts.append(f"📌 {entity}({code}) L2待命复检点触发: 现价¥{price} ≤ ¥{p['price']} "
                                      f"[{note}] → 唤醒复检（升 L1/挂 conditions/移除）")
                        points.pop(entity, None)  # 触发即失效
                        changed = True
                break
    if changed:
        _kv_set("watch_points", points)
    return alerts


def cleanup_tabs_auto(max_keep: int = 4, trigger: int = 10):
    """CDP tab 自动清理（心跳用，2026-08-27 加，防 OOM）：
    调独立入口 ~/.agent-browser/tab-cleanup.py（--quiet 静默）。
    页面 tab > trigger 时从最早的开始关，保留最近 max_keep 个。
    纯副作用：不输出到 monitor 结果（不唤醒 LLM），动作写 /tmp/tab_cleanup.log。
    """
    import subprocess
    try:
        subprocess.run(
            ["python3", "/home/catmouse/.agent-browser/tab-cleanup.py",
             "--keep", str(max_keep), "--trigger", str(trigger), "--quiet"],
            timeout=15, capture_output=True)
    except Exception:
        pass


def main() -> int:
    # CDP tab 自动清理（>10 保留 4，从最早开始关）——纯副作用，不唤醒 LLM
    cleanup_tabs_auto()
    lines = []
    tasks = check_tasks()
    if tasks:
        latest = tasks[0]
        lines.append(f"[EVENT] pending={len(tasks)} 个 | 最新 #{latest['id']} "
                     f"[{latest['type']}] {latest['entity']} (p{latest['priority']})")
        # 控制单次唤醒的事件列举条数（防响应截断，2026-08-22）
        # 只列前 3 个，其余汇总——agent 消费时按优先级处理，不会丢
        for t in tasks[:3]:
            lines.append(f"  #{t['id']} [{t['type']}] {t['entity']} p{t['priority']} src={t['source']}")
        if len(tasks) > 3:
            lines.append(f"  … 其余 {len(tasks)-3} 个（按优先级逐一 claim 处理，单轮≤3 个防截断）")
    lines.extend(atr_sync_daily())
    lines.extend(check_price_triggers())
    lines.extend(check_naked_conditions())
    lines.extend(check_watch_points())
    lines.extend(check_calendar())
    lines.extend(audit_inconsistencies())
    lines.extend(check_market_shock())
    lines.extend(scan_moves())
    if not lines:
        print("IDLE")
        return 0
    # 输出总条数上限：Monitor 唤醒信息量过大 → agent 响应易截断
    # 保留最关键的（价格触发/对账/大盘异动优先），异动扫描类可截断
    MAX_OUTPUT_LINES = 25
    if len(lines) > MAX_OUTPUT_LINES:
        lines = lines[:MAX_OUTPUT_LINES] + [f"… 共 {len(lines)} 条，已截断显示（完整清单见 taskbus list）"]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
