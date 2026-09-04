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
# v12 news scope：newsdb 路径（与 news-database config.get_db_path 同口径：env 优先）
NEWS_DB = os.environ.get("STOCK_NEWS_DB") or os.path.join(
    os.environ.get("STOCK_ANALYSIS_WORKSPACE_ROOT",
                   "/home/catmouse/Github_Project/daily-stock-workspace"),
    "data", "news", "news.db")
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


# 同拍价格缓存（2026-09-04 S1 修复）：E6 保护链/E7 watchpoint/E8 裸奔/E10 动量
# 对同一股票可能重复取价——同一次 price scope 内每 code 只取一次，砍重复进程+网络。
# monitor 每拍新进程启动，缓存天然按拍隔离（无跨拍陈旧问题）。
_PRICE_CACHE: dict[str, float] = {}


def _clear_price_cache() -> None:
    _PRICE_CACHE.clear()


def fetch_price(code: str) -> float | None:
    if code in _PRICE_CACHE:
        return _PRICE_CACHE[code]
    out = ptrade2("fetch-price", code)
    m = re.search(r"当前价格:\s*¥([\d.]+)", out)
    px = float(m.group(1)) if m else None
    if px is not None:
        _PRICE_CACHE[code] = px
    return px


def fetch_price_any(code: str) -> float | None:
    """取实时价，兼容 newsdb 混合码格式（sh600760 / 600703.SH / 300394）。

    newsdb event_stock 存码三态并存（实测 9/3：sh 前缀、点后缀、裸 6 位），
    ptrade2 fetch-price 只认前缀/裸部分格式时各异——依次尝试候选写法，首个成功即返回。
    """
    if not code:
        return None
    c = code.strip()
    cands = [c]
    m = re.match(r"^(sh|sz|bj)(\d{6})$", c, re.I)
    if m:
        cands.append(f"{m.group(2)}.{'SH' if m.group(1).lower() == 'sh' else 'SZ'}")
    elif re.search(r"\.(SH|SZ|BJ)$", c, re.I):
        digits, suf = c.split(".")[0], c.split(".")[-1].lower()
        cands.append(f"{suf}{digits}")
    elif re.match(r"^\d{6}$", c):
        cands.append(("sh" if c[0] in "5689" else "sz") + c)
    for cand in cands:
        px = fetch_price(cand)
        if px is not None:
            return px
    return None


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
    """待消费事件（排除 CALENDAR：定时回查由 check_calendar 到期才输出，未到期不唤醒）。

    v12：MSG_CANDIDATE/MSG_ORDER/MSG_REJUDGE 也不列——消息挂单三类型唯一消费者
    =专用心跳 msg-watch（claim 硬门见 task_bus/db.py），legacy 心跳只发现不消费，
    排除防止旧心跳被唤醒误 claim（prompt 热换前的双保险）。"""
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
            "WHERE status='pending' AND type NOT IN "
            "('CALENDAR','L3_SNAPSHOT','MSG_SNAPSHOT',"
            "'MSG_CANDIDATE','MSG_ORDER','MSG_REJUDGE',"
            # analysis-ttl（9/4）：ANALYSIS_REFRESH 唯一消费者=analysis-watch，
            # legacy 连 [EVENT] 列表都不该看到（claim 硬门是二道保险）
            "'ANALYSIS_REFRESH','WATCH_ALERT',"
            # 深挖/大盘异动（9/4）归 news-collect 消费（C1 初过滤产出），legacy 不列
            "'DEEP_DIVE','MARKET_SHOCK') "
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
        # 半盲修复（cron-audit 2026-09-02）：ptrade2 报错文本走 stdout 非空，
        # 旧判据 `if not out` 看不见失败 → 报错关键字并入告警条件
        if not out or "报错" in out or "❌" in out or "Error" in out:
            alerts.append(f"⚠️ {s} atr-sync 失败（止损位未同步，需人工核验）: {out[:120]}")
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
        # open 段的股票（持仓股）——只查**有实际持仓**（FIFO 净 qty>0）的段：
        # v9 段即账户 + M3 开闸后 sleeve-open 建的 NEWS pending 成员段是 open 段但 qty=0，
        # 保护链三件套由 sleeve-fill 成交时挂载，空槽不是"裸奔"（2026-09-02 误报 6 只实锤）。
        # L1 同理：清仓未 release 的空段也无需保护线。裸奔=有股份无保护线。
        open_stocks = [r["stock"] for r in conn.execute(
            "SELECT p.stock FROM position p WHERE p.status='open' AND EXISTS ("
            "  SELECT 1 FROM trades t WHERE t.account_id=p.id"
            "  GROUP BY t.account_id"
            "  HAVING SUM(CASE WHEN t.operation='buy' THEN t.quantity"
            "                  ELSE -t.quantity END) > 0)").fetchall()]
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
    """日线收盘价列表，**新→旧**（closes[0]=今日）。

    2026-09-03 修复：底层 kline_fetcher.py 对 day_data `sort(key=lambda x: x['date'])`
    输出**升序（旧→新）**——旧代码直接 append 导致 closes[0] 实为最旧 bar，
    scan_moves 的 today/ten_ago 全部错位（拿历史价当现价判动量）。此处反转对齐注释。
    """
    out = ptrade2("fetch-kline", code, "--type", "day", "--count", "15")
    closes = []
    for line in out.splitlines():
        m = re.search(r"收:\s*([\d.]+)", line)
        if m:
            closes.append(float(m.group(1)))
    return list(reversed(closes))  # 源升序→反转：新→旧


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


