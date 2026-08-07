"""扫描状态管理：记录每类实体上次扫描时间，供时效性调度。"""


def get_last_scan(conn, scope_type, scope_id):
    """返回上次扫描时间字符串，从未扫描返回 None。"""
    row = conn.execute(
        "SELECT last_scan FROM scan_log WHERE scope_type=? AND scope_id=?",
        (scope_type, scope_id)).fetchone()
    return row["last_scan"] if row else None


def set_last_scan(conn, scope_type, scope_id):
    """记录本次扫描时间（UPSERT）。"""
    conn.execute("""
        INSERT INTO scan_log (scope_type, scope_id, last_scan)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(scope_type, scope_id) DO UPDATE SET last_scan=excluded.last_scan
    """, (scope_type, scope_id))
    conn.commit()


def scan_due(conn, scope_type, scope_id, interval_hours=8):
    """判断是否到期需扫描。从未扫描 → 到期；距上次 ≥ interval_hours → 到期。"""
    last = get_last_scan(conn, scope_type, scope_id)
    if not last:
        return True
    row = conn.execute("""
        SELECT (julianday('now','localtime') - julianday(?)) * 24 >= ? AS due
    """, (last, interval_hours)).fetchone()
    return bool(row["due"])


def list_scan_scopes(conn):
    """列出采集 agent 应扫描的 scope 清单（从库拉取）。

    返回 [{scope_type, scope_id, last_scan, is_watchlist}...]：
    - stock: stocks 表全部（watchlist 优先）
    - industry: industries 表全部
    - market/policy: 全局 scope（market/global, policy/global）
    """
    scopes = []
    # 股票（watchlist 优先，其次 priority）
    for r in conn.execute("""
        SELECT code AS scope_id, is_watchlist FROM stocks
        ORDER BY is_watchlist DESC, priority DESC, code
    """):
        scopes.append({
            "scope_type": "stock", "scope_id": r["scope_id"],
            "last_scan": get_last_scan(conn, "stock", r["scope_id"]),
            "is_watchlist": r["is_watchlist"],
        })
    # 行业
    for r in conn.execute("SELECT id FROM industries ORDER BY id"):
        sid = str(r["id"])
        scopes.append({
            "scope_type": "industry", "scope_id": sid,
            "last_scan": get_last_scan(conn, "industry", sid),
            "is_watchlist": 0,
        })
    # 全局
    scopes.append({
        "scope_type": "market", "scope_id": "global",
        "last_scan": get_last_scan(conn, "market", "global"), "is_watchlist": 0,
    })
    scopes.append({
        "scope_type": "policy", "scope_id": "global",
        "last_scan": get_last_scan(conn, "policy", "global"), "is_watchlist": 0,
    })
    return scopes
