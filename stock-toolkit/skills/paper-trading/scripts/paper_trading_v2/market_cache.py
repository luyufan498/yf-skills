"""market.db 日K缓存库（raw 不复权 + 除权事件）

设计拍板（2026-09-04）：kline_daily 存 raw 不复权收盘价——raw 历史 bar 不可变
=缓存零漂移；除权跳跃不抹平，落 exright_events 事件表，供将来动量计算时折算。

双层刷新策略（fetch_kline_cached）：
1. 读时检查：库内最新 date >= 最近已收盘交易日 → 纯读库返回（网络 0 调用）
2. 有缺口 → 腾讯补缺口（date-range 参数，只拉缺的 bar）落库
3. TTL 全量重建：last_full_refresh_at 超 7 个交易日 → DELETE 该 code 重拉 count 250
4. 并发防风暴：BEGIN IMMEDIATE 写锁 + 锁内二次检查（别人已刷过就跳过重拉）
5. 抓取失败 → 返回现有缓存（陈旧但可用），记 last_fetch_fail_at，不抛异常

只落已收盘 bar（2026-09-09）：腾讯裸K端点会把当日进行中的 bar 一并返回，
全量刷新必须剪除 date > 最近已收盘交易日的 bar——否则盘中快照被当收盘价，
且 newest>=need 之后读时检查直接命中，脏 bar 永不自愈（实测 21 只票中招）。
读侧同样自愈：发现 newest > need 时删掉超前行再补缺。当日的实时价不走本缓存，
由调用方用 fetch-prices（批量实时）拼接。

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
# TTL：全量重建间隔（**交易日**，2026-09-09 由日历日改为交易日——与"新鲜度按
# 交易日"口径统一；7 交易日 ≈ 9.8 日历日）
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


def trading_days_between(start_date: str, end_date: str) -> int:
    """(start_date, end_date] 区间内的交易日数（含 end、不含 start）。"""
    try:
        days = load_trading_days()
    except Exception:
        return 0
    return sum(1 for d in days if start_date < d <= end_date)


def ttl_expired(last_iso: Optional[str], now: Optional[datetime] = None) -> bool:
    """全量重建是否到期：**按交易日计数**（2026-09-09 由日历日改）。

    last_iso = meta.last_full_refresh_at；判定 = 该时刻所在日之后、到最近已收盘
    交易日的交易日数 > TTL_FULL_REFRESH_DAYS。无记录 → 到期（首抓建基线）。
    日历不可用 → False（保守不重建，避免误删全库）。
    """
    if not last_iso:
        return True
    try:
        return (trading_days_between(str(last_iso)[:10], last_closed_trading_day(now))
                > TTL_FULL_REFRESH_DAYS)
    except Exception:
        return False


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
                       db_path: Optional[str] = None,
                       adjust: str = 'raw') -> List[dict]:
    """带缓存的日K获取（默认 raw 不复权；adjust='qfq' → 除权折算的 qfq 等效序列）

    adjust='qfq'（2026-09-09 加）：raw bar + exright_events 折算（o/h/l/c 同乘
    1/∏factor），供 ATR/peak 等原本直抓腾讯 qfq 的消费者改走缓存。事件表退化
    （首行无 'jump '）→ fail-closed 退回直抓腾讯 qfq（与合入前行为一致）。
    """
    bars = _fetch_kline_cached_raw(code, kline_type=kline_type, count=count, db_path=db_path)
    if adjust not in ('qfq', 'qfq_equivalent') or not bars:
        return bars
    try:
        folded = fold_qfq(bars, read_exright_events(code, db_path))
    except Exception:
        folded = None
    if folded is None:
        try:
            from paper_trading_v2.kline_fetcher import KLineDataFetcher
            return KLineDataFetcher().fetch_kline_data(code, 'day', count, adjust='qfq')
        except Exception:
            return bars
    return folded


def _fetch_kline_cached_raw(code: str, kline_type: str = 'day', count: int = 15,
                            db_path: Optional[str] = None) -> List[dict]:
    """带缓存的日K获取（raw 不复权）

    策略：库内最新 date >= 最近已收盘交易日 → 纯读库（0 网络）；
    有缺口 → 补缺 bar 落库；TTL 超 7 交易日 → DELETE 全量重拉 250 重建。
    并发防风暴：刷新前 BEGIN IMMEDIATE 写锁 + 锁内二次检查。
    抓取失败 → 返回现有缓存（陈旧但可用）+ 记失败时间。
    只落已收盘 bar（2026-09-09）。

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

    # TTL 判定：超期则全量重建（DELETE 该 code 重拉）——按交易日计数
    full_rebuild = False
    try:
        conn = _connect(db_path)
        try:
            full_rebuild = ttl_expired(_meta_get(conn, code, 'last_full_refresh_at'))
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
    if cached and need and newest > need:
        # 库里有"超前"bar（未收盘快照）→ 删掉自愈（2026-09-09 加：历史上全量
        # 刷新无 end 界，盘中会把当日进行中 bar 写库，newest>=need 后永不自愈）
        if _drop_bars_after(code, need, db_path) > 0:
            cached = read_cached_kline(code, count, db_path)
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
        if not ttl_expired(_meta_get(conn, code, 'last_full_refresh_at')):
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

        # 只落已收盘 bar（2026-09-09）：腾讯会把当日进行中 bar 一并返回
        raw, qfq = _clip_unclosed(raw, qfq)

        if not raw:
            # 空响应：可能是非法 code，也可能是临时故障——记失败时间，不删旧数据
            conn.rollback()
            _mark_fetch_fail(code, db_path)
            return read_cached_kline(code, count, db_path)

        conn.execute("DELETE FROM kline_daily WHERE code=?", (code,))
        _upsert_bars(conn, code, raw)
        events = detect_exright_jumps(raw, qfq or [])
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
            # 防御性剪除（end=need 已界，正常不会超；防数据源忽略区间参数）
            raw, _ = _clip_unclosed(raw, None)
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


