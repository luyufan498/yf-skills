#!/usr/bin/env python3
"""watch_scan.py — 心跳监控脚本（Hermes cron monitor-script 模式）

三 scope 分流（v12，main() 按 --scope 参数路由，详见各 run_*_scope）：
1. --scope news：消息组专用心跳（msg-watch 消费）——newsdb 新事件检出 → MSG_CANDIDATE，
   静默一切价格/legacy 检测。
2. --scope price：价格组专用心跳（C1 消费）——挂单槽四态扫描（E1）、保护链/裸奔/ATR
   同步/动量异动/大盘异动（E6-E11），交易时段闸内拍首批量预取价。
3. --scope legacy（默认，兼容现网旧 cron 无参调用）：旧全量逻辑原样保留（含 SLEEVE_FILL/
   [SLEEVE]），可回滚但不再演进。

legacy scope 每个 tick（30 分钟，零 LLM 成本）：
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
import fcntl
import sqlite3
import subprocess
import sys
import time
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

# P3（2026-09-04）：进程内直读 market.db 日K缓存需要 paper_trading_v2（与
# _earliest_fill_allowed 的 import 同一 editable 安装目录，不在默认 sys.path——
# 补路径后 _fetch_cached_closes 可进程内纯读；import 仍失败时退回
# fetch-kline-cached 子进程，行为不劣化）。
_PAPER_SCRIPTS = os.path.join(os.path.dirname(STOCK_WS_ROOT),
                              "yf-skills", "stock-toolkit", "skills",
                              "paper-trading", "scripts")
if os.path.isdir(_PAPER_SCRIPTS) and _PAPER_SCRIPTS not in sys.path:
    sys.path.insert(0, _PAPER_SCRIPTS)


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
# P3（2026-09-04）：run_price_scope 改为拍首一次 fetch_prices_batch 批量预取填充，
# E6/E7/E8 的 fetch_price 退化为纯字典读（缺失才回退旧的逐票子进程路径）。
_PRICE_CACHE: dict[str, float] = {}
# P3 批量昨收缓存：fetch_prices_batch 填充，scan_moves 的 day_chg 直接消费
# （昨收=交易所除权调整后的官方口径，比 K 线取昨日 bar 更准）。
_PRE_CLOSE_CACHE: dict[str, float] = {}
# P3 本拍批量快照：{归一码: {'price': x, 'pre_close': y}}，fetch_prices_batch 填充；
# E11 fetch_index_day_chg 直接读（_PRICE_CACHE 只有价，day_chg 还需昨收）。
_QUOTE: dict[str, dict] = {}


def _clear_price_cache() -> None:
    _PRICE_CACHE.clear()
    _PRE_CLOSE_CACHE.clear()
    _QUOTE.clear()


def _normalize_code(code: str) -> str:
    """候选码归一为 sh/sz+6 位前缀码（腾讯批量接口只认前缀码）。

    裸 6 位 / 点后缀（600703.SH / 601138）直接进 fetch-prices 会被静默丢弃
    （2026-09-04 实测：批量 '601138,600703.SH,sz002151' 只回 2 只）——批量收集前
    先归一。裸码交易所前缀启发式与 fetch_price_any 同款（5/6/8/9 开头→sh）。
    """
    c = (code or "").strip()
    m = re.match(r"^(sh|sz|bj)(\d{6})$", c, re.I)
    if m:
        return f"{m.group(1).lower()}{m.group(2)}"
    if re.search(r"\.(SH|SZ|BJ)$", c, re.I):
        digits, suf = c.split(".")[0], c.split(".")[-1].lower()
        return f"{suf}{digits}"
    if re.match(r"^\d{6}$", c):
        return ("sh" if c[0] in "5689" else "sz") + c
    return c.lower()


def fetch_prices_batch(codes: list[str]) -> dict[str, dict]:
    """批量实时价（P3，2026-09-04）：subprocess ptrade2 fetch-prices '<逗号连接>'
    --format json → {code: {'price': x, 'pre_close': y}}。

    - 一次网络往返拿全部需价票（腾讯原生批量；50 码实测 ~1.0s 含解释器启动）；
    - 成功的票同步填充 _PRICE_CACHE / _PRE_CLOSE_CACHE：E6/E7/E8 的 fetch_price
      变纯字典读、E10 拿 today/pre_close、E11 拿指数涨跌幅；
    - 失败（CLI 缺失/超时/非 JSON/空响应）→ 返回 {}，调用方降级不崩：各检测经
      fetch_price 回退旧的逐票子进程路径；单票缺失不影响其他票
      （fetch-prices 内部容错，返回数组里缺谁就是谁失败）。
    """
    norm_codes = sorted({_normalize_code(c) for c in codes if c and str(c).strip()})
    if not norm_codes:
        return {}
    out = ptrade2("fetch-prices", ",".join(norm_codes), "--format", "json", timeout=60)
    result: dict[str, dict] = {}
    try:
        items = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(items, list):
        return {}
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip().lower()
        if not code:
            continue
        try:
            px = float(it["current_price"]) if it.get("current_price") is not None else None
            pre = float(it["pre_close"]) if it.get("pre_close") is not None else None
        except (TypeError, ValueError):
            continue
        if px is None or px <= 0:  # 停牌/脏 0 价 → 视同该票失败，走逐票兜底
            continue
        result[code] = {"price": px, "pre_close": pre}
        _PRICE_CACHE[code] = px
        _QUOTE[code] = {"price": px, "pre_close": pre}
        if pre:
            _PRE_CLOSE_CACHE[code] = pre
    return result


def fetch_pre_close(code: str) -> float | None:
    """昨收（P3）：只读批量 dict 填充的 _PRE_CLOSE_CACHE；缺失返回 None。

    昨收没有逐票兜底来源（旧 K 线路径已退役）——调用方对 None 跳过该票本轮。
    """
    return _PRE_CLOSE_CACHE.get(_normalize_code(code))


def fetch_price(code: str) -> float | None:
    if code in _PRICE_CACHE:
        return _PRICE_CACHE[code]
    # P3：批量预取按归一码落缓存，混合格式码（裸 6 位/点后缀）查询先归一再命中
    norm = _normalize_code(code)
    if norm != code and norm in _PRICE_CACHE:
        px = _PRICE_CACHE[norm]
        _PRICE_CACHE[code] = px  # 原键回填，后续同码查询免归一
        return px
    out = ptrade2("fetch-price", code)
    m = re.search(r"当前价格:\s*¥([\d.]+)", out)
    px = float(m.group(1)) if m else None
    if px is not None:
        _PRICE_CACHE[code] = px
        if norm != code:
            _PRICE_CACHE[norm] = px
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
    note        TEXT,
    creator     TEXT NOT NULL DEFAULT '',
    handled_at  TEXT,
    handled_by  TEXT
);
CREATE TABLE IF NOT EXISTS kv_store (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""

# C1（WP1）失败码分流：连续失败计数 kv_state 键名（{cond_uid: {"code":…, "count":N}}）。
# 文档：insufficient_funds/insufficient_shares 连续 3 次 → 条件 suspended + 升级；
# error 重试 ≤2 次 → 升级。计数持久化到 watch_scan_state["cond_exec_fails"]（重启不丢）。
COND_EXEC_FAILS_KEY = "cond_exec_fails"
SUSPEND_AFTER_FAILS = 3          # 资金类连续失败挂起阈值（B5：防 15min 一轮无限洪泛）
ERROR_ESCALATE_AFTER = 2         # error 重试耗尽阈值（重试 ≤2 次后升级）
SUCCESS_RESETS_FAILS = True      # 成功执行后清零该条件失败计数
COND_EXEC_TICK_WINDOW = 600.0    # 同拍窗口（秒）：窗口内重复失败不重复计数（防 cron/手动重叠双计）


def _handled_columns_ensure(db_path: str | None = None) -> sqlite3.Connection:
    """C2（WP3）：task_events.handled_at/handled_by 列幂等迁移（与 B 的 creator 同法）。

    旧库（B 批前建的表，无 handled 列）连库自动补列；新库 TASKS_SCHEMA 已含列。
    返回连接（调用方负责 close）。"""
    p = db_path or TASKS_DB
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p)
    conn.executescript(TASKS_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    for col in ("creator", "handled_at", "handled_by"):
        if col not in cols:
            if col == "creator":
                conn.execute("ALTER TABLE task_events ADD COLUMN creator TEXT NOT NULL DEFAULT ''")
            else:
                conn.execute(f"ALTER TABLE task_events ADD COLUMN {col} TEXT")
    conn.commit()
    return conn


def _ensure_task_table():
    """确保任务表存在（脚本独立运行时不依赖 taskbus init）。"""
    conn = _handled_columns_ensure(TASKS_DB)
    conn.close()


def _resolve_cond_uid(db_path: str, cond_id: int) -> str | None:
    """C4（WP4）：条件行 id → cond_uid（行 id 随 conditions_manager.save DELETE+INSERT
    漂移，cond_uid 跨 save 稳定；去重键=(entity, cond_uid)）。查不到 → None。"""
    if not cond_id or cond_id <= 0 or not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT cond_uid FROM conditions WHERE id=?", (cond_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    uid = (row[0] or "").strip() if row else ""
    return uid or None


def _is_full_exit(action: str, name: str) -> bool:
    """C1 审计补丁（2026-09-10 主代理 R1.5）：卖出数量是否"清仓"语义。

    机械腿唯一可安全直调的情形。生产库 115 条卖出/保护类 active 条件实测：
    38 条明写"清仓"（--all 正确）、60 条明写比例（"次日卖出1/3"/"减半"/"50%"）、
    17 条文本模糊（"执行"/"移动止损-2.5ATR"）——一律 --all 会超卖 2–3 倍
    （打桩重放：寒武纪 TP1 action="次日卖出1/3（收盘确认触发）" 被下成 sell --all）。
    非清仓语义 → 不直调，退回 agent 路径（事件保持 pending）。
    """
    text = f"{action or ''} {name or ''}"
    return any(k in text for k in ("清仓", "全清", "全部卖出", "清空"))


def _condition_created_by(db_path: str, cond_id: int) -> str:
    """C1/C2：条件创建者（A 批 conditions.created_by 列；空 → '' 由调用方 fail-closed）。"""
    uid_row = None
    if not cond_id or cond_id <= 0 or not os.path.exists(db_path):
        return ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            uid_row = conn.execute("SELECT created_by FROM conditions WHERE id=?",
                                   (cond_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return ""
    return (uid_row[0] or "").strip() if uid_row else ""


# ---------- 1. 任务事件检查 ----------
def check_tasks() -> list[dict]:
    """待消费事件（排除 CALENDAR：定时回查由 analysis_watch_monitor.query_calendar_lines
    到期才输出，未到期不唤醒；本脚本的 check_calendar 检测已于 2026-09-06 移除）。

    v12：MSG_CANDIDATE/MSG_ORDER/MSG_REJUDGE/MSG_EXPIRE 也不列——消息链路四类型
    唯一消费者=专用心跳 msg-watch（claim 硬门见 task_bus/db.py），legacy 心跳只
    发现不消费，排除防止旧心跳被唤醒误 claim（prompt 热换前的双保险）。"""
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
            "'MSG_CANDIDATE','MSG_ORDER','MSG_REJUDGE','MSG_EXPIRE',"
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

    2026-09-10 批量化：一次 `ptrade2 atr-sync --format json`（不带股票名 → CLI
    内部遍历全部账户）替代逐票子进程——实测 31 票 19.5s → 2.8s。逐票判定取自
    results[]（status ok/skip 不算失败，其余逐个出告警）；批量失败/JSON 不可解析
    时**回退逐票子进程**（原路径行为逐字不变）。
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
    alerts: list[str] = []
    batch_ok = False
    try:
        out = ptrade2("atr-sync", "--format", "json", timeout=300)
        data = json.loads(out)
        items = data.get("results") if isinstance(data, dict) else data
        if isinstance(items, list):
            batch_ok = True
            for it in items:
                if not isinstance(it, dict):
                    continue
                status = str(it.get("status") or "")
                if status in ("ok", "skip"):
                    continue
                alerts.append(
                    f"⚠️ {it.get('stock') or '?'} atr-sync 失败（止损位未同步，需人工核验）: "
                    f"{status} {it.get('reason') or ''}"[:160])
    except Exception:
        batch_ok = False
    if not batch_ok:
        # 回退：逐票子进程（2026-09-02 半盲修复的判据原样保留）
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
    """去重：同实体同条件已有 pending/processing 事件则跳过（C4/WP4）。

    去重键从 payload LIKE '%"cond_id": <行id>%' 改为 **(entity, cond_uid)**——
    行 id 随 conditions_manager.save DELETE+INSERT 漂移（实测 id 范围 4–2878 而
    现存 189 行），同 cond_uid 行 id 变化后旧键查不到 → 重复入队。cond_uid 跨
    save 稳定（非全局唯一：sleeve 段 cond_uid=type 名跨 18 账户），故必须带实体。
    窗口明文 = pending + processing（failed 即释放窗口）。
    cond_id 解析不出 cond_uid（条件已删/行 id 失效）→ 退回 entity+direction 宽键。
    """
    if not os.path.exists(TASKS_DB):
        return False
    conn = sqlite3.connect(TASKS_DB)
    try:
        if cond_id and cond_id > 0:
            cond_uid = _resolve_cond_uid(POOL_DB, cond_id)
            if cond_uid:
                row = conn.execute(
                    "SELECT 1 FROM task_events WHERE entity=? AND status IN ('pending','processing') "
                    "AND type='WATCH_ALERT' AND payload LIKE ? LIMIT 1",
                    (entity, f"%\"cond_uid\": \"{cond_uid}\"%"),
                ).fetchone()
                if row is not None:
                    return True
            # cond_uid 不可得 → 宽键兜底（entity+direction，防同向叠加）
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
                 tp_only: bool = False, ref_id: str | None = None,
                 creator: str | None = None) -> bool:
    """写 WATCH_ALERT 事件 + 原子标记条件为 triggered（触发即失效，防重复进入流程）。

    C2（WP3）payload 契约（v3 方案）：
    - exec={result, code, at, price}：直调执行结果回写位（写入时 result='pending'，
      C1 直调后同事件更新 done/failed + 原因码，消费方按 payload 路由）；
    - ref={kind, id}：对象指针——condition → cond_uid（行 id 漂移，uid 跨 save 稳定）、
      watchpoint → wp_id（ref_id 传入时 kind=watchpoint）；
    - creator：对象创建者（trade 模式取 conditions.created_by；watchpoint 由调用方
      传 row.created_by；空 → fail-closed 由消费方晚审+通知）；
    - snapshot：5 字段最小快照 {entity, direction, threshold, price, code}（审计 B3：
      cond_uid 有 NULL/类型名行，纯指针会悬空，快照兜底）。

    manual=True（手动补录路径）：仅当条件仍 active 才允许写入，已 triggered/不存在则拒绝，
    返回 False 由调用方记录跳过原因——防对旧条件重复补录（如"买点上沿"昨已触发今又补）。
    mode="eval"（技术组 L2 待命复检价格点）：不标记 conditions（无账户条件），payload 带 mode 供消费方区分。
    mode="buy"（技术组 L2 建仓点）：同 eval 不碰 conditions，额外带 budget（建仓预算）供消费方 allocate。
    mode="sell"（卖出点，2026-09-04）：同 eval/buy 不碰 conditions；direction=sell + payload.mode=sell
      供 C1 price-watch 消费执行卖仓/减仓（限价卖；挂点语义=触发后心跳 agent 执行）。
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
    cond_uid = _resolve_cond_uid(POOL_DB, cond_id) if cond_id and cond_id > 0 else None
    if creator is None:
        creator = _condition_created_by(POOL_DB, cond_id) if cond_id and cond_id > 0 else ""
    if ref_id:
        ref = {"kind": "watchpoint", "id": ref_id}
    elif cond_uid:
        ref = {"kind": "condition", "id": cond_uid}
    else:
        ref = {"kind": "condition", "id": str(cond_id)} if cond_id and cond_id > 0 \
            else {"kind": "watchpoint", "id": f"{entity}:{mode}"}
    payload = json.dumps({
        "mode": mode, "direction": direction, "cond_id": cond_id, "cond_name": cond_name,
        "trigger_price": trigger_price, "current_price": current_price,
        "budget": budget,
        "cond_uid": cond_uid,
        "exec": {"result": "pending", "code": None,
                 "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                 "price": current_price},
        "ref": ref,
        "creator": creator or "",
        "snapshot": {"entity": entity, "direction": direction, "threshold": trigger_price,
                     "price": current_price, "code": code},
    }, ensure_ascii=False)
    conn = _handled_columns_ensure(TASKS_DB)
    try:
        # F1-⑦ 竞态修复（2026-09-10）：去重检查与 INSERT 必须落在同一个写事务里。
        # 原实现是 check-then-act（_has_pending_event 只读、随后无条件 INSERT），跨进程
        # 同刻命中时两侧都判"无 pending"→ 各写一条事件、各带**自己的** event_id 直调 sell
        # → trades 层按 event_id 的幂等键拦不住 → 真双卖（实测真两进程 20/20 复现）。
        # 事务内以 BEGIN IMMEDIATE 抢写锁后重判：对侧已写 → rollback + 返回 False 让位，
        # 本拍不直调；下一拍走既有 pending 重试路径复用同一条事件 id（幂等键恢复有效）。
        # 事务是进程内毫秒级、异常自动回滚——不持有需要显式释放的锁。
        conn.execute("BEGIN IMMEDIATE")
        # 抢到写锁后再判一次（此刻对侧若已写过，必已 commit 可见）
        if _has_pending_event(entity, direction, cond_id):
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO task_events (type, entity, source, priority, payload, creator) "
            "VALUES ('WATCH_ALERT', ?, 'heartbeat-scan', 1, ?, ?)",
            (entity, payload, creator or ""),
        )
        conn.commit()
    finally:
        conn.close()
    # 触发即失效：防重复进入流程（2026-09-09 精确化，晚审 TRIGGERED-STALE 根因修复）
    # （仅 trade 模式；eval 模式的价格点由调用方负责移除）
    if mode == "trade" and os.path.exists(POOL_DB) and cond_id and cond_id > 0:
        _mark_triggered_family(cond_id, cond_name, direction, current_price, tp_only)
    return True