def check_sleeve_pending() -> list[str]:
    """sleeve 槽状态可见性（cron-audit P0 根修，2026-09-02）：
    交易日 ≥9:30 且有 pending 槽 → 持久输出 [SLEEVE] 行 → monitor 必唤醒直到成交/弃单。
    字节稳定（开槽日/预算/留痕计数，无价格）；成交后行消失→确认唤醒。
    成交时点口径（2026-09-02 earliest_fill 改造）：today ≥ earliest_fill 的槽报
    '必跑 sleeve-fill'；未到期的只报'禁fill'。earliest 解析失败 fail-closed
    回退旧口径（开槽日次一交易日才可成交）。
    收盘上限（2026-09-02 主代理补，与事件层 747ee9a 对齐）：now > 15:05 不输出——
    fill 只在开盘窗口有意义；若 wake 层收盘后仍报'必跑'，心跳任何变更唤醒都会
    触发 sleeve-fill 按**收盘价**成交（口径污染）。当日漏成交归次日晨审处置。"""
    if not os.path.exists(POOL_DB) or not is_trading_day():
        return []
    if now_hhmm() < "09:30" or now_hhmm() > "15:05":
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        slots = conn.execute(
            "SELECT event_key,opened_at,budget FROM event_slots "
            "WHERE fill_status='pending' AND status IN ('open','partial') "
            "ORDER BY event_key").fetchall()
        blocked = conn.execute(
            "SELECT COUNT(*) n FROM shadow_log "
            "WHERE kind='fill_blocked' AND created_at>=?", (today,)).fetchone()["n"]
    finally:
        conn.close()
    out = []
    for s in slots:
        od = str(s["opened_at"])[:10]
        if _earliest_fill_allowed(s["event_key"], od, today):
            out.append(f"[SLEEVE] {s['event_key']} 待成交 开槽{od} "
                       f"预算¥{s['budget']:,.0f} → 本轮必跑 sleeve-fill")
        else:
            out.append(f"[SLEEVE] {s['event_key']} 未到最早成交日→到期才可成交，本轮禁fill")
    if slots and blocked:
        out.append(f"[SLEEVE] 今日 fill_blocked 留痕 {blocked} 条（R7 拒单，读 shadow_log 核验，禁绕防线）")
    return out


def _earliest_fill_allowed(event_key: str, opened_date: str, today: str) -> bool:
    """today ≥ earliest_fill 才可成交。真源=paper_trading_v2.earliest_fill（与
    sleeve-fill CLI 同一解析）；import 失败 fail-closed 回退旧口径（开槽日<今日）。"""
    try:
        from paper_trading_v2 import earliest_fill as ef
        res = ef.resolve_earliest_fill(event_key, opened_date)
        return today >= str(res.date)
    except Exception:
        return opened_date < today


