"""Watchlist — 池（关注名单）管理，三档策略 + L1 人工锁"""
from datetime import datetime
from paper_trading_v2.db import get_connection, migrate_db

STRATEGIES = ("L1", "L2", "L3")


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

    def add(self, stock, code=None, strategy="L2", source="agent", reason=""):
        """入池。L1 只能由人工添加。"""
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy 必须是 {STRATEGIES}")
        if strategy == "L1" and source != "manual":
            raise ValueError("L1 只能由人工添加（source=manual）")
        conn = self._conn()
        try:
            with conn:
                existing = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
                if existing:
                    if existing['strategy'] == 'L1' and source != 'manual':
                        raise ValueError("L1 锁定股的任何变更需人工确认（source=manual）")
                    conn.execute("UPDATE pool SET code=COALESCE(?,code), strategy=?, pool_status='active', "
                                 "entered_at=COALESCE(entered_at, ?) WHERE stock=?",
                                 (code, strategy, datetime.now().isoformat(), stock))
                    action = "set_strategy"
                    from_str = existing['strategy']
                else:
                    conn.execute("INSERT INTO pool (stock, code, strategy, pool_status, "
                                 "refresh_cadence, entered_at) VALUES (?,?,?,'active',?,?)",
                                 (stock, code, strategy,
                                  'daily' if strategy != 'L1' else 'manual',
                                  datetime.now().isoformat()))
                    action = "add"
                    from_str = None
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source) VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), action, stock, from_str,
                              strategy, reason, source))
            return True
        finally:
            conn.close()

    def remove(self, stock, source="agent", reason=""):
        """移出池。L1 需人工。"""
        conn = self._conn()
        try:
            row = conn.execute("SELECT strategy FROM pool WHERE stock=?", (stock,)).fetchone()
            if not row:
                raise ValueError(f"{stock} 不在池中")
            if row['strategy'] == 'L1' and source != 'manual':
                raise ValueError("L1 锁定股不能由 agent 移除，需人工确认")
            with conn:
                conn.execute("UPDATE pool SET pool_status='removed', exit_reason=? WHERE stock=?",
                             (reason, stock))
                conn.execute("INSERT INTO watchlog (timestamp, action, stock, strategy_from, "
                             "strategy_to, reason, source) VALUES (?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'remove', stock, row['strategy'],
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

    def get(self, stock):
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