def _mark_triggered_family(cond_id: int, cond_name: str, direction: str,
                           current_price, tp_only: bool) -> None:
    """触发即失效标记 + 留痕（2026-09-09 精确化）。

    旧版无差别连坐（sell 方向 `OR category='hard'`）把该段**全部** active 硬条件标
    triggered——含现价根本没碰到的止盈阶梯线；而 sync_take_profit_ladder 对
    active/triggered 幂等跳过 → 阶梯永久失效（恒申 2026-09-07 实证：TP 10.40/12.00
    在现价 7.5 被标掉，晚审连报 TRIGGERED-STALE）。现按语义分档：

    - tp_only：只标本条件（阶梯卖 1/3 后仓位存续，禁连坐）——同 2026-08-30 语义
    - buy 方向：沿用旧过滤（建仓/买入/加仓关键词）
    - 清仓类触发（action/name 含"清仓"）：保留旧语义整段全标——仓位将归零，
      全标防同一次破位重复下单；闭仓后 migrate 6b 归档 + atr-sync 空仓跳过会清干净
    - 其余卖出（减仓/止损/止盈/保护）：**只标本次价格已突破的线**
      （止损/保护线 price>=现价；止盈线 price<=现价）+ 本次触发线
    - 现价缺失：降级只标本次触发线并告警（不阻塞告警主流程）

    同时写 modified_at + condition_history（旧版无痕写，审计无法归因）。
    """
    conn = sqlite3.connect(POOL_DB)
    conn.row_factory = sqlite3.Row
    try:
        trig = conn.execute("SELECT account_id, action, name FROM conditions WHERE id=?",
                            (cond_id,)).fetchone()
        if not trig:
            return
        aid = trig["account_id"]
        trig_text = f"{trig['action'] or ''}{trig['name'] or ''}{cond_name or ''}"
        if tp_only:
            where, params = "id=?", [cond_id]
        elif direction == "buy":
            where = "(action LIKE '%建仓%' OR action LIKE '%买入%' OR action LIKE '%加仓%')"
            params = []
        elif "清仓" in trig_text:
            where = ("(action LIKE '%清仓%' OR action LIKE '%减仓%' OR action LIKE '%止损%' "
                     "OR action LIKE '%止盈%' OR category='hard')")
            params = []
        elif current_price is None:
            print(f"⚠️ 触发失效降级：{cond_name} 现价缺失，只标条件#{cond_id}", file=sys.stderr)
            where, params = "id=?", [cond_id]
        else:
            where = ("((type IN ('trailing_stop','cost_protection') AND price >= ?) "
                     "OR (type LIKE 'take_profit%' AND price <= ?) OR id=?)")
            params = [current_price, current_price, cond_id]
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM conditions WHERE account_id=? AND status='active' AND {where}",
            (aid, *params)).fetchall()]
        if not ids:
            return
        now = datetime.now().isoformat()
        ph = ",".join("?" * len(ids))
        conn.execute(f"UPDATE conditions SET status='triggered', modified_at=? "
                     f"WHERE id IN ({ph})", (now, *ids))
        for cid in ids:
            conn.execute(
                "INSERT INTO condition_history (condition_id, old_price, new_price, reason, "
                "timestamp, level, override_triggers) "
                "SELECT id, price, ?, ?, ?, 'auto', '[]' FROM conditions WHERE id=?",
                (current_price, f"触发即失效（{cond_name} 触发，现价 ¥{current_price}）",
                 now, cid))
        conn.commit()
    finally:
        conn.close()


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


def _parse_exec_fail_code(out: str) -> str:
    """C1（WP1）：ptrade2 sell/buy CLI 输出/返回码 → 原因码（失败分流依据）。

    判据从 CLI 输出解析（空输出/超时/异常 → 'error'）：
    - '报价陈旧'/'stale' → stale_quote；'停牌' → halted；'资金不足' → insufficient_funds；
    - '持仓不足' → insufficient_shares；'无持仓' → no_position；'已成交（幂等拒绝）' → already_fulfilled；
    - 其余非空非 ✅ 输出 → error；'✅' 开头 → 空（成功，不进分流）。
    """
    t = (out or "").strip()
    if not t or "Error" in t[:32] and "❌" not in t:
        return "error"
    if "报价陈旧" in t or "stale" in t.lower():
        return "stale_quote"
    if "停牌" in t or "halted" in t.lower():
        return "halted"
    if "资金不足" in t or "insufficient_funds" in t:
        return "insufficient_funds"
    if "持仓不足" in t or "insufficient_shares" in t:
        return "insufficient_shares"
    if "无持仓" in t or "no_position" in t:
        return "no_position"
    if "已成交" in t and "幂等拒绝" in t or "already_fulfilled" in t:
        return "already_fulfilled"
    return "error"


