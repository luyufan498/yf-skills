"""Watchlist — 池（关注名单）管理，三档策略 + NEWS 消息组缓冲 + L1 人工锁"""
from datetime import datetime
from paper_trading_v2.db import get_connection, migrate_db

# NEWS = 消息组 L2 信号缓冲（方案 2.5 组×层模型）：无交易权限，收编扫描/晨审落库
STRATEGIES = ("L1", "L2", "L3", "NEWS")


class Watchlist:
    """池：动态关注名单。L1 人工锁定（AI 无权自动移出/降级）。"""

    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    def add(self, stock, code=None, strategy="L2", source="agent", reason="", pin=None,
            event_key=None, news_kind=None):
        """入池/调整档位。档位可自由设置（L1=持仓段由 allocate 联动，也可手动指定）；
        pin 为独立保护标记（pin=1 禁止删除但允许降级）。
        event_key/news_kind：消息组收编参数（NEWS 缓冲行落 G3 事件键与六类词表打标，
        watchlog.event_key 是 G3 归并的键源；pool.event_key 为 stock↔事件权威落点）。
        """
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy 必须是 {STRATEGIES}")
        conn = self._conn()
        try:
            with conn:
                existing = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
                if existing:
                    conn.execute("UPDATE pool SET code=COALESCE(?,code), strategy=?, pool_status='active', "
                                 "entered_at=COALESCE(entered_at, ?), "
                                 "event_key=COALESCE(?, event_key) WHERE stock=?",
                                 (code, strategy, datetime.now().isoformat(),
                                  event_key, stock))
                    if pin is not None:
                        conn.execute("UPDATE pool SET pin=? WHERE stock=?", (1 if pin else 0, stock))
                    action = "set_strategy"
                    from_str = existing['strategy']
                else:
                    conn.execute("INSERT INTO pool (stock, code, strategy, pool_status, "
                                 "refresh_cadence, entered_at, event_key, pin) "
                                 "VALUES (?,?,?,'active',?,?,?,?)",
                                 (stock, code, strategy,
                                  'daily' if strategy in ('L1', 'L2') else 'event',
                                  datetime.now().isoformat(), event_key, 1 if pin else 0))
                    action = "add"
                    from_str = None
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source, event_key, news_kind) "
                             "VALUES (?,?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), action, stock, from_str,
                              strategy, reason, source, event_key, news_kind))
            return True
        finally:
            conn.close()

    def set_pin(self, stock, pin: bool, source="agent", reason=""):
        """设置/取消 pin 保护（独立于档位：pin 只禁止删除，不限制升降级）。"""
        conn = self._conn()
        try:
            row = conn.execute("SELECT strategy, pin FROM pool WHERE stock=?", (stock,)).fetchone()
            if not row:
                raise ValueError(f"{stock} 不在池中")
            if not pin and row['pin'] and source == 'agent':
                # 取消 pin 需人工确认（防 agent 误删保护）
                raise ValueError("取消 pin 需人工确认（source=manual）")
            with conn:
                conn.execute("UPDATE pool SET pin=? WHERE stock=?", (1 if pin else 0, stock))
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source) VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'set_pin', stock, row['strategy'],
                              None, reason, source))
            return True
        finally:
            conn.close()

    def remove(self, stock, source="agent", reason="", archive=False):
        """移出池。pin=1 的股票禁止删除（可降级但不可移除）。

        archive=True（sleeve-m1 新增 --archive 路径）：档案化终态——pool_status='archived'
        + archived_at，不触发旧 removed 语义（清仓不回池，回池要新证据，方案 2.6b）。
        archive=False（默认）→ 旧行为逐字节不变（pool_status='removed'）。
        """
        conn = self._conn()
        try:
            row = conn.execute("SELECT strategy, pin FROM pool WHERE stock=?", (stock,)).fetchone()
            if not row:
                raise ValueError(f"{stock} 不在池中")
            if row['pin']:
                raise ValueError(f"{stock} 有 pin 保护（名单锁定），禁止删除；可降级到 L3 观察")
            with conn:
                if archive:
                    conn.execute("UPDATE pool SET pool_status='archived', archived_at=?, "
                                 "exit_reason=? WHERE stock=?",
                                 (datetime.now().isoformat(), reason, stock))
                    action = 'archive'
                else:
                    conn.execute("UPDATE pool SET pool_status='removed', exit_reason=? WHERE stock=?",
                                 (reason, stock))
                    action = 'remove'
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source) VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), action, stock, row['strategy'],
                              None, reason, source))
            return True
        finally:
            conn.close()

    def list(self, status="active"):
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM pool WHERE pool_status=? ORDER BY strategy, stock",
                                (status,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def log(self, stock=None, days=30, limit=50):
        """名单变更审计日志（入池/出池/升降级历史），按时间倒序。"""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._conn()
        try:
            q = "SELECT * FROM watchlog WHERE timestamp >= ?"
            params = [cutoff]
            if stock:
                q += " AND stock=?"
                params.append(stock)
            q += " ORDER BY id DESC LIMIT ?"
            params.append(str(limit))
            return [dict(r) for r in conn.execute(q, params).fetchall()]
        finally:
            conn.close()

    def get(self, stock):
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