def check_sleeve_fill_event() -> list[str]:
    """SLEEVE_FILL 事件化（2026-09-02 earliest_fill 改造）：
    交易日 ≥9:30 且存在到期（today ≥ earliest_fill）pending 槽 → 向 taskbus 插
    type=SLEEVE_FILL 事件（payload 含 event_keys/slot_count）。
    同日已有同型 pending/processing/done 事件 → 不重复插（同日单事件；次日仍
    pending 会再插）。[SLEEVE] 行保留（check_sleeve_pending=唤醒层，不动）。
    earliest 解析失败 fail-closed：该槽视为未到期（CLI 侧有 audit fallback 留痕）。
    收盘上限（2026-09-02 修复）：now > 15:05（TRADE_END+5 分钟尾巴）不插——fill 只在
    开盘窗口有意义，收盘后插事件=心跳被唤醒去跑注定失败的 fill。"""
    if not os.path.exists(POOL_DB) or not is_trading_day():
        return []
    if now_hhmm() < TRADE_START or now_hhmm() > "15:05":
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        slots = conn.execute(
            "SELECT event_key,opened_at FROM event_slots "
            "WHERE fill_status='pending' AND status IN ('open','partial') "
            "ORDER BY event_key").fetchall()
    finally:
        conn.close()
    due = [s["event_key"] for s in slots
           if _earliest_fill_allowed(s["event_key"], str(s["opened_at"])[:10], today)]
    if not due:
        return []
    _ensure_task_table()
    pconn = sqlite3.connect(TASKS_DB)
    try:
        # 同日去重（规格口径）：已有 pending/processing 同型事件 → 不重复插。
        # done/failed 不算——槽仍到期 pending 说明成交没完成，下一 tick 重插=唤醒层
        # 持续施压直到 filled（[SLEEVE] 行同样持续唤醒，无失控风险）。
        same = pconn.execute(
            "SELECT 1 FROM task_events WHERE type='SLEEVE_FILL' AND status IN "
            "('pending','processing') LIMIT 1").fetchone()
        if same:
            return []
        payload = json.dumps({
            "event_keys": due, "slot_count": len(due), "date": today,
            "action": "到期 pending 槽成交：跑 ptrade2 sleeve-fill（禁 --allow-same-day 绕门）",
        }, ensure_ascii=False)
        pconn.execute(
            "INSERT INTO task_events (type, entity, source, priority, payload) "
            "VALUES ('SLEEVE_FILL', '消息组槽', 'heartbeat-scan', 1, ?)",
            (payload,),
        )
        pconn.commit()
    finally:
        pconn.close()
    return [f"📌 SLEEVE_FILL 事件入队：{len(due)} 个到期 pending 槽 "
            f"({', '.join(due)}) → sleeve-fill"]


# ---------- 4.6 v12 消息挂单：news scope 检出（方案 v12-news-order-20260903） ----------
MSG_EVENT_TYPES = ("MSG_CANDIDATE", "MSG_ORDER", "MSG_REJUDGE")
NEWS_IMPACT_MIN = 4          # 检出阈值：importance >= 4
NEWS_MAX_AGE_HOURS = 24      # 入库新鲜度：events.created_at 起 24h 内
NEWS_SCAN_STATE_KEY = "news_scan_state"   # kv: {"emitted": [event_key...]} 检出留痕（防 done 后复发）


def _pool_event_keys() -> set[str]:
    """已入池事件键（pool.event_key ∪ event_slots.event_key，只读）。

    缺表/缺列（旧库）→ 该来源视为空，不崩溃。newsdb 事件键约定 ND#<event_id>
    （与 sleeve watchlist-add --event-key 同一格式）。"""
    if not os.path.exists(POOL_DB):
        return set()
    keys: set[str] = set()
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    try:
        for table in ("pool", "event_slots"):
            try:
                keys.update(r[0] for r in conn.execute(
                    f"SELECT DISTINCT event_key FROM {table} "
                    "WHERE event_key IS NOT NULL AND event_key != ''"))
            except sqlite3.OperationalError:
                pass  # 表/列不存在（旧库）→ 忽略该来源
    finally:
        conn.close()
    return keys