def _record_cond_exec_fail(cond_uid: str, code: str) -> int:
    """C1：连续失败计数（kv_state watch_scan_state[COND_EXEC_FAILS_KEY]，持久化重启不丢）。

    同码连击 +1；换码重置为 1（连续=同一原因码连续出现）。返回当前连击数。
    拍次守卫（2026-09-10 审计补）：同一拍窗口内重复调用（cron 与手动 --scope price
    重叠）不重复计数——上次计数时间在 COND_EXEC_TICK_WINDOW 秒内 → 返回现值不加。"""
    st = load_state()
    fails = st.get(COND_EXEC_FAILS_KEY) or {}
    ent = fails.get(cond_uid) or {}
    now = time.time()
    last_at = ent.get("ts_epoch") or 0
    if ent.get("code") == code and (now - last_at) < COND_EXEC_TICK_WINDOW:
        return ent.get("count", 1)  # 同拍重复调用不重复计数
    n = ent.get("count", 0) + 1 if ent.get("code") == code else 1
    fails[cond_uid] = {"code": code, "count": n, "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                       "ts_epoch": now}
    st[COND_EXEC_FAILS_KEY] = fails
    save_state(st)
    return n


def _reset_cond_exec_fail(cond_uid: str):
    """C1：成功执行后清零该条件连续失败计数（SUCCESS_RESETS_FAILS）。"""
    if not SUCCESS_RESETS_FAILS or not cond_uid:
        return
    st = load_state()
    fails = st.get(COND_EXEC_FAILS_KEY) or {}
    if cond_uid in fails:
        fails.pop(cond_uid, None)
        st[COND_EXEC_FAILS_KEY] = fails
        save_state(st)


def _update_alert_exec(event_id: int, result: str, code: str | None, price: float,
                       status: str | None = None, handled_by: str = "watch-scan") -> None:
    """C1/C2：同事件回写 exec={result, code, at, price}（直调结果落 payload，留痕）。

    status 传入时同步改事件状态（done=成功留痕闭环 / failed=终态失败 / None=保持
    pending 重试车辆）。handled_at/handled_by 独立列（WP3：防双消费者互覆 JSON）。"""
    if not os.path.exists(TASKS_DB) or not event_id:
        return
    conn = _handled_columns_ensure(TASKS_DB)
    try:
        row = conn.execute("SELECT payload FROM task_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            return
        try:
            p = json.loads(row[0]) if row[0] else {}
        except (ValueError, TypeError):
            p = {}
        p["exec"] = {"result": result, "code": code,
                     "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "price": price}
        # 审计补丁（2026-09-10 主代理 R1.5）：handled_at/handled_by 是**消费方**处置标记
        # （tech-watch/msg-watch 处置完才写）；执行方写会让"未处置失败"查询永远查不到
        # → 失败静默丢失。执行时间已由 exec.at 承载。
        if status:
            conn.execute("UPDATE task_events SET payload=?, status=? WHERE id=?",
                         (json.dumps(p, ensure_ascii=False), status, event_id))
        else:
            conn.execute("UPDATE task_events SET payload=? WHERE id=?",
                         (json.dumps(p, ensure_ascii=False), event_id))
        conn.commit()
    finally:
        conn.close()


def check_price_triggers() -> list[str]:
    """读 active 条件 vs 实时价，穿越触发 → 同拍直调执行（C1/WP1，2026-09-10）。

    旧版：只写 WATCH_ALERT 等 agent 认领（判断腿延迟 + 机械腿空转）。
    新版（v3 方案 WP1）：检测命中 → **同拍** `ptrade2 sell/buy <股> <数量> --price <检测价>
    --event-id <事件id>`（成交价=检测价，E3 行情防线在 CLI 内）；事件仍写 WATCH_ALERT
    （留痕/升级通道），不再"等 agent 认领"。无 60s SLO、无人工门。

    失败分流（机械，从 CLI 输出解析原因码 → payload.exec）：
    - stale_quote|halted（防线拒）→ 条件保持 active，下一拍重试，不升级（事件留 pending 重试）；
    - insufficient_funds|insufficient_shares → 连续 3 次 → 条件置 suspended + 升级告警
      （计数持久化 kv_state watch_scan_state["cond_exec_fails"]，B5 防洪泛）；
    - no_position|already_fulfilled → 条件归档（archived），事件 exec.result=done；
    - error（异常/超时/未知）→ 重试 ≤2 次 → 升级（条件保持 active，事件留 pending）。
    - creator 为空（fail-closed，v3 §1.1）→ 不直调执行，事件保持 pending（晚审+通知）。
    卖出数量：保护线/止损类=清仓 --all；买入类=预算金额（budget 无则 5 万缺省）。
    """
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
        cond_uid = _resolve_cond_uid(POOL_DB, r["id"]) or f"cond:{r['id']}"
        arrow = "≥" if is_up else "≤"
        # ---- C1 重试路径：pending 事件作重试载体（上一拍 exec 失败可重试码）----
        if _has_pending_event(r["stock_name"], direction, r["id"]):
            retry = _pending_retry_event(r["stock_name"], cond_uid)
            if retry is None or retry.get("code") not in RETRYABLE_CODES \
                    or not _cond_active(r["id"]):
                continue  # 已有同向/同条件待处理事件（正常去重），跳过
            ev_id = retry["id"]
            # 重试不再写新事件（同事件留痕，exec 更新计次）
            creator = retry.get("creator") or ""
            if creator == "":
                triggers.append(
                    f"⚠️ {r['stock_name']}({r['stock_code']}) {direction.upper()} 重试被拒："
                    f"creator 为空（fail-closed，事件#{ev_id} 待晚审）")
                continue
            out = ptrade2("sell" if direction == "sell" else "buy", r["stock_name"],
                          *(["--all"] if direction == "sell" else ["50000"]),
                          "--price", f"{price:.2f}", "--event-id", str(ev_id), timeout=90)
            ok = bool(out) and "✅" in out
            if ok:
                _update_alert_exec(ev_id, "done", "ok", price, status="done")
                _reset_cond_exec_fail(cond_uid)
                triggers.append(
                    f"⚡ {r['stock_name']}({r['stock_code']}) {direction.upper()} 重试成交: "
                    f"现价¥{price:.2f} {arrow} 条件¥{r['price']:.2f}（事件#{ev_id} done）")
            else:
                code = _parse_exec_fail_code(out)
                n = _record_cond_exec_fail(cond_uid, code)
                _route_exec_fail(r, direction, price, action, cond_uid, ev_id, code, n, triggers)
            continue
        if not _write_alert(r["stock_name"], r["stock_code"], direction, r["id"],
                            action, r["price"], price, tp_only=is_up):
            continue  # 条件已非 active（极端竞态），跳过
        # ---- C1 同拍直调（事件已写，取回 id 后带 --event-id 执行）----
        ev_id, creator, ev_payload = None, "", "{}"
        try:
            _conn = sqlite3.connect(TASKS_DB)
            try:
                row = _conn.execute(
                    "SELECT id, creator, payload FROM task_events WHERE type='WATCH_ALERT' "
                    "AND entity=? AND status='pending' ORDER BY id DESC LIMIT 1",
                    (r["stock_name"],)).fetchone()
            finally:
                _conn.close()
            if row:
                ev_id, creator, ev_payload = row[0], row[1] or "", row[2] or "{}"
        except sqlite3.Error:
            creator = ""
        if creator is None or creator == "":
            # fail-closed（v3 §1.1）：无创建者 → 不执行，事件保持 pending 晚审+通知
            triggers.append(
                f"⚠️ {r['stock_name']}({r['stock_code']}) {direction.upper()} 触发但 creator 为空"
                f"（fail-closed，事件#{ev_id} 待晚审+通知，不直调执行）")
            continue
        if direction == "sell" and not _is_full_exit(action, cname):
            # 审计补丁（2026-09-10 主代理 R1.5）：数量语义非"清仓"（1/3、减半、模糊文本）
            # → 机械腿下不准数量，禁止直调，退回 agent 路径（事件保持 pending，
            # 记 exec.result=deferred_agent 留痕；不得写 handled_at——那是消费方标记）。
            _update_alert_exec(ev_id, "deferred_agent", "qty_not_full_exit", price)
            triggers.append(
                f"📤 {r['stock_name']}({r['stock_code']}) SELL 命中但数量语义非清仓"
                f"（action=「{action}」）→ 不直调，交 agent 消费（事件#{ev_id} 保持 pending）")
            continue
        if direction == "sell":
            out = ptrade2("sell", r["stock_name"], "--all",
                          "--price", f"{price:.2f}", "--event-id", str(ev_id), timeout=90)
        else:
            budget = None
            try:
                budget = json.loads(ev_payload or "{}").get("budget")
            except (ValueError, TypeError):
                budget = None
            amt = f"{budget:,.0f}" if budget else "50000"
            out = ptrade2("buy", r["stock_name"], amt,
                          "--price", f"{price:.2f}", "--event-id", str(ev_id), timeout=90)
        ok = bool(out) and "✅" in out
        if ok:
            _update_alert_exec(ev_id, "done", "ok", price, status="done")
            _reset_cond_exec_fail(cond_uid)
            triggers.append(
                f"⚡ {r['stock_name']}({r['stock_code']}) {direction.upper()} 同拍直调成交: "
                f"现价¥{price:.2f} {arrow} 条件¥{r['price']:.2f} [{action}]（事件#{ev_id} done）")
            continue
        code = _parse_exec_fail_code(out)
        n = _record_cond_exec_fail(cond_uid, code)
        _route_exec_fail(r, direction, price, action, cond_uid, ev_id, code, n, triggers)
    return triggers


RETRYABLE_CODES = ("stale_quote", "halted", "insufficient_funds", "insufficient_shares", "error")


def _pending_retry_event(entity: str, cond_uid: str) -> dict | None:
    """C1：查同 (entity, cond_uid) pending 事件的 exec（重试载体）。返回 {id, code, creator}。"""
    if not os.path.exists(TASKS_DB):
        return None
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, creator, payload FROM task_events WHERE type='WATCH_ALERT' "
            "AND entity=? AND status='pending' ORDER BY id DESC LIMIT 10",
            (entity,)).fetchall()
        for row in rows:
            try:
                p = json.loads(row["payload"] or "{}")
            except (ValueError, TypeError):
                continue
            if p.get("cond_uid") and cond_uid and str(p.get("cond_uid")) == str(cond_uid):
                ex = p.get("exec") or {}
                return {"id": row["id"], "code": ex.get("code"),
                        "creator": row["creator"] or p.get("creator") or ""}
        return None
    finally:
        conn.close()


def _route_exec_fail(r, direction: str, price: float, action: str, cond_uid: str,
                     ev_id: int, code: str, n: int, triggers: list) -> None:
    """C1 失败分流（机械）：按原因码路由（条件终态 + 事件 exec + 告警行）。

    - stale_quote|halted → 条件恢复 active，下一拍重试，不升级；
    - insufficient_funds|insufficient_shares → 连续 3 次 → suspended + 升级，否则恢复 active 重试；
    - no_position|already_fulfilled → 条件归档（archived），事件 exec.result=done；
    - error → 重试 ≤2 次（n>2）→ 升级，否则恢复 active 重试。
    """
    if code in ("stale_quote", "halted"):
        _restore_active(r["id"], cond_uid, f"exec失败恢复（{code}，下一拍重试）")
        _update_alert_exec(ev_id, "failed", code, price)  # 事件留 pending 作重试载体
        triggers.append(
            f"🔁 {r['stock_name']}({r['stock_code']}) {direction.upper()} 直调被拒[{code}]: "
            f"现价¥{price:.2f} 条件¥{r['price']:.2f} [{action}] → 条件保持 active 下一拍重试")
    elif code in ("insufficient_funds", "insufficient_shares"):
        _restore_active(r["id"], cond_uid, f"exec失败恢复（{code}）")
        if n >= SUSPEND_AFTER_FAILS:
            _suspend_condition(r["id"], cond_uid, f"连续 {n} 次 {code}")
            _update_alert_exec(ev_id, "failed", code, price, status="failed")
            triggers.append(
                f"🚨 {r['stock_name']}({r['stock_code']}) {direction.upper()} 连续 {n} 次 {code}"
                f" → 条件置 suspended + 升级（事件#{ev_id}，需人工核资金/仓位）")
        else:
            _update_alert_exec(ev_id, "failed", code, price)
            triggers.append(
                f"🔁 {r['stock_name']}({r['stock_code']}) {direction.upper()} 直调被拒[{code}]"
                f"（{n}/{SUSPEND_AFTER_FAILS}）: 现价¥{price:.2f} [{action}] → 下一拍重试")
    elif code in ("no_position", "already_fulfilled"):
        _archive_condition(r["id"], cond_uid, f"exec失败归档（{code}）")
        _update_alert_exec(ev_id, "done", code, price, status="done")
        _reset_cond_exec_fail(cond_uid)
        triggers.append(
            f"📦 {r['stock_name']}({r['stock_code']}) {direction.upper()} 直调[{code}] → "
            f"条件归档（事件#{ev_id} exec.result=done）")
    else:  # error
        _restore_active(r["id"], cond_uid, "exec失败恢复（error，重试）")
        if n > ERROR_ESCALATE_AFTER:
            _update_alert_exec(ev_id, "failed", code, price, status="failed")
            triggers.append(
                f"🚨 {r['stock_name']}({r['stock_code']}) {direction.upper()} 重试 {n} 次仍 error"
                f" → 升级（事件#{ev_id}，需人工核查）")
        else:
            _update_alert_exec(ev_id, "failed", code, price)
            triggers.append(
                f"🔁 {r['stock_name']}({r['stock_code']}) {direction.upper()} 直调异常[{code}]"
                f"（重试 {n}/{ERROR_ESCALATE_AFTER}）: 现价¥{price:.2f} [{action}] → 下一拍重试")


def _restore_active(cond_id: int, cond_uid: str, reason: str) -> None:
    """C1：直调失败（可重试/资金类）→ 撤销触发即失效标记，恢复条件 active + 留痕。"""
    if not os.path.exists(POOL_DB) or not cond_id or cond_id <= 0:
        return
    conn = sqlite3.connect(POOL_DB)
    try:
        now = datetime.now().isoformat()
        conn.execute("UPDATE conditions SET status='active', modified_at=? WHERE id=?",
                     (now, cond_id))
        conn.execute(
            "INSERT INTO condition_history (condition_id, old_price, new_price, reason, "
            "timestamp, level, override_triggers) "
            "SELECT id, price, price, ?, ?, 'auto', '[]' FROM conditions WHERE id=?",
            (reason, now, cond_id))
        conn.commit()
    finally:
        conn.close()


def _suspend_condition(cond_id: int, cond_uid: str, reason: str) -> None:
    """C1：资金类连续失败 ≥3 → 条件置 suspended + 升级（留痕）。"""
    if not os.path.exists(POOL_DB) or not cond_id or cond_id <= 0:
        return
    conn = sqlite3.connect(POOL_DB)
    try:
        now = datetime.now().isoformat()
        conn.execute("UPDATE conditions SET status='suspended', modified_at=? WHERE id=?",
                     (now, cond_id))
        conn.execute(
            "INSERT INTO condition_history (condition_id, old_price, new_price, reason, "
            "timestamp, level, override_triggers) "
            "SELECT id, price, price, ?, ?, 'auto', '[]' FROM conditions WHERE id=?",
            (reason, now, cond_id))
        conn.commit()
    finally:
        conn.close()


def _archive_condition(cond_id: int, cond_uid: str, reason: str) -> None:
    """C1：no_position/already_fulfilled → 条件归档（archived，义务已履行）。"""
    if not os.path.exists(POOL_DB) or not cond_id or cond_id <= 0:
        return
    conn = sqlite3.connect(POOL_DB)
    try:
        now = datetime.now().isoformat()
        conn.execute("UPDATE conditions SET status='archived', modified_at=? WHERE id=?",
                     (now, cond_id))
        conn.execute(
            "INSERT INTO condition_history (condition_id, old_price, new_price, reason, "
            "timestamp, level, override_triggers) "
            "SELECT id, price, price, ?, ?, 'auto', '[]' FROM conditions WHERE id=?",
            (reason, now, cond_id))
        conn.commit()
    finally:
        conn.close()


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
    """拉指数单日涨跌幅（%）。

    P3（2026-09-04）：改读批量 dict（check_market_shock 预取填充），逐票
    fetch-price 子进程退役。涨跌幅=(price/pre_close-1)*100，round 2——与 CLI
    fetch-price 涨跌幅字段同算法同精度（`{change_percent:.2f}`）；指数批量支持
    2026-09-04 实测（sh000001/sz399001 同批返回）。dict 缺该指数 → None（跳过）。
    """
    info = _QUOTE.get(_normalize_code(code))
    if not info or not info.get("price") or not info.get("pre_close"):
        return None
    return round((info["price"] / info["pre_close"] - 1) * 100, 2)


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


def _closes_need_refresh(code: str, closes: list[float]) -> bool:
    """进程内读库后的新鲜度自检（P3）：bar 不够 / 有缺口 / TTL 过期 → True。

    与 market_cache.fetch_kline_cached 同口径的判定，但纯读零网络——
    返回 True 时才走一次 fetch-kline-cached 子进程自愈（补缺/全量重建）。
    """
    try:
        from paper_trading_v2 import market_cache as _mc
    except Exception:
        return True  # 模块不可用 → 保守判定需刷新（子进程路径自会兜底）
    try:
        db = _mc.market_db_path()
        if not os.path.exists(db):
            return True
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE code=? AND key='last_full_refresh_at'",
                (code,)).fetchone()
        finally:
            conn.close()
        # TTL 判定与 market_cache.fetch_kline_cached 同款（2026-09-09 起按交易日）
        if not row:
            return True
        if _mc.ttl_expired(row[0]):
            return True
        # 缺口判定：bar 数足够 且 最新已收盘交易日已被覆盖
        if len(closes) < 10:
            return True
        need = _mc.last_closed_trading_day()
        conn = sqlite3.connect(db)
        try:
            newest = conn.execute(
                "SELECT MAX(date) FROM kline_daily WHERE code=?", (code,)).fetchone()[0]
            fail_row = conn.execute(
                "SELECT value FROM meta WHERE code=? AND key='last_fetch_fail_at'",
                (code,)).fetchone()
        finally:
            conn.close()
        if newest and newest >= need:
            return False
        # 停牌票节流（P3 实测 sh688432：最后 bar 8/28，缺口永远补不齐）——
        # 当日已试过补抓且源无新数据（last_fetch_fail_at=今天）→ 不再每拍空转自愈，
        # 直接用现有缓存（ten_ago 锚=该票最后实际交易日，与 legacy fetch-kline 同语义）
        if fail_row and str(fail_row[0])[:10] == datetime.now().strftime("%Y-%m-%d"):
            return False
        return True
    except Exception:
        return True


def _parse_kline_pairs(text: str) -> list[tuple[str | None, float]]:
    """'收: X' 行解析（与 fetch-kline/fetch-kline-cached pretty 同构）→ [(date, close)]。

    📅 行给出所属日期（用于剔除今日 bar）；缺日期行的 close 以 None 日期保留
    （日期过滤时按"不早于今日即弃"的保守口径处理不了 → None 日期一律保留，
    仅在极端解析退化的情况下出现）。
    """
    pairs: list[tuple[str | None, float]] = []
    cur_date: str | None = None
    for line in text.splitlines():
        dm = re.search(r"📅\s*(\d{4}-\d{2}-\d{2})", line)
        if dm:
            cur_date = dm.group(1)
        m = re.search(r"收:\s*([\d.]+)", line)
        if m:
            pairs.append((cur_date, float(m.group(1))))
    return pairs


def _qfq_equivalent_closes(code: str, pairs: list[tuple[str | None, float]],
                           today_str: str | None = None) -> list[float] | None:
    """raw 收盘序列 → qfq 等效收盘序列（E10 除权折算，2026-09-04）。

    公式（detect_exright_jumps 语义推导 + sh600000 2026-07-16 10派4.2 实测验证）：
      ratio(d) = qfq(d)/raw(d)，最新 bar re-anchor ratio=1（qfq 锚定最新收盘）；
      factor(除权日 e) = ratio(e)/ratio(e 前一已收盘 bar)。
    除权日之后（含当日）ratio≡1，之前 ratio≡1/factor（多除权累积）。故任意 bar
    的 qfq 等效价 = raw(bar) × ratio(bar) = raw(bar) / ∏factor(e ∈ (bar_d, today])，
    与 market_cache docstring 口径（窗口动量 raw × ∏factor = qfq 动量）等价：
        today / (ten_ago_raw / ∏f) - 1 ≡ today_qfq / ten_ago_qfq - 1
    实测：sh600000 ten_ago(7/15) raw 9.31 / 1.04724409 = 8.889998 == 腾讯 qfq 8.89。
    实现按**日期区间**逐 bar 取后缀事件链（非按 bar 计数）：停牌票长窗口跨多个
    除权自然连乘，无事件 ∏f=1 逐字透传。

    首事件退化（保守 fail-closed）：事件表**首行**的 factor 在检测时可能无 prev bar
    （退化为该日绝对 ratio 而非跳变系数）。判别依据：detect_exright_jumps 仅在
    有 prev bar 时才往 note 写 'jump X'——首行 note 含 'jump ' → 真跳变系数可信
    （如 TTL 全量重建从 250 bar 链内算出）；无 'jump ' → 退化不可信。首行退化
    事件落入折算窗口 → 返回 None（scan_moves 本轮跳过该票，不动状态机）；
    首行在窗口外或首行可信则正常折算。缺口补抓（gap_fill）场景实测会产出
    退化首事件（note 无 jump、factor≈1.0 而真值如 1.047）——本判别正是防它。
    窗口内脏 factor（≤0/非数值）同样 fail-closed 跳过。
    事件表读失败 → fail-open 返回原 raw 序列（=P3 既有行为，不因基础设施故障
    扩大静默面）。当前生产库 3 条事件均为含 prev 的正常跳变（note 带 'jump'）。
    """
    today_str = today_str or datetime.now().strftime("%Y-%m-%d")
    dated = [(d, c) for d, c in pairs if d and d < today_str]
    if not dated:
        return []
    try:
        if _PAPER_SCRIPTS not in sys.path:
            sys.path.insert(0, _PAPER_SCRIPTS)
        from paper_trading_v2 import market_cache as _mc
        events = _mc.read_exright_events(code)
    except Exception:
        return [c for _, c in dated]  # 事件表不可读 → fail-open 维持 raw（P3 行为）
    if not events:
        return [c for _, c in dated]
    start = dated[0][0]
    win: list[tuple[str, float]] = []
    for i, ev in enumerate(events):
        d = str(ev.get("date") or "")
        if not (start < d <= today_str):
            continue
        try:
            f = float(ev.get("factor"))
        except (TypeError, ValueError):
            f = 0.0
        # 首行无 'jump '（无 prev bar 退化为绝对 ratio）或 factor 非法 → fail-closed
        if f <= 0 or (i == 0 and "jump " not in str(ev.get("note") or "")):
            return None
        win.append((d, f))
    if not win:
        return [c for _, c in dated]
    # 逐 bar 后缀折算：新→旧扫，越过事件日即把该 factor 计入折算分母
    out: list[float] = []
    k, ei = 1.0, len(win) - 1
    for d, c in reversed(dated):
        while ei >= 0 and win[ei][0] > d:
            k *= win[ei][1]
            ei -= 1
        out.append(c / k)
    out.reverse()
    return out


def _fetch_cached_closes(code: str) -> list[float] | None:
    """日线收盘价列表（P3：market.db raw 缓存读），**旧→新**（closes[-1]=最近已收盘日）。

    P3（2026-09-04）：逐票 `fetch-kline`（腾讯 qfq 直抓 ~1.5s/票）退役，改读
    market.db 缓存。快路径=**进程内直读**（import paper_trading_v2 market_cache，
    0 子进程 0 网络，~5ms/票）；库内缺口/TTL 过期才走一次 `fetch-kline-cached`
    子进程自愈（补缺/全量重建，冷票一次性成本）。

    ⚠️ 今日 bar 剪除（读侧缓存纪律兜底）：盘中今日 K 是动态脏值，且 market_cache
    全量重建（无 end 界）可能在盘中把今日快照 bar 写进库——本函数**读时一律剪除
    date ≥ 今日的 bar**（cache 不动）。scan_moves 的 today=批量实时价、ten_ago
    以"最近已收盘日"为锚往回数，永不消费今日缓存 bar。

    ⚠️ 除权折算（E10，2026-09-04）：返回前经 _qfq_equivalent_closes 逐 bar 折算成
    **qfq 等效序列**——10 日窗口跨除权不再含跳空假摔（10 送 10 不再被当 -50%
    暴跌；sh600000 7/16 10派4.2 对拍：折算动量与腾讯 qfq 源零差）。多除权连乘、
    停牌票长窗口按日期区间取事件链、首事件退化/脏 factor 保守跳过（返回 None，
    scan_moves 弃该票本轮）、事件表读失败 fail-open 维持 raw。唯一消费方
    scan_moves 已适配 None；day_chg 不走此路径（pre_close=官方除权调整口径）。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    pairs: list[tuple[str | None, float]] = []
    try:
        if _PAPER_SCRIPTS not in sys.path:
            sys.path.insert(0, _PAPER_SCRIPTS)
        from paper_trading_v2 import market_cache as _mc
    except Exception:
        _mc = None
    if _mc is not None:
        try:
            bars = _mc.read_cached_kline(code, 15)
            pairs = [(b.get("date"), float(b["close"])) for b in bars if b.get("close")]
            if pairs and not _closes_need_refresh(code, [c for _, c in pairs]):
                # 暖缓存纯读命中（0 网络 0 子进程）
                return _qfq_equivalent_closes(code, pairs, today_str)
            # 冷票/缺口/TTL 过期 → 自愈（2026-09-09 由 CLI 子进程改为进程内
            # read_closes_cached，省一次解释器启动；内部锁内二次检查，并发安全）
            r = _mc.read_closes_cached(code, count=15)
            pairs = list(zip(r.get("dates") or [],
                             [float(c) for c in (r.get("closes") or [])]))
        except Exception:
            pairs = []
    if not pairs:
        # 模块不可用/自愈异常 → 回退 CLI 子进程路径
        out = ptrade2("fetch-kline-cached", code, "--count", "15", timeout=60)
        pairs = _parse_kline_pairs(out)
    try:
        return _qfq_equivalent_closes(code, pairs, today_str)
    except Exception:
        return [c for d, c in pairs if d and d < today_str]


def scan_moves() -> list[str]:
    """池内股票异动扫描（状态机 + 滞回：状态变化才输出，防反复唤醒）。

    每只股票维护 last_state（normal/sweet/chase/daymove）：
    - 状态不变 → 不输出（monitor 哈希稳定 → 睡眠）
    - 状态跃迁 → 输出一次（唤醒 agent 处理）
    - 滞回边界：进入甜点区 15% / 退出 14%；单日异动触发 7% / 复位 6.5%

    P3 数据源（2026-09-04）：today=批量实时价（_PRICE_CACHE，拍首预取）、
    day_ago=批量昨收 pre_close（_PRE_CLOSE_CACHE；腾讯官方除权调整口径，
    比取昨日 K 线 bar 更准）、ten_ago=market.db 缓存收盘（_fetch_cached_closes，
    升序取 closes[-10]；**qfq 等效折算**：跨除权 10 日窗口经 _qfq_equivalent_closes
    折算，ten_chg 无除权跳空假摔）。状态机/滞回/输出文案逐字不变（monitor 字节语义依赖）。
    """
    if not in_trade_hours():
        return []
    st = load_state()
    states = st.get("move_states", {})  # {name: last_state}
    alerts = []
    for name, code in pool_stocks():
        # today：批量实时价（拍首 fetch_prices_batch 预取；缺失 → 该票本轮跳过）
        today = fetch_price(code)
        if today is None:
            continue
        # day_ago：批量昨收（pre_close；缺失 → 该票本轮跳过）
        day_ago = fetch_pre_close(code)
        if not day_ago:
            continue
        # ten_ago：缓存收盘折算序列（qfq 等效，**升序 closes[-10]**=10 个交易日前：
        # closes[-1]=最近已收盘日=昨收；9/3 教训同源——升序数组"倒数第 N"必须用负
        # 索引，正索引 closes[9] 是窗口内第 10 根=t-6，会系统性错位；**bar 数==收盘
        # 日数**（今日 bar 已剪除，见 _fetch_cached_closes）。None=首事件退化/脏
        # factor fail-closed（_qfq_equivalent_closes）→ 本轮跳过该票，状态机不动。
        closes = _fetch_cached_closes(code)
        if not closes or len(closes) < 10:
            continue
        ten_ago = closes[-10]
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
# 消息链路事件类型全集（文档性常量，无消费处）：2026-09-09 起含 MSG_EXPIRE
# （论点失效清退令，生产者=晚审，消费者=msg-watch）
MSG_EVENT_TYPES = ("MSG_CANDIDATE", "MSG_ORDER", "MSG_REJUDGE", "MSG_EXPIRE")
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
    """检出留痕三查：taskbus 同键 MSG_CANDIDATE / MSG_ORDER / MSG_EXPIRE 状态、kv emitted。

    v12-patch/E13：同 event_key 仅剩 failed 记录 → 放行重检重发（消费失败不该
    永久封死一条消息链路——fail 多为环境性：锚价取不到/消费端崩）；pending/
    processing/done 任一存在 → 不重发（done 且槽已开在 _pool_event_keys 一层
    再挡一道，防双开）。taskbus 无任何同键记录时 kv 留痕兜底（防任务表清理后
    done 事件复发=死循环）。含 MSG_EXPIRE（2026-09-09）：同键清退令在场 →
    不再重复检出该事件。"""
    conn = sqlite3.connect(TASKS_DB)
    try:
        rows = conn.execute(
            "SELECT status FROM task_events WHERE type IN "
            "('MSG_CANDIDATE','MSG_ORDER','MSG_EXPIRE') "
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
    同 [SLEEVE] 语义）——专用心跳每拍看到未清事件持续唤醒，直到消费/重判闭环。
    含 MSG_EXPIRE（2026-09-09 清退令）：晚审挂的清退令 pending/processing 期间
    C2 monitor 持久可见，防积压死锁。"""
    if not os.path.exists(TASKS_DB):
        return []
    _ensure_task_table()
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, type, entity, priority, status FROM task_events "
            "WHERE type IN ('MSG_CANDIDATE','MSG_ORDER','MSG_REJUDGE','MSG_EXPIRE') "
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


def _segment_live_qty(code):
    """段实时持仓量（v9 起"段即账户"）：trades 汇总 buy−sell，与 storage.py 口径一致。

    v14/A：卖单 clamp 依赖它（累计卖出 ≤ 真实持仓）。取不到（无 open 段/库缺/异常）
    → None（fail-closed：调用方不得凭空假设持仓）。
    """
    if not code or not os.path.exists(POOL_DB):
        return None
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM position WHERE code=? AND status='open' "
            "ORDER BY id DESC LIMIT 1", (code,)).fetchone()
        if not row:
            return None
        rows = conn.execute("SELECT operation, quantity FROM trades "
                            "WHERE account_id=? ORDER BY seq", (row["id"],)).fetchall()
        if not rows:
            # v14 补丁（误判修复）：段存在但**零流水** = 不可判定，不等于"持仓归零"。
            # 原先返回 0 → sync_order_groups 把"新建空段 / 同 code 多段取到无流水段"判成
            # "段持仓已耗尽"，出行 group_closed 误弃有效挂单（实测：真实持仓 1000 股被判 0）。
            # 返回 None 与"无 open 段"同语义：消费点 `if live != 0: continue` 不动手。
            return None
        live = 0
        for r in rows:
            q = r["quantity"] or 0
            live += q if (r["operation"] or "") == "buy" else (
                -q if (r["operation"] or "") == "sell" else 0)
        return live
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _slot_first_member_stock(event_key):
    """槽首成员股票名（卖单出行要写 ptrade2 sell <名称>）。"""
    if not os.path.exists(POOL_DB):
        return None
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT stock FROM event_slot_members WHERE event_key=? "
                           "ORDER BY joined_at LIMIT 1", (event_key,)).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _arbitrate_sell_hits(hits):
    """v14/A 卖单同刻仲裁 + 总量裁决（方案 D4）。

    - 排序：按"价格需走过的距离"由近到远（顺序触发时会先成交的先执行）；同段串行逐张扣减；
    - 裁决：累计 ≤ 段实时持仓（trades 汇总）；超限 clamp 并留痕；qty/持仓不可判定 → fail-closed；
    - 留痕：同拍成交 ≥2 档时输出一行"同拍成交 N 档"（区分跳空一次成交与分拍成交）。
    Phase 1 边界：机械层只出行（成交仍由 CLI/agent 消费）。
    """
    if not hits:
        return []
    def _key(h):
        """排序：价格路径上先被触发的档先执行（clamp 截断时才起作用）。

        基准 = 挂单时价格 placed（价格从哪里走过来的）；缺 placed 时退化为按触发价单调
        （涨破型：低触发价先；跌破型：高触发价先）——等价于把基准设成 ±∞。
        注意：**不是**"离现价的距离"——跳空时会误把最高档排到最前（与阶梯语义相反）。
        """
        rise = h["band_max"] >= 9.9e9            # ≥X 型：触发价 = band_min
        trig = h["band_min"] if rise else h["band_max"]
        ref = h.get("placed")
        if ref is None:
            ref = -1e18 if rise else 1e18
        return (abs(trig - ref), h["event_key"])

    ordered = sorted(hits, key=_key)
    out, avail = [], {}
    exec_n = clamp_n = skip_n = 0     # v14 补丁：留痕按"兑现"计数（原先按命中数报，失真）
    for h in ordered:
        code = h["code"]
        if code not in avail:
            q = _segment_live_qty(code)
            avail[code] = q if isinstance(q, int) and q >= 0 else None
        left = avail[code]
        qty = h["qty"]
        stock = _slot_first_member_stock(h["event_key"]) or h["event_key"]
        if not isinstance(qty, int) or qty <= 0 or left is None:
            out.append(f"[PRICE-ORDER] {h['event_key']} 卖单命中 现价¥{h['px']:.2f} ∈ "
                       f"带[¥{h['band_min']:.2f},¥{h['band_max']:.2f}] 但 qty/段持仓不可判定"
                       f"（qty={qty} 段持仓={left}）→ fail-closed 出行，本轮不执行")
            skip_n += 1
            continue
        take = min(qty, left)
        avail[code] = left - take
        if take <= 0:
            out.append(f"[PRICE-ORDER] {h['event_key']} 卖单命中但段持仓已耗尽"
                       f"（qty={qty}，剩余 {left}）→ 本轮不执行；同组剩余单应失效")
            skip_n += 1
            continue
        note = "" if take == qty else f" ⚠clamp {qty}→{take}（段持仓不足/同拍先成交档）"
        if take != qty:
            clamp_n += 1
        exec_n += 1
        out.append(f"[PRICE-ORDER] {h['event_key']} 现价¥{h['px']:.2f} ∈ "
                   f"带[¥{h['band_min']:.2f},¥{h['band_max']:.2f}] → 跑 ptrade2 sell "
                   f"{stock} --qty {take} --price {h['px']:.2f} "
                   f"--event-id {h['event_key']}{note}")
    # v14 补丁（留痕口径 + 方案 A5 汇总行）：
    # ① 原「同拍成交 N 档」按**命中数**打印，被 clamp 到 0 的档也算"成交"，与对账口径冲突
    #    （实测：两档命中、只兑现一张，仍报"同拍成交 2 档…全部兑现"）→ 改为按兑现数；
    # ② 补 A5 要求的每拍汇总行（命中 N → 兑现 M / 未执行 K / clamp J），供复算对账。
    if len(ordered) > 1:
        # 汇总行只在"同拍多档"时出：单档场景每行自带注记（⚠clamp / 不可判定 / 已耗尽），
        # 保持单档输出与改动前逐字节一致（减少对消费侧 prompt 的影响面）。
        out.append(f"[PRICE-ORDER] 卖单汇总：命中 {len(ordered)} 单 → 兑现 {exec_n} / "
                   f"未执行 {skip_n} / clamp {clamp_n}（信息留痕行，无待执行命令）")
    if exec_n > 1:
        out.append(f"[PRICE-ORDER] 同拍成交 {exec_n} 档（卖单并列命中 → 全部兑现，"
                   f"按距离近→远：{', '.join(h['event_key'] for h in ordered)}）")
    return out


