"""news-collect 执行侧（M1，方案 §4/§2.B）：任务执行框架 + T5 空置探索。

定位（红线：本文件只做调度簿记，不做真实采集）：实际采集动作由 cron prompt /
采集子代理注入 collector_fn 实现（M1 走 searxng/brave 不走 CDP——审计 B4）。

- run_task(conn, task_id, collector_fn)：通用执行框架——取任务行 → 调注入的
  collector_fn → 成功 mark_task_run 推进 next_due；失败 last_result='FAIL:<原因>'
  且**不推进** next_due（下次仍到期，方案 §4 步骤 2 的失败语义）。
- stale_candidates(conn, days=7)：T5 候选派生查询（方案 §3：T5 不建逐股状态行，
  查一次 SQL 即得候选清单）。池内股=stocks.is_watchlist=1（L1/L2/NEWS 名单落点）。
  关联判定口径：**messages 表**（该股经 event_stock 关联事件下的消息，
  MAX(messages.fetched_at) = 最近一次有消息映射到该股的时间）。只看 messages：
  "新闻/行业/大V 分析映射到股票"必然产生 messages 行；事件仅有 started_at
  无消息 = 空壳事件，不算已关联。
- mark_stale_checked(code, result)：检查留痕（stale_check_log 表，M3 "连续 2 轮
  翻不到→上报用户"判定依据）。
- kv_get/kv_set：newsdb 侧轻量状态（monitor last_stale_sig 集合签名存储）。
- stale_line：monitor 查 c 的输出行（集合签名字节稳定，见函数 docstring）。
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

STALE_SIG_KEY = "last_stale_sig"  # kv 键：monitor stale 输出签名（候选集 hash）

_TS_FMT = "%Y-%m-%d %H:%M:%S"  # 与 newsdb 其余表一致（datetime('now','localtime')）


def _ensure_stale_tables(conn: sqlite3.Connection) -> None:
    """幂等建 stale_check_log/kv_store（旧库直接补表，不触碰其他表）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stale_check_log (
            code         TEXT PRIMARY KEY,
            last_checked TEXT NOT NULL,
            result       TEXT
        );
        CREATE TABLE IF NOT EXISTS kv_store (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# 任务执行框架（T1-T4：调度簿记，采集动作由注入的 collector_fn 实现）
# ---------------------------------------------------------------------------

def run_task(conn: sqlite3.Connection, task_id: str, collector_fn) -> bool:
    """执行一个调度任务（通用框架，方案 §4 步骤 1-2 的簿记侧）。

    流程：取任务行（不存在 → ensure_default_tasks 幂等补 seed 后再取）→
    调 collector_fn(task_row)：
      - 成功（正常返回）→ mark_task_run 推进 next_due=now+ttl，
        last_result=返回摘要（非 str 时 json 序列化）；
      - 失败（抛异常）→ **不推进** next_due（下次仍到期），
        last_result='FAIL:<异常摘要>'（截 200 字符）。

    collector_fn 约定：入参=任务行 sqlite3.Row（无行时 None），返回轮次摘要
    （str 或可 json 序列化对象）；采集失败时抛异常。
    返回 True=成功推进；False=失败（未推进）。
    """
    from news_database import task_schedule as ts

    row = ts.get_task(conn, task_id)
    if row is None:
        ts.ensure_default_tasks(conn)
        row = ts.get_task(conn, task_id)
    try:
        result = collector_fn(row)
    except Exception as e:  # noqa: BLE001 —— 执行侧失败语义：吞异常落 FAIL 行
        reason = f"FAIL:{type(e).__name__}: {e}"[:200]
        # 失败只留簿记（last_result=FAIL:...），next_due 不推进：
        # 直接 UPDATE，绕过 mark_task_run 的推进逻辑
        conn.execute(
            "UPDATE task_schedule SET last_result=? WHERE task_id=?",
            (reason, task_id),
        )
        conn.commit()
        return False
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False, default=str)
    ts.mark_task_run(conn, task_id, result=result or "ok")
    return True


# ---------------------------------------------------------------------------
# T5 空置探索（派生查询，无逐股状态行——方案 §3）
# ---------------------------------------------------------------------------

