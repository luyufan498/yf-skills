"""market.db 日K缓存库（raw 不复权 + 除权事件）

设计拍板（2026-09-04）：kline_daily 存 raw 不复权收盘价——raw 历史 bar 不可变
=缓存零漂移；除权跳跃不抹平，落 exright_events 事件表，供将来动量计算时折算。

双层刷新策略（fetch_kline_cached）：
1. 读时检查：库内最新 date >= 最近已收盘交易日 → 纯读库返回（网络 0 调用）
2. 有缺口 → 腾讯补缺口（date-range 参数，只拉缺的 bar）落库
3. TTL 全量重建：last_full_refresh_at 超 7 天 → DELETE 该 code 重拉 count 250
4. 并发防风暴：BEGIN IMMEDIATE 写锁 + 锁内二次检查（别人已刷过就跳过重拉）
5. 抓取失败 → 返回现有缓存（陈旧但可用），记 last_fetch_fail_at，不抛异常

除权检测：同一次刷新并行拿 raw + qfq 两份序列，逐 bar ratio=qfq_close/raw_close，
ratio 序列跳变点 = 除权日（factor=跳变后的 ratio，即 raw→qfq 折算系数）。
阈值 1e-4（实测 sh600000 平时 ratio 噪声 ~1e-4，除权跳变 ~4.6e-2）。
"""
import json
import os
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

# 除权检测兜底阈值：ratio=qfq/raw 序列日间跳变超过该值视为除权日。
# 实测（2026-09-04）：腾讯价格四舍五入噪声 ~1e-4（@250元股）~2e-3（@9元股），
# 真除权跳变 ~4.6e-2（sh600000 10派4.2）。5e-3 居中分离噪声与真事件；
# 小额分红（跳变 < 5e-3）靠 qfq bar 自带的 FHcontent 权威标记兜住。
EXRIGHT_JUMP_THRESHOLD = 5e-3
# TTL：全量重建间隔（天）
TTL_FULL_REFRESH_DAYS = 7
# 全量重建拉取条数
FULL_REFRESH_COUNT = 250

_CALENDAR_PATH = Path('/home/catmouse/Github_Project/daily-stock-workspace/data/trading_calendar.json')
_TZ = ZoneInfo('Asia/Shanghai')


def market_db_path() -> str:
    """market.db 路径：$STOCK_ANALYSIS_WORKSPACE/market.db（与 master_pool.db 同目录）"""
    workspace = os.environ.get('STOCK_ANALYSIS_WORKSPACE',
                               '/home/catmouse/Github_Project/daily-stock-workspace/.paper-trading')
    return os.path.join(workspace, 'market.db')


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """WAL + busy_timeout 连接（多进程并发安全）"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(db_path: Optional[str] = None) -> str:
    """建表（幂等）。返回实际 db 路径。"""
    db_path = db_path or market_db_path()
    conn = _connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kline_daily (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (code, date)
            );
            CREATE TABLE IF NOT EXISTS exright_events (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                factor REAL,
                note TEXT,
                PRIMARY KEY (code, date)
            );
            CREATE TABLE IF NOT EXISTS meta (
                code TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                updated_at TEXT,
                PRIMARY KEY (code, key)
            );
        """)
        conn.commit()
    finally:
        conn.close()
    return db_path


# ---------- 交易日历 ----------

def load_trading_days(calendar_path: Optional[str] = None) -> List[str]:
    """读 trading_calendar.json 真源，返回升序 'YYYY-MM-DD' 交易日列表（2026 覆盖）"""
    path = Path(calendar_path) if calendar_path else _CALENDAR_PATH
    with open(path, 'r', encoding='utf-8') as f:
        cal = json.load(f)
    days = []
    for year_list in cal['markets']['CN_A_SHARE']['years'].values():
        for d in year_list:
            days.append(f"{d:08d}"[:4] + '-' + f"{d:08d}"[4:6] + '-' + f"{d:08d}"[6:8])
    days.sort()
    return days