def sync_order_groups() -> list[str]:
    """v14/A 组内联动失效（方案 D10）：同组已有成交且该段实时持仓已耗尽 → 其余挂单应失效。

    机械层只"检测 + 出行"（状态翻转仍由 CLI 守卫：仅 pending_order 可 expire + group_closed
    要求 group_key → 重复调用被拒 = 幂等）。只作用于 batch_id ≤ 已成交单批次的挂单，
    豁免刚重挂的新批次。段仓位未耗尽不动（阶梯各档独立，不互相失效）。
    """
    if not os.path.exists(POOL_DB):
        return []
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT event_key, group_key, batch_id, status, fill_status FROM event_slots "
            "WHERE group_key IS NOT NULL AND (status='pending_order' OR fill_status='filled') "
            "ORDER BY group_key, COALESCE(batch_id,0), event_key").fetchall()
    except sqlite3.OperationalError:      # 旧库无 group_key 列（未迁移）
        rows = []
    finally:
        conn.close()
    groups = {}
    for r in rows:
        groups.setdefault(r["group_key"], []).append(r)
    out = []
    for gk, rs in sorted(groups.items()):
        filled = [r for r in rs if (r["fill_status"] or "") == "filled"]
        pending = [r for r in rs if r["status"] == "pending_order"]
        if not filled or not pending:
            continue
        code = (_slot_member_code(pending[0]["event_key"])
                or _slot_member_code(filled[0]["event_key"]))
        live = _segment_live_qty(code) if code else None
        if live != 0:                     # 未耗尽（None=取不到 → 不动，绝不禁忌误弃）
            continue
        top = max((r["batch_id"] or 0) for r in filled)
        for r in pending:
            if (r["batch_id"] or 0) > top:       # 豁免刚重挂的新批次
                continue
            out.append(f"[PRICE-ORDER] {r['event_key']} 组[{gk}] 段持仓已耗尽"
                       f"（同组已有成交 + 持仓 0）→ 跑 ptrade2 sleeve-order-expire "
                       f"{r['event_key']} --reason group_closed")
    return out


