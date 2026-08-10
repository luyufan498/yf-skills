"""MasterPoolManager — 总池账本（pool_ledger）+ 资金流水（audit）"""
from datetime import datetime, timedelta
from paper_trading_v2.db import get_connection, migrate_db


class MasterPoolManager:
    """总池账本：total 固定，free 被 allocate/topup/release 驱动，审计留痕。"""

    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    def init_pool(self, total: float, source="manual"):
        conn = self._conn()
        try:
            with conn:
                row = conn.execute("SELECT * FROM pool_ledger WHERE id=1").fetchone()
                if row:
                    raise ValueError(f"总池已初始化 total={row['total']}，需删除数据库重置")
                conn.execute("INSERT INTO pool_ledger (id, total, free, updated_at) "
                             "VALUES (1, ?, ?, ?)", (total, total, datetime.now().isoformat()))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'init', None, total, 0, total,
                              '初始化总池', source))
            return True
        finally:
            conn.close()

    def show(self) -> dict:
        conn = self._conn()
        try:
            ledger = conn.execute("SELECT * FROM pool_ledger WHERE id=1").fetchone()
            if not ledger:
                return {"error": "总池未初始化"}
            open_count = conn.execute("SELECT COUNT(*) c FROM position WHERE status='open'").fetchone()['c']
            occupied = conn.execute(
                "SELECT COALESCE(SUM(budget),0) s FROM position WHERE status='open'").fetchone()['s']
            realized = ledger['free'] + occupied - ledger['total']  # 累计已实现盈亏（进池部分）
            return {
                "total": ledger['total'], "free": ledger['free'],
                "occupied": occupied,
                "usage_rate": occupied / ledger['total'] if ledger['total'] else 0,
                "realized_pnl": realized,
                "open_segments": open_count,
            }
        finally:
            conn.close()

    def _get_free(self, conn):
        row = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()
        return row[0] if row else None

    def _get_strategy(self, conn, stock):
        row = conn.execute("SELECT strategy, code FROM pool WHERE stock=? AND pool_status='active'",
                           (stock,)).fetchone()
        return (row['strategy'], row['code']) if row else ('L2', None)

    def allocate(self, stock, amount, reason, source="agent", code=None):
        """开持仓段：从 free 拨 budget，建/重置账户，记 audit。账户与池同事务。"""
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            free = self._get_free(conn)
            if free is None:
                raise ValueError("总池未初始化，请先 ptrade2 master-pool-init")
            if amount <= 0:
                raise ValueError("分配金额必须 > 0")
            if amount > free:
                raise ValueError(f"总池空闲不足：需 ¥{amount:,.0f}，空闲 ¥{free:,.0f}")
            already_open = conn.execute(
                "SELECT id FROM position WHERE stock=? AND status='open'", (stock,)).fetchone()
            if already_open:
                raise ValueError(f"{stock} 已有 open 段，需先 release 再重新 allocate")
            total = conn.execute("SELECT total FROM pool_ledger WHERE id=1").fetchone()[0]
            if amount > 0.3 * total:
                raise ValueError(f"单股分配超过总池 30%：¥{amount:,.0f} > 30%×{total:,.0f}")
            strat, pool_code = self._get_strategy(conn, stock)
            if code is None:
                code = pool_code
            if strat == 'L1' and source != 'manual':
                raise ValueError("L1 锁定股需人工 allocate（source=manual）")
            if strat != 'L1':
                cool = conn.execute(
                    "SELECT cooldown_until FROM position WHERE stock=? ORDER BY id DESC LIMIT 1",
                    (stock,)).fetchone()
                if cool and cool[0] and datetime.now() < datetime.fromisoformat(cool[0]):
                    raise ValueError(f"{stock} 在冷却期内（至 {cool[0]}），禁止 allocate")
                open_count = conn.execute(
                    "SELECT COUNT(*) c FROM position WHERE status='open' AND strategy!='L1'"
                ).fetchone()['c']
                if open_count >= 8:
                    raise ValueError(f"持仓段已满（{open_count}/8），需先 release 再开新段")
            with conn:
                new_free = free - amount
                conn.execute("UPDATE pool_ledger SET free=?, updated_at=? WHERE id=1",
                             (new_free, now))
                conn.execute("INSERT INTO position (stock, code, strategy, status, budget, "
                             "topup_total, opened_at) VALUES (?,?,?,'open',?,0,?)",
                             (stock, code, strat, amount, now))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (now, 'allocate', stock, amount, free, new_free, reason, source))
                # ---- 账户：同一事务直接 SQL ----
                acct = conn.execute("SELECT id FROM accounts WHERE stock_name=?", (stock,)).fetchone()
                if acct:
                    aid = acct[0]
                    # 归档旧段操作（保留历史），再重置账户
                    seg_id = conn.execute(
                        "SELECT id FROM position WHERE stock=? AND status='closed' "
                        "ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
                    seg_id = seg_id[0] if seg_id else None
                    conn.execute(
                        "INSERT INTO operations_archive (account_id, archived_at, segment_id, type, "
                        "price, quantity, amount, cost, profit, capital, timestamp, note, seq) "
                        "SELECT ?, ?, ?, type, price, quantity, amount, cost, profit, capital, "
                        "timestamp, note, seq FROM operations WHERE account_id=?",
                        (aid, now, seg_id, aid))
                    conn.execute("DELETE FROM operations WHERE account_id=?", (aid,))
                    conn.execute("DELETE FROM positions WHERE account_id=?", (aid,))
                    conn.execute("DELETE FROM condition_history WHERE condition_id IN "
                                 "(SELECT id FROM conditions WHERE account_id=?)", (aid,))
                    conn.execute("DELETE FROM conditions WHERE account_id=?", (aid,))
                    conn.execute("DELETE FROM exright_applied WHERE account_id=?", (aid,))
                    conn.execute(
                        "UPDATE accounts SET stock_code=COALESCE(?, stock_code), capital_total=?, "
                        "capital_available=?, capital_used=0, fifo_index=-1, fifo_offset=0, "
                        "updated_at=? WHERE id=?",
                        (code, amount, amount, now, aid))
                else:
                    cur = conn.execute(
                        "INSERT INTO accounts (stock_name, stock_code, capital_total, "
                        "capital_available, capital_used, fifo_index, fifo_offset, created_at, "
                        "updated_at) VALUES (?,?,?,?,0,-1,0,?,?)",
                        (stock, code, amount, amount, now, now))
                    aid = cur.lastrowid
                conn.execute("INSERT INTO operations (account_id, seq, type, capital, timestamp, "
                             "note) VALUES (?,0,'init',?,?,'初始化资金池')",
                             (aid, amount, now))
            return True
        finally:
            conn.close()

    def topup(self, stock, amount, reason, source="agent"):
        """段内注资：从 free 拨差额进账户，同步加 total/available。同事务。"""
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            free = self._get_free(conn)
            if free is None:
                raise ValueError("总池未初始化")
            if amount <= 0:
                raise ValueError("注资金额必须 > 0")
            if amount > free:
                raise ValueError(f"总池空闲不足：需 ¥{amount:,.0f}，空闲 ¥{free:,.0f}")
            seg = conn.execute("SELECT * FROM position WHERE stock=? AND status='open'",
                               (stock,)).fetchone()
            if not seg:
                raise ValueError(f"{stock} 没有 open 段，需先 allocate")
            acct = conn.execute("SELECT id FROM accounts WHERE stock_name=?", (stock,)).fetchone()
            if not acct:
                raise ValueError(f"账户 {stock} 不存在")
            aid = acct[0]
            with conn:
                conn.execute("UPDATE position SET budget=budget+?, topup_total=topup_total+? "
                             "WHERE id=?", (amount, amount, seg['id']))
                new_free = free - amount
                conn.execute("UPDATE pool_ledger SET free=?, updated_at=? WHERE id=1",
                             (new_free, now))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (now, 'topup', stock, amount, free, new_free, reason, source))
                conn.execute("UPDATE accounts SET capital_total=capital_total+?, "
                             "capital_available=capital_available+?, updated_at=? WHERE id=?",
                             (amount, amount, now, aid))
            return True
        finally:
            conn.close()

    def release(self, stock, reason, source="agent"):
        """关持仓段：空仓后把现值回 free，段归档，7 日 cooldown。L1 需人工。"""
        conn = self._conn()
        try:
            seg = conn.execute("SELECT * FROM position WHERE stock=? AND status='open'",
                               (stock,)).fetchone()
            if not seg:
                raise ValueError(f"{stock} 没有 open 段")
            if seg['strategy'] == 'L1' and source != 'manual':
                raise ValueError("L1 锁定股不能由 agent release，需人工确认")
            # 读账户（可能触发写回）在池写事务前完成，避免同库写锁冲突
            from paper_trading_v2.trading import PaperTrader
            trader = PaperTrader()
            acct = trader.get_account(stock)
            if acct is None:
                raise ValueError(f"账户 {stock} 不存在")
            qty, _ = trader.get_remaining_position(acct)
            if qty > 0:
                raise ValueError(f"{stock} 仍有持仓 {qty} 股，先清仓再 release")
            value = acct.capital_pool.available
            free = self._get_free(conn)
            new_free = free + value
            now = datetime.now().isoformat()
            with conn:
                conn.execute("UPDATE pool_ledger SET free=?, updated_at=? WHERE id=1",
                             (new_free, now))
                realized = value - seg['budget']
                conn.execute("UPDATE position SET status='closed', closed_at=?, close_value=?, "
                             "realized_pnl=?, cooldown_until=? WHERE id=?",
                             (now, value, realized,
                              (datetime.now() + timedelta(days=7)).isoformat(), seg['id']))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (now, 'release', stock, value, free, new_free, reason, source))
            return True
        finally:
            conn.close()

    def records(self, days=None):
        conn = self._conn()
        try:
            if days:
                since = (datetime.now() - timedelta(days=days)).isoformat()
                rows = conn.execute("SELECT * FROM audit WHERE timestamp>=? ORDER BY id",
                                    (since,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM audit ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
