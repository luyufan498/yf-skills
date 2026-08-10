"""PoolManager — 持仓段管理：段状态、cooldown、段位上限"""
from datetime import datetime
from paper_trading_v2.db import get_connection, migrate_db


class PoolManager:
    """持仓段：open 段占预算，closed 段归档。L1 人工段不计段位上限。"""

    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    def open_segments(self):
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM position WHERE status='open' ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def in_cooldown(self, stock, now=None):
        now = now or datetime.now()
        conn = self._conn()
        try:
            row = conn.execute("SELECT cooldown_until FROM position WHERE stock=? ORDER BY id "
                               "DESC LIMIT 1", (stock,)).fetchone()
            if not row or not row[0]:
                return False
            return now < datetime.fromisoformat(row[0])
        finally:
            conn.close()

    def is_agent_slot_available(self):
        """agent 段位上限 8（L1 不计）"""
        conn = self._conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) c FROM position WHERE status='open' AND strategy != 'L1'"
            ).fetchone()['c']
            return count < 8
        finally:
            conn.close()