# ======================================================================
# Phase 2：系统兜底单（成本保护 / ATR 移动止损 → 挂单机制承载）
# 契约与 paper_trading_v2/exec_layer.py 同源（读同一个 exec_layer.json）：
#   off=不生成/不执行；shadow=生成+只留痕（影子期铁律：零 ptrade2 调用）；
#   orders=生成；仅白名单标的同拍直调执行，名单外降级 shadow。
# ======================================================================

def _protect_mode(stock_name: str | None = None) -> str:
    """该标的的兜底单口径（off/shadow/orders）——缺文件=off（零影响）。

    与 ``paper_trading_v2.exec_layer.protect_mode`` 同契约；本脚本不 import 该包
    （task-bus 与 paper-trading 是两个包，只共享这一个 JSON 配置）。
    """
    cfg: dict = {}
    try:
        p = os.environ.get("PTRADE2_EXEC_LAYER_FILE") or os.path.join(WS, "exec_layer.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        c = (d.get("protect_orders") if isinstance(d, dict) else None)
        cfg = c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        cfg = {}
    env = (os.environ.get("PTRADE2_PROTECT_ORDERS") or "").strip().lower()
    mode = env or str(cfg.get("mode") or "off").strip().lower()
    if mode not in ("off", "shadow", "orders"):
        mode = "off"
    if mode != "orders" or not stock_name:
        return mode
    wl = cfg.get("exec_stocks")
    wl = wl if isinstance(wl, (list, tuple)) else []
    names = {str(x).strip() for x in wl if str(x).strip()}
    return "orders" if stock_name in names else "shadow"


def _stock_name_by_code(code: str) -> str | None:
    """code → 持仓段名（open 段优先；取不到 None → 执行侧 fail-closed）。"""
    if not code or not os.path.exists(POOL_DB):
        return None
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT stock FROM position WHERE code=? AND status='open' "
            "ORDER BY id DESC LIMIT 1", (code,)).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _protect_trace(kind: str, key: str, payload: dict) -> None:
    """影子留痕（pool DB shadow_log）——只记不执行；任何失败静默（不得影响扫描）。"""
    try:
        conn = sqlite3.connect(POOL_DB, timeout=5)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO shadow_log (kind,key,payload,created_at) VALUES (?,?,?,?)",
                    (kind, key, json.dumps(payload, ensure_ascii=False, default=str),
                     datetime.now().isoformat(timespec="seconds")))
        finally:
            conn.close()
    except Exception:
        pass