def _news_event_codes(nconn: sqlite3.Connection, event_id: int) -> list[str]:
    """事件关联股票代码（直接 event_stock 优先；行业事件经 event_industry→
    industry_stocks 兜底）。按 relevance 降序，首位=anchor 取价对象。"""
    codes = [r[0] for r in nconn.execute(
        "SELECT stock_code FROM event_stock WHERE event_id=? "
        "ORDER BY relevance DESC, stock_code", (event_id,)).fetchall()]
    if codes:
        return codes
    return [r[0] for r in nconn.execute(
        "SELECT ish.stock_code FROM event_industry ei "
        "JOIN industry_stocks ish ON ish.industry_id = ei.industry_id "
        "WHERE ei.event_id=? ORDER BY ish.relevance DESC, ish.stock_code LIMIT 5",
        (event_id,)).fetchall()]


def _news_already_emitted(event_key: str, emitted: set[str]) -> bool:
    """检出留痕三查：taskbus 同键 MSG_CANDIDATE / MSG_ORDER 状态、kv emitted。

    v12-patch/E13：同 event_key 仅剩 failed 记录 → 放行重检重发（消费失败不该
    永久封死一条消息链路——fail 多为环境性：锚价取不到/消费端崩）；pending/
    processing/done 任一存在 → 不重发（done 且槽已开在 _pool_event_keys 一层
    再挡一道，防双开）。taskbus 无任何同键记录时 kv 留痕兜底（防任务表清理后
    done 事件复发=死循环）。"""
    conn = sqlite3.connect(TASKS_DB)
    try:
        rows = conn.execute(
            "SELECT status FROM task_events WHERE type IN ('MSG_CANDIDATE','MSG_ORDER') "
            "AND payload LIKE ?",
            (f'%"event_key": "{event_key}"%',)).fetchall()
    finally:
        conn.close()
    if rows:
        return any(r[0] != "failed" for r in rows)
    return event_key in emitted


def check_news_events() -> list[str]:
    """news scope 检出：newsdb 新事件 → MSG_CANDIDATE 事件入 taskbus。

    判定（契约）：imp≥4、status=open、bullish 方向（事件下至少一条消息
    signal_direction='bullish'）、created_at（入库时刻真源）24h 内、
    未入池（ND#id 不在 pool/event_slots）、未发过（_news_already_emitted）。

    payload：event_key=ND#<id>、anchor_price=**检出时刻实时价**（第一关联股，
    fetch_price_any 兼容混合码格式）、event_title、newsdb_event_id、codes 等。
    fail-closed：无关联股或取价失败 → **不写事件**（无锚无法定 ±5% band，
    输出顺延提示；24h 窗口内下一 tick 再试）。锚价语义=事件入库/检出时刻快照，
    不是挂单时刻价（用户裁决 9/3 第 1 条）。"""
    if not os.path.exists(NEWS_DB) or not os.path.exists(TASKS_DB):
        return []
    st = _kv_get(NEWS_SCAN_STATE_KEY)
    emitted = set(st.get("emitted") or [])
    pool_keys = _pool_event_keys()
    out, new_emitted = [], []
    nconn = sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)
    nconn.row_factory = sqlite3.Row
    try:
        rows = nconn.execute(
            "SELECT e.id, e.title, e.importance, e.entity_type, e.created_at FROM events e "
            "WHERE e.importance >= ? AND e.status = 'open' "
            "AND e.created_at != '' AND e.created_at >= datetime('now','localtime', ?) "
            "AND EXISTS (SELECT 1 FROM messages m WHERE m.event_id = e.id "
            "            AND m.signal_direction = 'bullish') "
            "ORDER BY e.importance DESC, e.id DESC",
            (NEWS_IMPACT_MIN, f"-{NEWS_MAX_AGE_HOURS} hours")).fetchall()
        for e in rows:
            event_key = f"ND#{e['id']}"
            if event_key in pool_keys:
                continue  # 已入池（watchlist/sleeve 槽），消息组链路已接管
            if _news_already_emitted(event_key, emitted):
                continue
            codes = _news_event_codes(nconn, e["id"])
            anchor = None
            for c in codes:
                anchor = fetch_price_any(c)
                if anchor is not None:
                    break
            if anchor is None:
                out.append(f"⚠️ {event_key}「{e['title'][:30]}」锚价获取失败"
                           f"（codes={codes[:3]}），本轮顺延，24h 内下一 tick 重试")
                continue
            _ensure_task_table()
            payload = json.dumps({
                "event_key": event_key,
                "newsdb_event_id": e["id"],
                "event_title": e["title"],
                "anchor_price": anchor,
                "anchor_code": codes[0] if codes else None,
                "importance": e["importance"],
                "entity_type": e["entity_type"],
                "codes": codes,
                "news_created_at": e["created_at"],
                "detected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "action": "消息专用心跳消费：G1-G4 闸→值得→watchlist-add NEWS + "
                          "sleeve-open→sleeve-order-place（band=[anchor×0.95, anchor×1.05]）；"
                          "不值得→done 注明（claimed_by 必须=msg-watch）",
            }, ensure_ascii=False)
            conn = sqlite3.connect(TASKS_DB)
            try:
                cur = conn.execute(
                    "INSERT INTO task_events (type, entity, source, priority, payload) "
                    "VALUES ('MSG_CANDIDATE', ?, 'watch-scan-news', 1, ?)",
                    (event_key, payload))
                conn.commit()
                tid = cur.lastrowid
            finally:
                conn.close()
            new_emitted.append(event_key)
            out.append(f"📰 MSG_CANDIDATE #{tid} {event_key} imp={e['importance']} "
                       f"锚¥{anchor:.2f}「{e['title'][:40]}」→ claim --consumer msg-watch")
    finally:
        nconn.close()
    if new_emitted:
        emitted.update(new_emitted)
        st["emitted"] = sorted(emitted)[-300:]  # 留痕上限，防 kv 无限膨胀
        _kv_set(NEWS_SCAN_STATE_KEY, st)
    return out