def _drop_bars_after(code: str, date_upto: str, db_path: str) -> int:
    """删除 date > date_upto 的 bar（自愈"未收盘快照"历史污染），返回删除行数。"""
    try:
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("DELETE FROM kline_daily WHERE code=? AND date>?",
                               (code, date_upto))
            n = cur.rowcount or 0
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0


def _clip_unclosed(raw: Optional[List[dict]],
                   qfq: Optional[List[dict]] = None):
    """剪除"未收盘"bar（date > 最近已收盘交易日）。

    2026-09-09 加：腾讯裸K端点会把当日进行中的 bar 一并返回，原先全量刷新无 end 界
    直接落库 → 盘中快照被当收盘价，且 newest>=need 后永不自愈（实测 21 只票中招）。
    日历不可用 → 原样返回（宁可少剪，不可误删）。
    """
    try:
        need = last_closed_trading_day()
    except Exception:
        return raw, qfq
    raw2 = [b for b in (raw or []) if b.get('date') and b['date'] <= need]
    if qfq is None:
        return raw2, qfq
    qfq2 = [b for b in (qfq or []) if b.get('date') and b['date'] <= need]
    return raw2, qfq2


def normalize_code(raw: Optional[str]) -> str:
    """代码归一为腾讯/库内口径 'sh600703'/'sz002648'/'hk00700'。

    裸 6 位（600703 / 002536）按 5/6/8/9 开头 → sh，其余 → sz；
    点后缀（600703.SH / 000063.SZ）取交易所字母；已带前缀的原样小写。
    批量实时价接口只认前缀码，裸码会被静默丢弃（2026-09-04 实测）。
    """
    c = (raw or '').strip()
    if not c:
        return ''
    if '.' in c:
        num, _, suf = c.partition('.')
        suf = suf.upper()
        return ('sh' if suf == 'SH' else 'sz' if suf == 'SZ' else suf.lower()) + num.zfill(6)
    low = c.lower()
    if low[:2] in ('sh', 'sz', 'hk', 'us', 'gb'):
        # 港股是 5 位（hk00700），不能 zfill(6)
        return low[:2] + low[2:].zfill(6) if low[:2] in ('sh', 'sz') else low
    if c.isdigit():
        return ('sh' if c[0] in '5689' else 'sz') + c.zfill(6)
    return low


def read_closes_cached(code: str, count: int = 15,
                       db_path: Optional[str] = None) -> Dict:
    """某票「已收盘」收盘价序列（走读时自愈：缺口补抓 / TTL 重建 / 超前 bar 清理）。

    2026-09-09 抽出（原 watch_scan._fetch_cached_closes 前半段），CLI: ptrade2 closes-cached。

    Returns:
        {'code', 'dates': [升序], 'closes': [升序], 'newest': 最新已收盘日,
         'refreshed': 本次是否触发了网络刷新（meta 时间戳变化判定）}
    """
    code = normalize_code(code)
    before = _meta_snapshot(code, db_path)
    bars = fetch_kline_cached(code, count=count, db_path=db_path)
    after = _meta_snapshot(code, db_path)
    return {
        'code': code,
        'dates': [b['date'] for b in bars],
        'closes': [b['close'] for b in bars],
        'newest': bars[-1]['date'] if bars else None,
        'refreshed': before != after,
    }


def _meta_snapshot(code: str, db_path: Optional[str] = None) -> tuple:
    """(last_full_refresh_at, last_gap_fill_at) 快照，用于判定本次读是否触发刷新。"""
    db_path = db_path or market_db_path()
    if not os.path.exists(db_path):
        return ('', '')
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        try:
            rows = dict(conn.execute(
                "SELECT key, value FROM meta WHERE code=? AND key IN "
                "('last_full_refresh_at','last_gap_fill_at')", (code,)).fetchall())
        finally:
            conn.close()
        return (rows.get('last_full_refresh_at', ''), rows.get('last_gap_fill_at', ''))
    except Exception:
        return ('', '')