def last_closed_trading_day(now: Optional[datetime] = None,
                            calendar_path: Optional[str] = None) -> str:
    """最近已收盘交易日（'YYYY-MM-DD'）

    收盘判定：当日 15:00 前（不含）→ 上一交易日；当日为交易日且 >= 15:00 → 当日。
    （港股 16:00 收盘，A股口径统一从紧用 15:00：当 15:30 扫描时两者都已收盘。）
    """
    now = now if now is not None else datetime.now(_TZ)
    days = load_trading_days(calendar_path)
    today_s = now.strftime('%Y-%m-%d')
    closed_cutoff = now.hour >= 15
    for d in reversed(days):
        if d < today_s:
            return d
        if d == today_s and closed_cutoff:
            return d
    return days[-1] if days else today_s


# ---------- meta kv ----------

def _meta_get(conn: sqlite3.Connection, code: str, key: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE code=? AND key=?", (code, key)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, code: str, key: str, value: str):
    conn.execute(
        "INSERT INTO meta (code, key, value, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(code,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (code, key, value, datetime.now().isoformat(timespec='seconds')))


# ---------- 库内读取 ----------

def read_cached_kline(code: str, count: int = 15, db_path: Optional[str] = None) -> List[dict]:
    """读库内已有日K（按日期降序取最近 count 条，返回时升序）。纯读，不触网络。

    Returns:
        升序 list[dict]：{'date','open','high','low','close','volume'}（raw 不复权）
    """
    db_path = db_path or market_db_path()
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM kline_daily "
            "WHERE code=? ORDER BY date DESC LIMIT ?", (code, max(int(count), 1))).fetchall()
    finally:
        conn.close()
    rows.reverse()  # 降序取最近 → 反转成升序
    return [
        {'date': r[0], 'open': r[1], 'high': r[2], 'low': r[3], 'close': r[4], 'volume': r[5]}
        for r in rows
    ]


def read_exright_events(code: str, db_path: Optional[str] = None) -> List[dict]:
    """读某 code 的除权事件（升序）。供将来动量 raw→qfq 折算。"""
    db_path = db_path or market_db_path()
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT date, factor, note FROM exright_events WHERE code=? ORDER BY date",
            (code,)).fetchall()
    finally:
        conn.close()
    return [{'date': r[0], 'factor': r[1], 'note': r[2]} for r in rows]


# ---------- 除权检测 ----------

def detect_exright_jumps(raw_bars: List[dict], qfq_bars: List[dict]) -> List[dict]:
    """除权事件检测（双信号）

    1. 权威信号：qfq bar 携带的腾讯除权标记（exright dict 含 FHcontent，
       仅真除权除息日有）→ 无条件记事件（可捕获跳变 < 阈值的小额分红）
    2. 兜底信号：ratio=qfq/raw 日间跳变 > EXRIGHT_JUMP_THRESHOLD
       （qfq 标记缺失时的兜底，如部分市场）

    factor 语义 = 跳变系数 ratio[cur]/ratio[prev]（跨 re-anchor 不变量，
    raw 动量 × factor = 复权动量）；无 prev bar 时退化为该日绝对 ratio。
    note 记录绝对 ratio 过渡与 FHcontent，供下游核对。
    """
    qmap = {b['date']: b for b in qfq_bars or []}
    prev_ratio = None
    events = []
    for b in sorted(raw_bars or [], key=lambda x: x['date']):
        q = qmap.get(b['date'])
        if not q or not b.get('close') or not q.get('close') or not b['close']:
            continue
        ratio = float(q['close']) / float(b['close'])
        meta = q.get('exright') or {}
        fh = meta.get('FHcontent', '') or ''
        jump_ratio = (ratio / prev_ratio) if prev_ratio else None
        jump_hit = prev_ratio is not None and abs(ratio - prev_ratio) > EXRIGHT_JUMP_THRESHOLD
        if fh or jump_hit:
            note_parts = []
            if prev_ratio is not None:
                note_parts.append(f"ratio {prev_ratio:.6f} -> {ratio:.6f}")
            if jump_ratio is not None:
                note_parts.append(f"jump {jump_ratio:.6f}")
            if fh:
                note_parts.append(f"FHcontent={fh}")
            if meta.get('cqr') and meta.get('cqr') != b['date']:
                note_parts.append(f"cqr={meta['cqr']}")
            events.append({
                'date': b['date'],
                'factor': round(jump_ratio, 8) if jump_ratio is not None else round(ratio, 8),
                'note': '; '.join(note_parts) or 'exright marker',
            })
        prev_ratio = ratio
    return events