def news_pending_lines() -> list[str]:
    """news scope 唤醒层：pending/processing 的 MSG_* 事件持久列举（字节稳定，
    同 [SLEEVE] 语义）——专用心跳每拍看到未清事件持续唤醒，直到消费/重判闭环。"""
    if not os.path.exists(TASKS_DB):
        return []
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, type, entity, priority, status FROM task_events "
            "WHERE type IN ('MSG_CANDIDATE','MSG_ORDER','MSG_REJUDGE') "
            "AND status IN ('pending','processing') "
            "ORDER BY priority ASC, id ASC LIMIT 30").fetchall()
    finally:
        conn.close()
    return [f"[NEWS] #{r['id']} [{r['type']}] {r['entity']} p{r['priority']} "
            f"{r['status']} → taskbus claim {r['id']} --consumer msg-watch"
            for r in rows]


def run_news_scope() -> int:
    """news scope 主流程（v12 专用心跳 monitor）：只检 newsdb 新事件 + 列 MSG_*
    待办。**静默** SLEEVE_FILL/[SLEEVE]（旧链路退役，legacy scope 原样保留可回滚）、
    静默价格条件/裸奔/异动/大盘等 legacy 检测——专用心跳只看 news 输出，防串唤醒。"""
    lines = []
    lines.extend(check_news_events())
    lines.extend(news_pending_lines())
    if not lines:
        print("IDLE")
        return 0
    print("\n".join(lines))
    return 0


# ---------- 4.7 v12 挂单槽价格扫描：price scope（v12-patch E1/E12/E6） ----------
def in_price_scan_window() -> bool:
    """E12：--scope price 交易时段闸（精确）：交易日 + 9:30-11:30 / 13:00-14:57。

    不含 9:00-9:29 集合竞价（伪价不进挂单检测）、不含 14:58+ 收盘集合竞价
    （集中撮合价不宜作检测价）。比 legacy in_trade_hours（15:00 止）更严——
    挂单检测价/成交判定窗口收口；保护链扫描（E6，check_price_triggers）仍按
    legacy 自己的 in_trade_hours 窗口到 15:00。
    """
    if not is_trading_day():
        return False
    hhmm = now_hhmm()
    return ("09:30" <= hhmm <= "11:30") or ("13:00" <= hhmm <= "14:57")


def _slot_member_code(event_key: str) -> str | None:
    """挂单槽取价对象 code（首成员）：event_slot_members JOIN position 段——
    与 paper_trading_v2.sleeve_order._first_member_code 同口径（E9 核价/E2 payload
    同源，取价对象唯一）。取不到 → None（调用方输出取价失败行）。"""
    if not os.path.exists(POOL_DB):
        return None
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT p.code FROM event_slot_members m "
            "LEFT JOIN position p ON p.stock=m.stock AND p.status='open' "
            "AND p.strategy='NEWS' WHERE m.event_key=? AND p.code IS NOT NULL "
            "ORDER BY m.joined_at LIMIT 1", (event_key,)).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None  # 缺表/缺列（旧库）→ 视为无成员段
    finally:
        conn.close()


