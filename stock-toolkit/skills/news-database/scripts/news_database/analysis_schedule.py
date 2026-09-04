"""analysis_schedule：批量分析 TTL 滚动状态表（analysis-ttl 方案 §三）。

与 task_schedule 的分工：task_schedule 是任务级节奏（T1-T4 仅几行）；本模块是
**池内每股一行**的批量分析状态真源——替代 stock-batch-analysis 每日全量扫 50 只。

TTL 语义：最近一次完整分析距今超过 TTL_TRADING_DAYS 个交易日 → 到期入待分析队列。
交易日判定走工作区单一真源 trading_calendar（data/trading_calendar.json），
模块缺位/数据缺失时回退周末规则（永不抛异常）。

外部强制刷新：事件侧（task_events ANALYSIS_REFRESH，consumer=analysis-watch）或
晨审/手工直接调 request_refresh 打标 refresh_required=1（插队优先）。
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# 表结构（analysis-ttl 方案 §三）——池内每股一行
# ---------------------------------------------------------------------------

ANALYSIS_SCHEDULE_DDL = """
CREATE TABLE IF NOT EXISTS analysis_schedule (
    stock            TEXT PRIMARY KEY,   -- 池内股票名
    last_analyzed_at TEXT,               -- 最近完整分析时间(ISO)。NULL=从未分析→最优先
    refresh_required INTEGER NOT NULL DEFAULT 0,  -- 1=事件强制刷新待处理(插队优先)
    refresh_reason   TEXT,               -- 触发原因: ttl/news_signal/price_move/external
    refresh_source   TEXT,               -- 触发源引用(如 ND#xxx 事件号 / daymove / CALENDAR#)
    refreshed_at     TEXT,               -- 最近打标强制刷新时间(排序用旧→新)
    last_result      TEXT                -- 最近分析结论摘要(供晨审读)
);
"""

# TTL 默认 3 个交易日（方案 §三：批量分析滚动周期，交易日真源=trading_calendar）
TTL_TRADING_DAYS = 3

# 待分析队列默认单轮条数（analysis-watch 心跳单轮消费上限，防响应截断）
DEFAULT_DUE_LIMIT = 5

# 交易日历单一真源（同 watch_scan.py 接法：workspace 根插 sys.path 后 import）
STOCK_WS_ROOT = "/home/catmouse/Github_Project/daily-stock-workspace"
if STOCK_WS_ROOT not in sys.path:
    sys.path.insert(0, STOCK_WS_ROOT)


def _now_str() -> str:
    """本地时间戳，格式与 newsdb 其余表一致（datetime('now','localtime')）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def ensure_table(conn: sqlite3.Connection) -> None:
    """幂等建表（IF NOT EXISTS），不触碰 scan_log/task_schedule/其他表。可重复调用。"""
    conn.execute(ANALYSIS_SCHEDULE_DDL)
    conn.commit()


def upsert_stock(conn: sqlite3.Connection, stock: str) -> None:
    """池内股票入表（幂等 upsert）。

    已有行：last_analyzed_at/refresh 状态/last_result 全保留（只补缺行，不动历史）。
    新行：last_analyzed_at=NULL（从未分析 → due_stocks 最优先）。
    """
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO analysis_schedule (stock) VALUES (?)
        ON CONFLICT(stock) DO NOTHING
        """,
        (stock,),
    )
    conn.commit()


def seed_pool(conn: sqlite3.Connection, stocks) -> int:
    """全池股票批量入表（迁移用）：缺行 INSERT，已有行不动。返回新入表行数。"""
    ensure_table(conn)
    before = conn.execute("SELECT COUNT(*) FROM analysis_schedule").fetchone()[0]
    conn.executemany(
        "INSERT INTO analysis_schedule (stock) VALUES (?) ON CONFLICT(stock) DO NOTHING",
        [(s,) for s in stocks],
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM analysis_schedule").fetchone()[0]
    return after - before


def mark_analyzed(conn: sqlite3.Connection, stock: str, result: str = "",
                  now: str | None = None) -> None:
    """分析完成：last_analyzed_at=now、refresh_required=0、refresh_reason='ttl'、last_result。

    refresh_reason 置 'ttl' 语义=下次到期仍按 TTL 周期滚动（非事件挂起态）。
    行不存在 → 自动建行（兜底，正常应先 seed_pool）。
    """
    ensure_table(conn)
    if now is None:
        now = _now_str()
    conn.execute(
        """
        INSERT INTO analysis_schedule (stock, last_analyzed_at, refresh_required,
                                       refresh_reason, last_result)
        VALUES (?, ?, 0, 'ttl', ?)
        ON CONFLICT(stock) DO UPDATE SET
            last_analyzed_at=excluded.last_analyzed_at,
            refresh_required=0,
            refresh_reason='ttl',
            last_result=excluded.last_result
        """,
        (stock, now, result),
    )
    conn.commit()


def request_refresh(conn: sqlite3.Connection, stock: str, reason: str,
                    source: str, now: str | None = None) -> None:
    """外部强制刷新：refresh_required=1 + reason/source（幂等 upsert，插队优先）。

    行不存在 → 自动建行。重复打标：刷新 refreshed_at 打标时间（组内排序
    "打标时间旧→新"，同股重复请求以最新打标为准）。
    """
    ensure_table(conn)
    if now is None:
        now = _now_str()
    conn.execute(
        """
        INSERT INTO analysis_schedule (stock, refresh_required, refresh_reason,
                                       refresh_source, refreshed_at)
        VALUES (?, 1, ?, ?, ?)
        ON CONFLICT(stock) DO UPDATE SET
            refresh_required=1,
            refresh_reason=excluded.refresh_reason,
            refresh_source=excluded.refresh_source,
            refreshed_at=excluded.refreshed_at
        """,
        (stock, reason, source, now),
    )
    conn.commit()


def ttl_boundary(now: datetime | str, ttl_days: int = TTL_TRADING_DAYS) -> str:
    """TTL 边界：从 now 往回数 ttl_days 个交易日，返回 'YYYY-MM-DD'。

    交易日判定走工作区 trading_calendar（is_trading_day，2026-09-04 周五 →
    边界 2026-09-01 周二：9/7 周一距 9/4 仅 1 交易日，未到期）。
    trading_calendar 数据/模块缺位时回退自然日近似（周末短路）。
    """
    if isinstance(now, str):
        now = _parse(now)
    day = now.date()
    count = 0
    while count < ttl_days:
        day -= timedelta(days=1)
        if _is_trading_day(day):
            count += 1
    return day.strftime("%Y-%m-%d")


def _is_trading_day(day: date) -> bool:
    """工作区 trading_calendar 主路径；缺位回退周末规则（永不抛异常）。"""
    try:
        from trading_calendar import is_trading_day as _cal_day
        return _cal_day(day)
    except Exception:
        return day.weekday() < 5


def due_stocks(conn: sqlite3.Connection, now: str | None = None,
               limit: int = DEFAULT_DUE_LIMIT,
               ttl_days: int = TTL_TRADING_DAYS) -> list[sqlite3.Row]:
    """待分析队列：refresh_required=1 插队优先，再按 last_analyzed_at 最久（NULL 最前）。

    过滤：refresh_required=1 OR last_analyzed_at < TTL 边界（now 往回数
    ttl_days 个交易日的日期，格式 'YYYY-MM-DD' 前缀比较——last_analyzed_at 为
    'YYYY-MM-DD HH:MM:SS'，'YYYY-MM-DD...' < 'YYYY-MM-DD' 为 False，边界当天
    不误判为过期）。
    排序：refresh_required DESC → 组一按 refreshed_at 打标时间旧→新 →
    组二按 last_analyzed_at 升序（NULL 最前，SQL 升序 NULL 天然最前）→ stock 稳定序。
    表不存在 → 先建表，返回 []（安全空跑）。now 缺省取当前本地时间。
    """
    ensure_table(conn)
    if now is None:
        now = _now_str()
    boundary = ttl_boundary(now, ttl_days)
    return conn.execute(
        """
        SELECT stock, last_analyzed_at, refresh_required, refresh_reason,
               refresh_source, refreshed_at, last_result
        FROM analysis_schedule
        WHERE refresh_required=1 OR (last_analyzed_at IS NOT NULL
                                     AND last_analyzed_at < ?)
           OR last_analyzed_at IS NULL
        ORDER BY refresh_required DESC,
                 CASE WHEN refresh_required=1 THEN refreshed_at END ASC,
                 CASE WHEN refresh_required<>1 THEN last_analyzed_at END ASC,
                 stock ASC
        LIMIT ?
        """,
        (boundary, int(limit)),
    ).fetchall()


def due_reason(row: sqlite3.Row, now: str | None = None,
               ttl_days: int = TTL_TRADING_DAYS) -> str:
    """单行待分析原因（monitor 输出用）：refresh:reason 或 ttl-过期天数。

    过期天数=expiry_date − now_date（expiry=last_analyzed_at + ttl_days 交易日，
    用 next-style 正推）；refresh 行输出打标原因（ttl 也显式输出，打标语义优先）。
    """
    if row["refresh_required"]:
        return f"refresh:{row['refresh_reason'] or 'external'}"
    if row["last_analyzed_at"] is None:
        return "ttl-never"
    if now is None:
        now = _now_str()
    base = _parse(row["last_analyzed_at"]).date()
    expiry = base
    count = 0
    while count < ttl_days:
        expiry += timedelta(days=1)
        if _is_trading_day(expiry):
            count += 1
    return f"ttl-{(_parse(now).date() - expiry).days}"


def list_all(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """返回 analysis_schedule 全表行（按 stock 升序）。表不存在 → 先建表返回 []。"""
    ensure_table(conn)
    return conn.execute(
        """
        SELECT stock, last_analyzed_at, refresh_required, refresh_reason,
               refresh_source, refreshed_at, last_result
        FROM analysis_schedule
        ORDER BY stock
        """
    ).fetchall()


def get_stock(conn: sqlite3.Connection, stock: str) -> sqlite3.Row | None:
    """按股票名取单行；不存在返回 None。"""
    ensure_table(conn)
    return conn.execute(
        """
        SELECT stock, last_analyzed_at, refresh_required, refresh_reason,
               refresh_source, refreshed_at, last_result
        FROM analysis_schedule
        WHERE stock=?
        """,
        (stock,),
    ).fetchone()