def _upsert_exright_events(conn: sqlite3.Connection, code: str, events: List[dict]):
    for ev in events:
        conn.execute(
            "INSERT INTO exright_events (code, date, factor, note) VALUES (?,?,?,?) "
            "ON CONFLICT(code,date) DO UPDATE SET factor=excluded.factor, note=excluded.note",
            (code, ev['date'], ev['factor'], ev.get('note', '')))


# ---------- 腾讯抓取（薄封装，测试 monkeypatch 点） ----------

def _fetch_raw_bars(code: str, start: str = '', end: str = '', count: int = 15) -> List[dict]:
    """腾讯 raw 日K（date-range 可选）。测试 monkeypatch 本函数隔离网络。"""
    from paper_trading_v2.kline_fetcher import KLineDataFetcher
    f = KLineDataFetcher()
    if start or end:
        return f._fetch_kline_range(code, start, end, count)
    return f.fetch_raw_kline(code, count)


def _fetch_qfq_bars(code: str, count: int = 250) -> List[dict]:
    """腾讯 qfq 日K。测试 monkeypatch 本函数隔离网络。"""
    from paper_trading_v2.kline_fetcher import KLineDataFetcher
    return KLineDataFetcher().fetch_kline_data(code, 'day', count, adjust='qfq')


# ---------- 落库 ----------

def _upsert_bars(conn: sqlite3.Connection, code: str, bars: List[dict]):
    for b in bars:
        if not b or not b.get('date'):
            continue
        conn.execute(
            "INSERT INTO kline_daily (code, date, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(code,date) DO UPDATE SET open=excluded.open, high=excluded.high, "
            "low=excluded.low, close=excluded.close, volume=excluded.volume",
            (code, b['date'], b.get('open'), b.get('high'), b.get('low'),
             b.get('close'), b.get('volume')))


# ---------- 主入口 ----------

def fetch_kline_cached(code: str, kline_type: str = 'day', count: int = 15,
                       db_path: Optional[str] = None) -> List[dict]:
    """带缓存的日K获取（raw 不复权）

    策略：库内最新 date >= 最近已收盘交易日 → 纯读库（0 网络）；
    有缺口 → 补缺 bar 落库；TTL 超 7 天 → DELETE 全量重拉 250 重建。
    并发防风暴：刷新前 BEGIN IMMEDIATE 写锁 + 锁内二次检查。
    抓取失败 → 返回现有缓存（陈旧但可用）+ 记失败时间。

    Args:
        code: 股票代码（如 'sh688041'）
        kline_type: 仅支持 'day'（其他类型透传直抓不缓存）
        count: 返回最近 N 条

    Returns:
        升序 list[dict]：{'date','open','high','low','close','volume'}（raw 不复权）
    """
    if kline_type != 'day':
        # 周K/月K/分钟K 不缓存，直抓（qfq 保持既有行为）
        from paper_trading_v2.kline_fetcher import KLineDataFetcher
        return KLineDataFetcher().fetch_kline_data(code, kline_type, count)

    code = code.strip().lower()
    count = max(int(count), 1)
    db_path = db_path or market_db_path()
    init_db(db_path)

    # TTL 判定：超期则全量重建（DELETE 该 code 重拉）
    full_rebuild = False
    try:
        conn = _connect(db_path)
        try:
            last = _meta_get(conn, code, 'last_full_refresh_at')
            if last:
                age_days = (time.time() - datetime.fromisoformat(last).timestamp()) / 86400.0
                if age_days > TTL_FULL_REFRESH_DAYS:
                    full_rebuild = True
            else:
                full_rebuild = True  # 从未全量刷新过 → 首抓按全量建基线
        finally:
            conn.close()
    except Exception:
        full_rebuild = True

    if full_rebuild:
        return _full_refresh(code, count, db_path)

    # 读时检查：库内最新 date 是否已覆盖最近已收盘交易日
    cached = read_cached_kline(code, count, db_path)
    try:
        need = last_closed_trading_day()
    except Exception:
        need = None  # 日历不可用 → 无法判定，直接返回缓存
    newest = cached[-1]['date'] if cached else ''
    if cached and need and newest >= need:
        return cached  # 纯读库命中

    # 有缺口 → 写锁 + 锁内二次检查 + 补缺
    if need is None:
        return cached  # 日历不可用：无法判定缺口，返回缓存（陈旧但可用）
    return _gap_fill(code, count, need, db_path)