def _parse_order_ttl(ts) -> "datetime | None":
    """挂单 TTL 宽松解析（T/空格分隔 ISO）。解析失败返回 None——扫描侧不阻塞
    （成交/弃单 CLI 侧 fail-closed 复验 TTL，是最终防线）。"""
    try:
        return datetime.fromisoformat(str(ts)[:19].replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def check_price_orders() -> list[str]:
    """E1 挂单槽价格扫描（--scope price）：pending_order 槽四态检测。

    每拍扫 event_slots status='pending_order'（**不是** legacy 的
    fill_status='pending' AND status IN ('open','partial')——那是旧 sleeve-fill 的），
    对每槽 fetch_price_any 取现价（首成员 code，_slot_member_code）：
    - 价 ∈ [band_min,band_max] → 触带行（消费方 sleeve-order-fill --price 检测价）
    - 价 < band_min → 破带行（sleeve-order-expire --reason band_break）
    - now > order_ttl → 过期行（--reason expired；TTL 优先于价格——挂单已死）
    - 取价失败 → 明确失败行（不静默：不成交不弃单，下一拍重试）
    现价 > band_max：未触带未破带（挂单等回落）→ 无动作不输出（防每拍空唤醒，
    TTL 到期自然走 expired）；band 缺失（异常态）→ fail-closed 行不动作。
    本函数只输出触发行不执行——fill/expire CLI 是执行与最终防线（带内判定、
    TTL fail-closed、E3 行情防线、E9 band_break 核价都在那边复验）。
    持久输出语义同 [SLEEVE] 行：动作完成前每拍重复 → monitor 持续唤醒直到闭环。
    """
    if not os.path.exists(POOL_DB) or not in_price_scan_window():
        return []
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        slots = conn.execute(
            "SELECT event_key, band_min, band_max, order_ttl FROM event_slots "
            "WHERE status='pending_order' ORDER BY event_key").fetchall()
    finally:
        conn.close()
    now = datetime.now()
    out = []
    for s in slots:
        event_key = s["event_key"]
        if s["band_min"] is None or s["band_max"] is None:
            out.append(f"[PRICE-ORDER] {event_key} 挂单带缺失（band_min/max=NULL，异常态）"
                       f"→ fail-closed 本轮跳过（不成交不弃单）")
            continue
        ttl_dt = _parse_order_ttl(s["order_ttl"])
        if ttl_dt is not None and now > ttl_dt:
            out.append(f"[PRICE-ORDER] {event_key} 挂单已过期 ttl={s['order_ttl']} "
                       f"→ 跑 ptrade2 sleeve-order-expire {event_key} --reason expired")
            continue
        code = _slot_member_code(event_key)
        px = fetch_price_any(code) if code else None
        if px is None:
            out.append(f"[PRICE-ORDER] {event_key} 取价失败（code={code or '无成员段'}）"
                       f"→ 本轮跳过，不成交不弃单（fail-closed，下一拍重试）")
            continue
        if s["band_min"] <= px <= s["band_max"]:
            out.append(f"[PRICE-ORDER] {event_key} 现价¥{px:.2f} ∈ 带"
                       f"[¥{s['band_min']:.2f},¥{s['band_max']:.2f}] → 跑 ptrade2 "
                       f"sleeve-order-fill {event_key} --price {px:.2f}")
        elif px < s["band_min"]:
            out.append(f"[PRICE-ORDER] {event_key} 现价¥{px:.2f} < "
                       f"band_min¥{s['band_min']:.2f} → 跑 ptrade2 "
                       f"sleeve-order-expire {event_key} --reason band_break")
    return out


def check_orphan_slots() -> list[str]:
    """孤儿槽检测（2026-09-04 断链修复）：open + fill_status='pending' + band 缺失的槽。

    v12 迁移断链产物——晨审/旧链路 sleeve-open 建槽后没接 sleeve-order-place，
    槽停在 open/pending、band_min/max=NULL、order_id=NULL：check_price_orders 只扫
    status='pending_order' 永远看不到 → 静默永不成交（9/4 ND#553/ND#407 卡 2.5h 根因）。
    本检测对任何此类槽持续输出 → monitor 变化 → 唤醒 agent 补挂单（sleeve-order-place）
    或正确处置（勿弃单勿成交——根本没挂单）。修复后同型槽不再产生，本函数变 IDLE。
    """
    if not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        slots = conn.execute(
            "SELECT event_key FROM event_slots "
            "WHERE status='open' AND fill_status='pending' "
            "AND (band_min IS NULL OR band_max IS NULL) ORDER BY event_key").fetchall()
    finally:
        conn.close()
    return [f"[ORPHAN-SLOT] {s['event_key']} 开槽未挂单（open/pending 且 band=NULL，v12 断链）"
            f"→ 补跑 ptrade2 sleeve-order-place {s['event_key']} --anchor <成员昨收/现价> "
            f"--ttl <最近交易节收盘>（成员 code 用 _slot_member_code 查实，勿错锚）"
            for s in slots]


def run_price_scope() -> int:
    """price scope 主流程（v12 C1 price-watch 心跳 monitor）：E1 挂单槽四态扫描
    + E6 保护链扫描（承接 legacy check_price_triggers 全账户扫，含 strategy='NEWS'
    成员段——成交后保护链归属的延续监督，触发写 WATCH_ALERT 供 C1 消费执行）。
    静默 news 检出/任务列举/atr/异动/大盘等——专用心跳只看价格输出，防串唤醒。"""
    lines = []
    lines.extend(check_price_orders())      # E1：挂单槽触带/破带/过期/取价失败
    lines.extend(check_orphan_slots())      # E1b：孤儿槽（开槽未挂单，v12 断链兜底）
    lines.extend(check_price_triggers())    # E6：保护链（全账户含 NEWS 段）
    lines.extend(check_watch_points())      # E7：技术组 watchpoint buy/eval 触发（2026-09-04
                                            #   C1 吸收 legacy——C1=唯一股价盯盘心跳，脚本前置触发
                                            #   →WATCH_ALERT 事件供 C1 消费核验/复检）
    lines.extend(check_naked_conditions())  # E8：裸奔告警（无保护链的实际持仓段，同属股价盯盘）
    lines.extend(atr_sync_daily())          # E9：每日首次交易 tick 止损位同步（2026-09-04 随
                                            #   ATR 归价格域从 legacy 迁入——纯脚本，成功静默
                                            #   失败告警唤醒 C1；止损位=保护链数据）
    lines.extend(scan_moves())              # E10：池内个股动量甜点/追高/单日异动（2026-09-04
                                            #   从 legacy 迁入——纯价格扫描，状态机滞回防反复
                                            #   唤醒；检出→C1 agent 初过滤原因）
    lines.extend(check_market_shock())      # E11：大盘指数异动（2026-09-04 从 legacy 迁入——
                                            #   指数也是价格；触发写 MARKET_SHOCK 事件，同交易日
                                            #   去重；消费=news-collect 深挖）
    if not lines:
        print("IDLE")
        return 0
    print("\n".join(lines))
    return 0


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
    # v12 scope 分流（方案 review 修订#7）：
    #   --scope news   → 消息挂单专用心跳 monitor（只检 newsdb 新事件，静默 SLEEVE 与一切 legacy 检测）
    #   --scope price  → 挂单执行心跳 monitor（v12-patch/E1：挂单槽四态扫描 + E6 保护链）
    #   --scope legacy（默认，兼容现网 cron 无参调用）→ 旧全量逻辑原样，含 SLEEVE_FILL/[SLEEVE]
    scope = "legacy"
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--scope" and i + 1 < len(args):
            scope = args[i + 1]
        elif a.startswith("--scope="):
            scope = a.split("=", 1)[1]
    if scope not in ("news", "legacy", "price"):
        print(f"❌ --scope 应为 news / legacy / price（收到 {scope!r}）", file=sys.stderr)
        return 2
    if scope == "news":
        return run_news_scope()
    if scope == "price":
        return run_price_scope()
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
    lines.extend(check_sleeve_fill_event())   # SLEEVE_FILL 事件入队（到期 pending 槽）
    lines.extend(check_sleeve_pending())
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