def stale_candidates(conn: sqlite3.Connection, days: int = 7,
                     now: str | None = None) -> list[dict]:
    """T5 空置候选（方案 §2.B）：池内股近 days 天零关联。

    池内股 = stocks.is_watchlist=1。关联判定口径（**messages 表**）：该股经
    event_stock 关联的事件下 MAX(messages.fetched_at) = 最近一次有消息映射的
    时间；只有空壳事件（无消息）→ 视为从未关联（last_related_at=None）。
    返回 [{code, name, last_related_at, days}]，days=零关联整天数（从未关联
    → None），最久未关联在前（None 最前）。
    """
    _ensure_stale_tables(conn)
    if now is None:
        now = datetime.now().strftime(_TS_FMT)
    cutoff = (datetime.strptime(now, _TS_FMT) - timedelta(days=days)).strftime(_TS_FMT)
    rows = conn.execute(
        """
        SELECT s.code, s.name, MAX(m.fetched_at) AS last_related
        FROM stocks s
        LEFT JOIN event_stock es ON es.stock_code = s.code
        LEFT JOIN messages m     ON m.event_id = es.event_id
        WHERE s.is_watchlist = 1
        GROUP BY s.code, s.name
        HAVING last_related IS NULL OR last_related < ?
        """,
        (cutoff,),
    ).fetchall()
    out = []
    for r in rows:
        lr = r["last_related"]
        out.append({
            "code": r["code"],
            "name": r["name"],
            "last_related_at": lr,
            "days": None if lr is None else _days_between(lr, now),
        })
    # 从未关联（days=None，最严重）排最前，其余按零关联天数降序
    out.sort(key=lambda d: -(d["days"] if d["days"] is not None else 10**9))
    return out


def _days_between(past: str, now: str) -> int:
    """零关联天数 = now - past 的整天数（向下取整）。"""
    delta = datetime.strptime(now, _TS_FMT) - datetime.strptime(past, _TS_FMT)
    return int(delta.total_seconds() // 86400)


def mark_stale_checked(conn: sqlite3.Connection, code: str,
                       result: str = "") -> None:
    """T5 检查留痕（stale_check_log UPSERT）——M3 "连续 2 轮翻不到→上报"判定依据。"""
    _ensure_stale_tables(conn)
    conn.execute(
        """
        INSERT INTO stale_check_log (code, last_checked, result)
        VALUES (?, datetime('now','localtime'), ?)
        ON CONFLICT(code) DO UPDATE SET
            last_checked=excluded.last_checked, result=excluded.result
        """,
        (code, result),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# kv（newsdb 侧轻量状态：monitor last_stale_sig 等）
# ---------------------------------------------------------------------------

def kv_get(conn: sqlite3.Connection, key: str):
    """读 kv；JSON 可解析返回对象，否则返回原始字符串；缺失返回 None。"""
    _ensure_stale_tables(conn)
    row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return row[0]


def kv_set(conn: sqlite3.Connection, key: str, value) -> None:
    """写 kv（upsert；dict/list 序列化 JSON）。"""
    _ensure_stale_tables(conn)
    v = (json.dumps(value, ensure_ascii=False)
         if isinstance(value, (dict, list))
         else ("" if value is None else str(value)))
    conn.execute(
        """
        INSERT INTO kv_store (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,
            updated_at=datetime('now','localtime')
        """,
        (key, v),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# monitor 用：T5 输出行（集合签名稳定——候选集不变→同一字节）
# ---------------------------------------------------------------------------

def stale_sig(candidates: list[dict]) -> str:
    """候选集合签名：code 有序表 join 后 sha1（集合不变→签名不变）。"""
    codes = [c["code"] for c in candidates]
    return hashlib.sha1(",".join(codes).encode("utf-8")).hexdigest()


def stale_line(candidates: list[dict], conn: sqlite3.Connection,
               enabled: bool = False) -> list[str]:
    """T5 输出行（monitor 查 c 用）。字节稳定性规则（方案 §4 查 c，任务 M1 定案）：

    - enabled=False（开关关）→ []（不输出）
    - N=0（无候选）→ []（输出空）
    - 候选集 vs kv last_stale_sig **无变化** → 固定行 "[STALE] 空置候选 N 只"
      （只含 N，集合不变则字节恒定）
    - 候选集**变化** → 新行：有已知关联股时含最长零关联天数
      "[STALE] 空置候选 N 只（最长 M 天）"；全部从未关联时
      "[STALE] 空置候选 N 只（名单更新）"（保证与固定行字节必不同）
      —— N 相同但集合成员变化的场景（A 恢复关联、B 新空置）也能变字节唤醒。
    写 sig 进 kv（仅变化时）。返回输出行列表（0 或 1 行）。
    """
    if not enabled:
        return []
    n = len(candidates)
    if n == 0:
        return []
    sig = stale_sig(candidates)
    prev = kv_get(conn, STALE_SIG_KEY)
    if prev == sig:
        return [f"[STALE] 空置候选 {n} 只"]
    kv_set(conn, STALE_SIG_KEY, sig)
    max_days = max((c["days"] for c in candidates if c["days"] is not None),
                   default=None)
    if max_days is None:
        return [f"[STALE] 空置候选 {n} 只（名单更新）"]
    return [f"[STALE] 空置候选 {n} 只（最长 {max_days} 天）"]