def _full_refresh(code: str, count: int, db_path: str) -> List[dict]:
    """TTL 全量重建：写锁内二次检查 TTL，DELETE 该 code 重拉 count 250。"""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 锁内二次检查（并发防风暴：别人已刷过就跳过重拉）
        last = _meta_get(conn, code, 'last_full_refresh_at')
        if last:
            age_days = (time.time() - datetime.fromisoformat(last).timestamp()) / 86400.0
            if age_days <= TTL_FULL_REFRESH_DAYS:
                conn.rollback()
                return read_cached_kline(code, count, db_path)
        try:
            raw = _fetch_raw_bars(code, count=FULL_REFRESH_COUNT)
            qfq = _fetch_qfq_bars(code, count=FULL_REFRESH_COUNT)
        except Exception as e:
            print(f"[market_cache] full refresh fetch fail {code}: {e}")
            conn.rollback()
            _mark_fetch_fail(code, db_path)
            return read_cached_kline(code, count, db_path)

        if not raw:
            # 空响应：可能是非法 code，也可能是临时故障——记失败时间，不删旧数据
            conn.rollback()
            _mark_fetch_fail(code, db_path)
            return read_cached_kline(code, count, db_path)

        conn.execute("DELETE FROM kline_daily WHERE code=?", (code,))
        _upsert_bars(conn, code, raw)
        events = detect_exright_jumps(raw, qfq)
        if events:
            conn.execute("DELETE FROM exright_events WHERE code=?", (code,))
            _upsert_exright_events(conn, code, events)
        _meta_set(conn, code, 'last_full_refresh_at', datetime.now().isoformat(timespec='seconds'))
        _meta_set(conn, code, 'last_full_refresh_bars', str(len(raw)))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[market_cache] full refresh error {code}: {e}")
        _mark_fetch_fail(code, db_path)
    finally:
        conn.close()
    return read_cached_kline(code, count, db_path)


def _gap_fill(code: str, count: int, need: str, db_path: str) -> List[dict]:
    """缺口补抓：只拉缺的 bar（date-range），写锁内二次检查。"""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 锁内二次检查：可能另一进程刚补完
        row = conn.execute("SELECT MAX(date) FROM kline_daily WHERE code=?", (code,)).fetchone()
        newest = row[0] if row and row[0] else ''
        cached = read_cached_kline(code, count, db_path)
        if newest and need and newest >= need:
            conn.rollback()
            return cached
        if not cached:
            # 库里没数据（比如刚被人 DELETE）→ 降级走全量
            conn.rollback()
            return _full_refresh(code, count, db_path)

        start = (datetime.strptime(newest, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
        try:
            raw = _fetch_raw_bars(code, start=start, end=need, count=count)
            qfq = _fetch_qfq_bars(code, count=count)
        except Exception as e:
            print(f"[market_cache] gap fill fetch fail {code}: {e}")
            conn.rollback()
            _mark_fetch_fail(code, db_path)
            return cached

        if raw:
            _upsert_bars(conn, code, raw)
            _upsert_exright_events(conn, code, detect_exright_jumps(raw, qfq))
            _meta_set(conn, code, 'last_gap_fill_at', datetime.now().isoformat(timespec='seconds'))
            conn.commit()
        else:
            # 无新 bar：若今天是交易日且已收盘仍无 → 记录但不报错（数据源延迟等）
            conn.rollback()
            _mark_fetch_fail(code, db_path)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[market_cache] gap fill error {code}: {e}")
        _mark_fetch_fail(code, db_path)
    finally:
        conn.close()
    return read_cached_kline(code, count, db_path)


def _mark_fetch_fail(code: str, db_path: str):
    """记失败时间（独立短连接，绝不抛）"""
    try:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            _meta_set(conn, code, 'last_fetch_fail_at', datetime.now().isoformat(timespec='seconds'))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
