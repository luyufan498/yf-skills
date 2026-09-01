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

    def open_segments(self, include_news=False):
        """主池 open 段（默认排除 sleeve 成员段 strategy='NEWS'——双池互不侵占）。"""
        conn = self._conn()
        try:
            q = "SELECT * FROM position WHERE status='open'"
            if not include_news:
                q += " AND COALESCE(strategy,'') != 'NEWS'"
            rows = conn.execute(q + " ORDER BY id").fetchall()
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
        """总持仓段位上限 20（有持仓= L1，全部计入；sleeve 成员段不侵占主池段位）"""
        conn = self._conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) c FROM position WHERE status='open' "
                "AND COALESCE(strategy,'') != 'NEWS'"
            ).fetchone()['c']
            return count < 20
        finally:
            conn.close()