def _protect_hit_lines(event_key: str, code: str, px: float, slot_row) -> list[str]:
    """兜底单命中（现价 ≤ 保护线）处置：影子期只留痕；执行期同拍直调。

    - ``mode != orders``（off/shadow，或 orders 但该票不在白名单）→ **零 ptrade2 调用**，
      只出行留痕 + shadow_log(kind='protect_hit')——影子期绝不产生任何真实卖出；
    - ``mode = orders`` → 同拍直调 `ptrade2 sell <名> --qty N --price P --event-id <槽键>`
      （与保护链 WP1 同款直调语义），成交/失败原因码一律 shadow_log(kind='protect_exec')。
    - 标的名/qty 不可判定 → 执行被拒（fail-closed，宁可不卖不卖错）。
    """
    name = _stock_name_by_code(code)
    qty = slot_row["qty"] if "qty" in slot_row.keys() else None
    # 跌破卖几何：保护线 = 带上沿（band=[0, 线]）
    line = float(slot_row["band_max"])
    mode = _protect_mode(name)
    head = (f"[PROTECT-ORDER] {event_key} {name or '?'}({code}) 兜底单命中："
            f"现价¥{px:.2f} ≤ 保护线¥{line:.2f}")
    _protect_trace("protect_hit", event_key,
                   {"stock": name, "code": code, "px": px, "line": line,
                    "qty": qty, "mode": mode,
                    "batch_id": slot_row["batch_id"] if "batch_id" in slot_row.keys() else None})
    if mode != "orders":
        return [head + f" → 影子期（mode={mode}）只记不执行（不调 ptrade2），"
                       f"等 atr-sync 重算/人工核验"]
    if not name or qty is None or int(qty) <= 0:
        return [head + f" → 执行被拒（标的名/qty 不可判定：name={name!r} qty={qty!r}，"
                       f"fail-closed 不卖）"]
    out = ptrade2("sell", name, "--qty", str(int(qty)), "--price", f"{px:.2f}",
                  "--event-id", event_key, timeout=90)
    ok = bool(out) and "✅" in out
    _protect_trace("protect_exec", event_key,
                   {"stock": name, "code": code, "px": px, "line": line, "qty": int(qty),
                    "ok": ok, "out_tail": (out or "")[-200:]})
    if ok:
        return [head + f" → 同拍直调 ptrade2 sell {name} --qty {int(qty)} "
                       f"--price {px:.2f} --event-id {event_key}（成交）"]
    return [head + f" → 同拍直调被拒（{_parse_exec_fail_code(out)}）：{(out or '')[-120:]}"]


