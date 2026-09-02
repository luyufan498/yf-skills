"""earliest_fill — sleeve-fill 成交时点门（2026-09-02 P+2 盲区改造）

裁决（回测实锤）：隔夜消息旧口径白睡两晚（次晨开槽+开槽日禁成交→P+2 才买）。
新规则：**earliest_fill_date = next_trading_day(消息入库时刻所在日)**，入库时刻=
newsdb events.**created_at**（成交门管"消息何时被我们知道"；started_at 归新鲜度/G闸，
两码事——2026-09-02 规格裁决）。
- 盘中消息（10:30 入库）→ 当日拒、次一交易日放行（T+1 语义维持）
- 隔夜消息（P 日晚入库）→ P+1 日开盘即可成交（修 P+2 盲区）
- newsdb 不可达/事件键/列解析失败 → **fail-closed 回退 opened_at+1 交易日**（旧行为）
  + audit 留痕（action='sleeve_fill_gate_fallback'）
- 成交放行 audit 记录 earliest_fill + 来源（action='sleeve_fill_earliest'，
  reason 含 earliest_fill=<date> source=newsdb|fallback）

实现口径：动态计算，**零 schema 列、零生产库迁移**；newsdb 只走只读 URI
（mode=ro，绝不写）。**生产 newsdb 现表若尚无 created_at 列 → 解析失败按
fail-closed 走 fallback（旧 T+1 行为）+ audit 留痕，绝不回读 started_at**——
门只看 created_at（规格裁决），newsdb 补列后自动升级新口径。
日历/今日/库路径全部可注入（模块属性 monkeypatch / 函数参数）。
--allow-same-day 豁免保留在 CLI 层。
"""
import os
import re
import sqlite3
from datetime import date as _date_cls
from datetime import datetime, timedelta
from typing import NamedTuple, Optional

# 事件键 → newsdb events.id（ND#<id> 即 newsdb 事件表主键；auto:/#bN 派生键无对应行）
ND_KEY_RE = re.compile(r"^ND#(\d+)$")

NEWS_DB_ENV = "STOCK_NEWS_DB"


class EarliestFill(NamedTuple):
    date: _date_cls       # 最早可成交日（含当日）
    source: str           # 'newsdb'（created_at 次一交易日）| 'fallback'（开槽日+1 交易日）


# ---------- 交易日历（依赖注入点：monkeypatch is_trading_day 即可全替换） ----------

_cal_is_trading_day = None
_cal_loaded = False


def _load_calendar():
    """真源=工作区 trading_calendar.py（sys.path 注入）；缺位时 _fallback_day 兜底。"""
    global _cal_is_trading_day, _cal_loaded
    if not _cal_loaded:
        root = os.environ.get("STOCK_ANALYSIS_WORKSPACE_ROOT",
                              "/home/catmouse/Github_Project/daily-stock-workspace")
        import sys
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from trading_calendar import is_trading_day as _c
            _cal_is_trading_day = _c
        except ImportError:
            _cal_is_trading_day = None
        _cal_loaded = True
    return _cal_is_trading_day


def _fallback_day(d: _date_cls) -> bool:
    """日历缺位兜底（同 trading_calendar._fallback 口径）：周末恒休 + 2026 官方段。"""
    if d.weekday() >= 5:
        return False
    md = d.month * 100 + d.day
    for a, b in ((101, 103), (215, 223), (404, 406), (501, 505),
                 (619, 621), (925, 927), (1001, 1007)):
        if a <= md <= b:
            return False
    return True


def is_trading_day(d) -> bool:
    cal = _load_calendar()
    if cal is not None:
        return bool(cal(d))
    return _fallback_day(_as_date(d))


def _as_date(d) -> _date_cls:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, _date_cls):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def next_trading_day(d=None) -> _date_cls:
    """d 的次一交易日（循环上限 400 天防日历坏数据死循环）。"""
    day = _as_date(d) if d is not None else datetime.now().date()
    for _ in range(400):
        day += timedelta(days=1)
        if is_trading_day(day):
            return day
    raise ValueError(f"next_trading_day({d}) 400 天内找不到交易日（日历数据异常）")


def today_str() -> str:
    """今日 ISO 日期（测试 monkeypatch 本函数即冻结时钟）。"""
    return datetime.now().date().isoformat()


# ---------- newsdb 入库时刻解析（只读，绝不写；基准=created_at，见模块 docstring） ----------

def _newsdb_intake_date(event_key: str, news_db_path: Optional[str] = None):
    """ND#<id> → newsdb events.created_at（入库时刻）所在日。任何失败返回 None。"""
    m = ND_KEY_RE.match(str(event_key or "").strip())
    if not m:
        return None
    db = news_db_path or os.environ.get(NEWS_DB_ENV) or ""
    if not db or not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT created_at FROM events WHERE id=?",
                               (int(m.group(1)),)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:      # 含 no such column（旧库无 created_at）→ fail-closed
        return None
    if not row or not row[0]:
        return None
    try:
        return _as_date(str(row[0]))
    except ValueError:
        return None


def resolve_earliest_fill(event_key: str, opened_at,
                          news_db_path: Optional[str] = None) -> EarliestFill:
    """槽 → 最早可成交日。newsdb created_at 次一交易日；解析失败 fail-closed
    回退开槽日次一交易日（旧 T+1 行为，source='fallback' 供 audit 标痕）。"""
    intake = _newsdb_intake_date(event_key, news_db_path)
    if intake is not None:
        try:
            return EarliestFill(next_trading_day(intake), "newsdb")
        except ValueError:
            pass  # 日历坏数据 → 同走 fallback
    try:
        return EarliestFill(next_trading_day(_as_date(str(opened_at)[:10])),
                            "fallback")
    except (ValueError, TypeError):
        # opened_at 也坏：极端保守=far future，永不可成交（宁可漏成交不可错成交）
        return EarliestFill(_date_cls(9999, 12, 31), "fallback")


def gate_allowed(resolution: EarliestFill, today_iso: Optional[str] = None) -> bool:
    """today ≥ earliest_fill.date 才可成交（ISO 串字典序=日期序）。"""
    return (today_iso or today_str()) >= str(resolution.date)


# ---------- audit 留痕 ----------

def already_audited(conn, action: str, event_key: str, today_iso=None) -> bool:
    """当日同 action 同键是否已留痕（防 cron 每 tick 刷 audit）。"""
    row = conn.execute(
        "SELECT 1 FROM audit WHERE action=? AND reason LIKE ? AND timestamp >= ? LIMIT 1",
        (action, f"%[{event_key}]", (today_iso or today_str()))).fetchone()
    return row is not None


def audit_gate(conn, action: str, event_key: str, reason: str,
               source: str = "cli-sleeve-fill"):
    """成交时点门 audit 行（amount 空=非资金事件；stock=NULL，键尾缀 [key]）。"""
    conn.execute(
        "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
        "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(), action, None, None, None, None,
         f"{reason} [{event_key}]", source))