def fetch_closes_cached(codes: List[str], count: int = 15, *,
                        include_today: bool = True,
                        db_path: Optional[str] = None) -> Dict[str, dict]:
    """批量：N 票已收盘收盘价序列（0 网络暖缓存）+ **一次**批量实时价拼当日。

    2026-09-09 定稿的取数契约：历史 bar 只从 market.db 已收盘缓存来，当日价只从
    腾讯批量实时接口来（缓存永不存当日 bar）。网络成本 = 暖缓存 0 次 + 冷票/缺口
    各 1 次自愈；include_today=True 时 +1 次批量实时价（无论多少票）。

    Returns:
        {归一码: {'code','dates','closes','newest','refreshed',
                  'today','pre_close','quote_date','quote_time','name'}}
        实时价缺失（接口失败/非法码）→ today 为 None，不抛错。
    """
    norm = [normalize_code(c) for c in codes if (c or '').strip()]
    out = {}
    for code in norm:
        out[code] = read_closes_cached(code, count=count, db_path=db_path)
        out[code].update({'today': None, 'pre_close': None,
                          'quote_date': None, 'quote_time': None, 'name': None})
    if include_today and norm:
        try:
            from paper_trading_v2.price_fetcher import StockPriceFetcher
            infos = StockPriceFetcher().fetch_batch(norm) or {}
        except Exception as e:
            print(f"[market_cache] 批量实时价失败：{e}")
            infos = {}
        for code, info in infos.items():
            key = normalize_code(code)
            if key not in out:
                continue
            out[key].update({
                'today': info.current_price,
                'pre_close': info.pre_close,
                'quote_date': info.date,
                'quote_time': info.time,
                'name': info.name,
            })
    return out


def fetch_klines_cached(codes: List[str], count: int = 15,
                        db_path: Optional[str] = None,
                        adjust: str = 'raw') -> Dict[str, List[dict]]:
    """批量：每票的已收盘日K bar 列表（走读时自愈）。

    2026-09-09 加（CLI: `ptrade2 klines-cached`）——需要 high/low 的消费者
    （ATR / peak 回填 / G5 回检）用它，避免逐票 spawn 子进程；只要收盘价的用
    `fetch_closes_cached`（额外拼一次批量实时价）。
    adjust='qfq' → 除权折算（ATR 类消费者用，见 fetch_kline_cached）。
    """
    out: Dict[str, List[dict]] = {}
    for raw in codes:
        code = normalize_code(raw)
        if not code or code in out:
            continue
        out[code] = fetch_kline_cached(code, count=count, db_path=db_path, adjust=adjust)
    return out


def fold_qfq(bars: List[dict], events: Optional[List[dict]] = None) -> Optional[List[dict]]:
    """raw bars → qfq 等效 bars（除权折算，2026-09-09 泛化自 watch_scan._qfq_equivalent_closes）。

    公式：ratio(d)=qfq(d)/raw(d)，最新 bar re-anchor ratio=1；除权日 factor=ratio 跳变系数
    → 任意 bar 的 qfq 等效价 = raw × 1/∏factor(事件日 ∈ (bar_date, 今天])。
    o/h/l/c 同乘折算系数，volume 不动。

    Returns:
        同长度 list（无事件 → 原样返回）；**None = fail-closed**（事件表首行退化/因子非法，
        调用方应放弃该票或退回直抓 qfq），与 watch_scan 既有语义一致。
    """
    if not bars:
        return bars
    events = events or []
    if not events:
        return bars
    start = str(bars[0].get('date') or '')
    win: List[tuple] = []
    for i, ev in enumerate(events):
        d = str(ev.get('date') or '')
        if not (start < d):
            continue
        try:
            f = float(ev.get('factor') or 0)
        except (TypeError, ValueError):
            f = 0.0
        # 首行无 'jump '（检测时无 prev bar，退化为绝对 ratio）或因子非法 → fail-closed
        if f <= 0 or (i == 0 and 'jump ' not in str(ev.get('note') or '')):
            return None
        win.append((d, f))
    if not win:
        return bars
    out: List[dict] = []
    k, ei = 1.0, len(win) - 1
    for b in reversed(bars):
        d = str(b.get('date') or '')
        while ei >= 0 and win[ei][0] > d:
            k *= win[ei][1]
            ei -= 1
        nb = dict(b)
        for fld in ('open', 'high', 'low', 'close'):
            v = b.get(fld)
            nb[fld] = (v / k) if isinstance(v, (int, float)) else v
        out.append(nb)
    out.reverse()
    return out


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