def check_price_orders() -> list[str]:
    """E1 挂单槽价格扫描（--scope price）：pending_order 槽四态检测（C3/WP7，2026-09-10）。

    v14/A（执行层 Phase 1）：**几何=时机轴、side=动作轴**。
    - side='buy'（缺省）：下面 v3 §2 的四态**原样保留**（旧槽行为逐字节不变）；
    - side='sell'（卖出挂单，单边带 + qty）：三出口——进带 → `ptrade2 sell --qty` 出行
      （并入同刻仲裁 + 总量裁决）；TTL → expired 出行；取价失败 → 跳过。**卖单永不弃单**
      （单边带另一端是哨兵极值，"整带穿越"不可能发生；下限保护是"反向走远"，由短 TTL 承担）。

    band 语义按 v3 §2 等待型/立即型改造（band 不是限价，是"可执行价格窗口"）：
    - TTL → 过期行（--reason expired，不变，消费方执行）；
    - 等待型（placed_px ∉ band）：仍在创建价同侧 → 等（TTL 到期 expired）；
      **跨到另一侧**（整带穿越未成交）→ 同拍直调 `sleeve-order-expire
      --reason band_skipped`（发起方核价：调用前自查现价确在另一侧，CLI 层不再核价——
      A 批 expire 白名单已含两码）；
    - 立即型（placed_px ∈ band）按生产现状语义（2026-09-10 用户裁决回退：带本身是
      容忍度缓冲，边界抖动不值得建状态，band_left/band_out_count 计数机制已撤销，
      列保留不用）：
      · 价 ∈ [band_min,band_max] → 触带行（sleeve-order-fill --price 检测价，不变）；
      · 价 < band_min → 同拍直调 `--reason band_break`（生产现状，原样保留）；
      · 价 > band_max → **无动作不输出**（挂单等回落，TTL 到期自然走 expired）；
    - 取价失败 → 明确失败行（不静默，下一拍重试）。
    弃单直调（打桩可断言），成交 fill 仍只出行（E3/E9 防线在 CLI，agent 消费）。
    """
    if not os.path.exists(POOL_DB) or not in_price_scan_window():
        return []
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            slots = conn.execute(
                "SELECT event_key, band_min, band_max, order_ttl, placed_px, "
                "side, qty, group_key, batch_id "
                "FROM event_slots WHERE status='pending_order' ORDER BY event_key").fetchall()
        except sqlite3.OperationalError:  # 旧库缺 v14 列 / placed_px 列
            slots = conn.execute(
                "SELECT event_key, band_min, band_max, order_ttl, "
                "anchor_price AS placed_px, "
                "NULL AS side, NULL AS qty, NULL AS group_key, NULL AS batch_id "
                "FROM event_slots WHERE status='pending_order' ORDER BY event_key").fetchall()
    finally:
        conn.close()
    now = datetime.now()
    out = []
    sell_hits = []          # v14/A：卖单命中候选（收集后统一仲裁 + 总量裁决）
    for s in slots:
        event_key = s["event_key"]
        side = (s["side"] or "buy") if "side" in s.keys() else "buy"
        is_sell = side == "sell"
        if s["band_min"] is None or s["band_max"] is None:
            out.append(f"[PRICE-ORDER] {event_key} 挂单带缺失（band_min/max=NULL，异常态）"
                       f"→ fail-closed 本轮跳过（不成交不弃单）")
            continue
        ttl_dt = _parse_order_ttl(s["order_ttl"])
        if ttl_dt is not None and now > ttl_dt:
            out.append(f"[PRICE-ORDER] {event_key} 挂单已过期 ttl={s['order_ttl']} "
                       f"→ 跑 ptrade2 sleeve-order-expire {event_key} --reason expired")
            continue
        is_protect = event_key.startswith("protect:")
        # 系统兜底单自带槽（无成员段）→ code 从槽键取（'protect:<code>'）；
        # 普通挂单槽仍取首成员 code（_slot_member_code）。
        code = event_key.split(":", 1)[1] if is_protect else _slot_member_code(event_key)
        px = fetch_price_any(code) if code else None
        if px is None:
            out.append(f"[PRICE-ORDER] {event_key} 取价失败（code={code or '无成员段'}）"
                       f"→ 本轮跳过，不成交不弃单（fail-closed，下一拍重试）")
            continue
        placed = s["placed_px"]
        if s["band_min"] <= px <= s["band_max"]:
            if is_sell:
                if is_protect:
                    # Phase 2 系统兜底单命中：影子期只留痕；执行期（逐票白名单）
                    # 同拍直调。不走 _arbitrate_sell_hits——那是消息槽卖单的
                    # 段持仓裁决口径，系统兜底单按自身 qty + CLI 持仓校验。
                    out.extend(_protect_hit_lines(event_key, code or "", px, s))
                    continue
                # 卖单进带 → 收集候选（统一种排序 + 持仓裁决后出行，见 _arbitrate_sell_hits）
                sell_hits.append({"event_key": event_key, "px": px, "code": code,
                                  "qty": s["qty"] if "qty" in s.keys() else None,
                                  "band_min": s["band_min"], "band_max": s["band_max"],
                                  "placed": placed})
                continue
            out.append(f"[PRICE-ORDER] {event_key} 现价¥{px:.2f} ∈ 带"
                       f"[¥{s['band_min']:.2f},¥{s['band_max']:.2f}] → 跑 ptrade2 "
                       f"sleeve-order-fill {event_key} --price {px:.2f}")
            continue
        if is_sell:
            # v14/A 三出口：进带→成交（上面已收集）、TTL→返回（上面）、取价失败→跳过（上面）。
            # 单边带另一端是哨兵极值，"整带穿越"不可能发生 → 卖单只等，**永不弃单**。
            continue
        if placed is not None and not (s["band_min"] <= placed <= s["band_max"]):
            # ---- 等待型（创建价 ∉ band）：跨带判定（生产现状不动：下穿即弃单，
            # 上穿无动作——等待型跨带只对"创建价在下、现价到上"的情形成立）----
            placed_below = placed < s["band_min"]
            px_below = px < s["band_min"]
            if (placed_below and not px_below) or (not placed_below and px_below):
                # 跨到另一侧（发起方核价：px 确在另一侧）→ 同拍直调 band_skipped
                if _expire_order_direct(event_key, "band_skipped"):
                    out.append(f"[PRICE-ORDER] {event_key} 等待型挂单整带穿越未成交"
                               f"（创建价¥{placed:.2f} 现价¥{px:.2f} 跨带）→ 同拍直调 "
                               f"sleeve-order-expire {event_key} --reason band_skipped")
                else:
                    out.append(f"[PRICE-ORDER] {event_key} band_skipped 弃单被拒"
                               f"（CLI fail-closed，下一拍重试）")
            # 同侧 → 继续等（无输出不唤醒，TTL 到期自然 expired）
            continue
        # ---- 立即型（创建价 ∈ band）：生产现状语义 ----
        if px < s["band_min"]:
            # 下穿 → 同拍直调 band_break（生产现状原样，发起方已核价 px<band_min）
            if _expire_order_direct(event_key, "band_break"):
                out.append(f"[PRICE-ORDER] {event_key} 现价¥{px:.2f} < "
                           f"band_min¥{s['band_min']:.2f} → 同拍直调 sleeve-order-expire "
                           f"{event_key} --reason band_break")
            else:
                out.append(f"[PRICE-ORDER] {event_key} band_break 弃单被拒"
                           f"（CLI fail-closed，下一拍重试）")
        # px > band_max：无动作不输出（挂单等回落，TTL 到期自然走 expired——生产现状）
    out.extend(_arbitrate_sell_hits(sell_hits))     # v14/A：卖单同刻仲裁 + 总量裁决
    return out


def _expire_order_direct(event_key: str, reason: str) -> bool:
    """C3：同拍直调 sleeve-order-expire（band_break/band_skipped，A 批 expire 白名单已含，
    CLI 层不再核价——发起方=本函数已完成下穿/跨带判定）。成功 → True。"""
    out = ptrade2("sleeve-order-expire", event_key, "--reason", reason, timeout=90)
    return bool(out) and ("✅" in out or "已弃单" in out or "已过期" in out)


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


def _collect_price_scope_codes() -> set[str]:
    """price scope 拍首批量预取的 code 收集（P3）：E1/E6/E7/E8/E10/E11 全部需价码。

    - E1 挂单槽：pending_order 槽首成员 code（_slot_member_code）；
    - E6 保护链：conditions JOIN position 的去重 code 集合；
    - E7 watchpoint：watch_points 的 code（缺码实体兜底 pool_stocks 映射）；
    - E8 裸奔错位检测：有净持仓 open 段中非 NEWS 段的 code；
    - E10 动量：pool_stocks 的 code；
    - E11 大盘：MARKET_INDICES 四大指数（腾讯批量支持指数，2026-09-04 实测）。
    只读 DB（master_pool 生产库只读约束），一次连接批量取完。
    """
    codes: set[str] = set()
    codes.update(MARKET_INDICES)                                   # E11
    # E7：watch_points 的 code 集合（kv_store，含裸 6 位/点后缀——批量前归一化；
    # 不在池的实体也收集，避免 E7 逐票兜底 subprocess）
    if os.path.exists(TASKS_DB):
        conn = sqlite3.connect(TASKS_DB)
        try:
            row = conn.execute("SELECT value FROM kv_store WHERE key='watch_points'").fetchone()
            if row:
                pts = json.loads(row[0])
                if isinstance(pts, dict):
                    for plist in pts.values():
                        if isinstance(plist, list):
                            for p in plist:
                                if isinstance(p, dict) and p.get("code"):
                                    codes.add(str(p["code"]))
        except (sqlite3.Error, json.JSONDecodeError, TypeError):
            pass  # kv 不可读 → E7 走 pool 映射兜底，不阻塞收集
        finally:
            conn.close()
    if not os.path.exists(POOL_DB):
        return codes
    conn = sqlite3.connect(f"file:{POOL_DB}?mode=ro", uri=True)
    try:
        # E1：挂单槽首成员（带 SQL 兜底，取不到的槽在检测内报"无成员段"）
        try:
            for r in conn.execute(
                    "SELECT event_key FROM event_slots WHERE status='pending_order'").fetchall():
                c = _slot_member_code(r[0])
                if c:
                    codes.add(c)
        except sqlite3.OperationalError:
            pass
        # E6：conditions JOIN position（与 check_price_triggers 同 SQL，取 code 列）
        for r in conn.execute(
                "SELECT DISTINCT a.code FROM conditions cn JOIN position a ON cn.account_id=a.id "
                "WHERE cn.status='active' AND cn.price IS NOT NULL AND a.code IS NOT NULL"):
            codes.add(r[0])
        # E7 watchpoint：code 缺失的实体由 pool_stocks 兜底映射（与 E10 合并拉取）
        for r in conn.execute(
                "SELECT p.stock, COALESCE(p.code, "
                "(SELECT code FROM position WHERE stock=p.stock AND code IS NOT NULL LIMIT 1)) "
                "FROM pool p WHERE p.pool_status='active' AND COALESCE(p.strategy,'') != 'NEWS'"):
            if r[1]:
                codes.add(r[1])
        # E8：裸奔错位检测取价对象 = 非 NEWS 的实际持仓段（NEWS 段保护线由
        # sleeve-fill 挂载，错位检测不消费——与 check_naked_conditions 行内查询同口径）
        for r in conn.execute(
                "SELECT DISTINCT p.code FROM position p WHERE p.status='open' AND "
                "COALESCE(p.strategy,'')!='NEWS' AND p.code IS NOT NULL AND EXISTS ("
                "  SELECT 1 FROM trades t WHERE t.account_id=p.id GROUP BY t.account_id"
                "  HAVING SUM(CASE WHEN t.operation='buy' THEN t.quantity"
                "                  ELSE -t.quantity END) > 0)"):
            codes.add(r[0])
    finally:
        conn.close()
    return {c for c in (x.strip() for x in codes) if c}


def run_price_scope() -> int:
    """price scope 主流程（v12 C1 price-watch 心跳 monitor）：E1 挂单槽四态扫描
    + E6 保护链扫描（承接 legacy check_price_triggers 全账户扫，含 strategy='NEWS'
    成员段——成交后保护链归属的延续监督，触发写 WATCH_ALERT 供 C1 消费执行）。
    静默 news 检出/任务列举/atr/异动/大盘等——专用心跳只看价格输出，防串唤醒。

    P3（2026-09-04）性能改造：拍首 _collect_price_scope_codes 收集全部需价码 →
    **一次 fetch_prices_batch 批量实时价**（腾讯原生批量，50 码 ~1.0s）填充
    _PRICE_CACHE/_PRE_CLOSE_CACHE/_QUOTE → E6/E7/E8 的 fetch_price 变纯字典读、
    E10 消费批量价+昨收+market.db 日K缓存、E11 读批量指数涨跌幅。常规拍子进程
    从 ~100 个降到 1 个（+冷票时每票 1 次 fetch-kline-cached 补抓）。
    预取失败（返回 {}）→ 各检测自动退回旧的逐票 fetch_price 路径，不崩。
    """
    fetch_prices_batch(list(_collect_price_scope_codes()))  # 拍首唯一实时价请求（填充同拍缓存）
    lines = []
    lines.extend(check_price_orders())      # E1：挂单槽触带/破带/过期/取价失败
    lines.extend(check_orphan_slots())      # E1b：孤儿槽（开槽未挂单，v12 断链兜底）
    lines.extend(sync_order_groups())       # v14/A：组内联动失效（同组已成交 + 段仓位耗尽）
    lines.extend(check_price_triggers())    # E6：保护链（全账户含 NEWS 段）
    lines.extend(check_watch_points())      # E7：技术组 watchpoint buy/eval/sell 触发（2026-09-04
                                            #   C1 吸收 legacy——C1=唯一股价盯盘心跳，脚本前置触发
                                            #   →WATCH_ALERT 事件供 C1 消费核验/复检/卖出）
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


def check_watch_points() -> list[str]:
    """价格点检测（C5/WP5，2026-09-10 改读 watch_points 表）：命中 → 消费 + WATCH_ALERT。

    数据源：watch_points **表**（B 批迁移真源，wp_id/created_by/status 列）；表不存在
    （旧库）→ 回退 kv_store('watch_points') 旧路径（行为不劣化）。
    - mode=eval（技术组 L2 待命复检点）→ WATCH_ALERT(mode=eval) 唤醒分析 agent 复检
    - mode=buy（技术组 L2 建仓点）→ WATCH_ALERT(mode=buy, budget=金额) 唤醒核验 → allocate → buy
    - mode=sell（卖出点）→ WATCH_ALERT(mode=sell, direction=sell) 唤醒 C1 执行卖仓/减仓

    触发方向按 mode 分流：
    - buy/eval：单值 现价 ≤ price；配 min 则区间 现价 ∈ [min, price]（price=上沿）。
    - sell：单值 现价 ≥ price；配 min 则区间 现价 ∈ [price, min]（price=下沿、min=上沿）。

    新增失效判据（§2 B6）与消费语义（A3）：
    - buy 区间价**跌穿 min** / sell 区间价**冲过 min（上沿）** → `expired:range_break`
      （不触发消费，行状态落库，tech-watch 决定重挂/放弃）；
    - 触发时 `|现价−挂点价|/挂点价 > 5%`（跳空脱靶）→ **不执行**，`price_drifted`；
    - 正常触发消费：写 `consumed_at` + `trigger_event_id` + `status='consumed'`
      （**不得删除行**——A3：只打标记回滚旧代码会再触发一次，状态列防复发）；
    - 事件 payload 带 `creator`（表行 created_by；空 → payload.creator=''，fail-closed 由消费方）。
    kv 兼容期同步维护（add 双写，消费/失效同步 pop，防旧 kv 读方复发）。
    """
    if not in_trade_hours() or not os.path.exists(TASKS_DB):
        return []
    pool = {name: code for name, code in pool_stocks()}
    alerts = []
    rows = _wp_table_rows()                                  # 表优先（B 批真源）
    if rows is not None:
        return _consume_watch_points(rows, pool, alerts)
    return _consume_watch_points_kv(pool, alerts)


def _wp_table_rows() -> list[dict] | None:
    """C5：读 watch_points 表 active 行（wp_id/created_by/status）；表不存在 → None（回退 kv）。"""
    if not os.path.exists(TASKS_DB):
        return None
    conn = sqlite3.connect(TASKS_DB)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watch_points'").fetchone()
        if not exists:
            return None
        return [dict(r) for r in conn.execute(
            "SELECT * FROM watch_points WHERE status='active' ORDER BY entity, added_at, wp_id")]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _consume_watch_points(rows: list[dict], pool: dict, alerts: list[str]) -> list[str]:
    """C5 表路径消费：命中 → 写事件（creator/ref/snapshot）+ 行置 consumed（不删行）。

    失效判据：range_break（buy 跌穿 min / sell 冲过 min）与 price_drifted（>5%）→
    行状态落库（expired:range_break / price_drifted），不消费不写事件。
    """
    changed_kv = False
    kv_points = _kv_get("watch_points") or {}
    for row in rows:
        entity, code = row["entity"], row.get("code")
        code = code or pool.get(entity)
        if not code:
            continue  # 无 code（未入池且未传 --code），跳过
        price = fetch_price(code)
        if price is None:
            continue
        wp_id = row["wp_id"]
        mode = row.get("mode") or "eval"
        note = row.get("note") or ""
        pt_price, min_price = row["price"], row.get("min")
        # ---- 失效判据（先于命中）----
        if mode == "buy" and min_price is not None and price < min_price:
            _wp_set_status(wp_id, "expired:range_break")
            alerts.append(f"⚠️ {entity}({code}) buy 挂点区间失效: 现价¥{price} 跌穿 min¥{min_price} "
                          f"→ expired:range_break（{wp_id}）")
            _kv_pop_point(kv_points, entity, pt_price, mode)
            changed_kv = True
            continue
        if mode == "sell" and min_price is not None and price > min_price:
            _wp_set_status(wp_id, "expired:range_break")
            alerts.append(f"⚠️ {entity}({code}) sell 挂点区间失效: 现价¥{price} 冲过上沿¥{min_price} "
                          f"→ expired:range_break（{wp_id}）")
            _kv_pop_point(kv_points, entity, pt_price, mode)
            changed_kv = True
            continue
        # ---- 命中判定 ----
        if mode == "sell":
            hit = pt_price <= price <= min_price if min_price is not None else price >= pt_price
        else:
            hit = min_price <= price <= pt_price if min_price is not None else price <= pt_price
        if not hit:
            continue
        # ---- 跳空脱靶判据：|现价−挂点价|/挂点价 > 5% → 不执行 ----
        if pt_price and abs(price - pt_price) / pt_price > 0.05:
            _wp_set_status(wp_id, "price_drifted")
            alerts.append(f"⚠️ {entity}({code}) 挂点触发但跳空脱靶: 现价¥{price} 偏离挂点¥{pt_price} "
                          f"> 5% → price_drifted（不执行，{wp_id}）")
            _kv_pop_point(kv_points, entity, pt_price, mode)
            changed_kv = True
            continue
        # ---- 触发消费：写事件 + 行置 consumed（不删行）----
        cond_name = {"sell": f"卖出点-{note}" if note else "卖出点",
                     "buy": f"建仓点-{note}" if note else "建仓点",
                     "eval": f"L2复检-{note}" if note else "L2复检"}.get(mode, mode)
        ok = _write_alert(entity, code, mode, 0, cond_name, pt_price, price,
                          mode=mode, budget=row.get("amount"),
                          ref_id=wp_id, creator=row.get("created_by") or "")
        if not ok:
            continue  # 去重命中（同事件已在场），不改行
        _wp_consume(wp_id)
        if mode == "sell":
            range_txt = (f"（带内 ¥{pt_price}~{min_price}）" if min_price is not None else "")
            alerts.append(f"💰 {entity}({code}) 卖出点触发: 现价¥{price} ≥ ¥{pt_price} "
                          f"{range_txt}[{note}] → 唤醒 C1 执行卖仓/减仓（限价卖）")
        elif mode == "buy":
            budget = row.get("amount")
            budget_txt = f" 预算¥{budget:,.0f}" if budget else "（⚠️无预算）"
            alerts.append(f"🛒 {entity}({code}) L2建仓点触发: 现价¥{price} ≤ ¥{pt_price} "
                          f"[{note}]{budget_txt} → 唤醒核验建仓")
        else:
            alerts.append(f"📌 {entity}({code}) L2待命复检点触发: 现价¥{price} ≤ ¥{pt_price} "
                          f"[{note}] → 唤醒复检（升 L1/挂 conditions/移除）")
        _kv_pop_point(kv_points, entity, pt_price, mode)
        changed_kv = True
    if changed_kv:
        _kv_set("watch_points", kv_points)
    return alerts


def _consume_watch_points_kv(pool: dict, alerts: list[str]) -> list[str]:
    """C5 kv 旧路径（表不存在时回退）：原逻辑原样（触发即 kv pop 硬删）。"""
    points = _kv_get("watch_points")
    if not points:
        return alerts
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
            note = p.get("note", "")
            mode = p.get("mode", "eval")
            if mode == "sell":
                min_price = p.get("min")
                if min_price is not None:
                    hit = p["price"] <= price <= min_price
                else:
                    hit = price >= p["price"]
                if hit:
                    cond_name = f"卖出点-{note}" if note else "卖出点"
                    if _write_alert(entity, code, "sell", 0, cond_name, p["price"], price,
                                    mode="sell"):
                        range_txt = (f"（带内 ¥{p['price']}~{min_price}）"
                                     if min_price is not None else "")
                        alerts.append(f"💰 {entity}({code}) 卖出点触发: 现价¥{price} ≥ ¥{p['price']} "
                                      f"{range_txt}[{note}] → 唤醒 C1 执行卖仓/减仓（限价卖）")
                        points.pop(entity, None)  # 触发即失效（同 buy/eval）
                        changed = True
                        break  # 触发即处理完该实体（同 buy/eval 单点语义）
                continue
            min_price = p.get("min")
            if min_price is not None:
                hit = min_price <= price <= p["price"]
            else:
                hit = price <= p["price"]
            if hit:
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


WP_TABLE_DDL = """CREATE TABLE IF NOT EXISTS watch_points (
    wp_id            TEXT PRIMARY KEY,
    entity           TEXT NOT NULL,
    code             TEXT,
    price            REAL NOT NULL,
    min              REAL,
    mode             TEXT NOT NULL DEFAULT 'eval',
    amount           REAL,
    note             TEXT DEFAULT '',
    created_by       TEXT DEFAULT '',
    added_at         TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    consumed_at      TEXT,
    trigger_event_id INTEGER
)"""


def _wp_set_status(wp_id: str, status: str) -> None:
    """C5：行状态落库（expired:range_break / price_drifted），不删行。"""
    if not os.path.exists(TASKS_DB):
        return
    conn = sqlite3.connect(TASKS_DB)
    try:
        conn.execute(WP_TABLE_DDL)  # 隔离/旧库兜底建表
        conn.execute("UPDATE watch_points SET status=? WHERE wp_id=?", (status, wp_id))
        conn.commit()
    except sqlite3.Error:
        pass  # 表不可写（旧库）→ 状态不落，消费继续
    finally:
        conn.close()


def _wp_consume(wp_id: str) -> None:
    """C5：触发消费 → consumed_at + trigger_event_id + status='consumed'（行不删）。

    trigger_event_id=本实体最新 WATCH_ALERT 事件 id。"""
    if not os.path.exists(TASKS_DB):
        return
    conn = sqlite3.connect(TASKS_DB)
    try:
        conn.execute(WP_TABLE_DDL)
        row = conn.execute(
            "SELECT id FROM task_events WHERE type='WATCH_ALERT' AND status IN ('pending','done') "
            "ORDER BY id DESC LIMIT 1").fetchone()
        ev_id = row[0] if row else None
        conn.execute(
            "UPDATE watch_points SET status='consumed', consumed_at=datetime('now','localtime'), "
            "trigger_event_id=? WHERE wp_id=?", (ev_id, wp_id))
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def _kv_pop_point(kv_points: dict, entity: str, pt_price: float, mode: str) -> bool:
    """C5：kv 兼容期同步 pop（同实体同价同模式的点移除；整实体空了才删键）。"""
    if entity not in kv_points:
        return False
    before = len(kv_points[entity])
    kv_points[entity] = [p for p in kv_points[entity]
                         if not (float(p.get("price") or 0) == float(pt_price)
                                 and (p.get("mode") or "eval") == mode)]
    if not kv_points[entity]:
        kv_points.pop(entity, None)
    return len(kv_points.get(entity, [])) < before or entity not in kv_points


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


# ======================================================================
# 整拍互斥（Phase 0 / 执行层方案 2026-09-10）：同一 scope 只允许一份扫描在跑
# ======================================================================
_SCAN_LOCK_FH = None


def acquire_scan_lock(scope: str, lock_dir: str | None = None):
    """尝试取该 scope 的整拍锁（非阻塞 flock）。

    取到 → 返回句柄（须存活到进程结束；进程死亡含 SIGKILL 由内核自动释放，
    不存在"忘记释放"的锁泄露）；取不到 → 返回 None，调用方打印字节稳定的
    ``IDLE`` 并退出 0（monitor 模式：输出不变 = 不唤醒 agent）。

    作用域按 scope 分开：price 与 news 各自一把锁，互不阻塞。
    """
    global _SCAN_LOCK_FH
    if os.environ.get("WATCH_SCAN_NO_LOCK") == "1":     # 逃生阀（排障/特殊测试）
        return True
    d = (lock_dir or os.environ.get("WATCH_SCAN_LOCK_DIR")
         or os.path.join(os.path.expanduser("~"), ".hermes", "run"))
    os.makedirs(d, exist_ok=True)
    fh = open(os.path.join(d, f"watch_scan.{scope}.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    _SCAN_LOCK_FH = fh
    return fh


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
    # Phase 0 整拍互斥：取不到锁 = 已有一份同 scope 扫描在跑 → 本拍什么都不做。
    # 输出必须是字节稳定的 "IDLE"，否则 monitor 会把跳过误判成变化而白唤醒 agent。
    if acquire_scan_lock(scope) is None:
        print("IDLE")
        return 0
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
