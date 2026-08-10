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
            occupied = ledger['total'] - ledger['free']
            return {
                "total": ledger['total'], "free": ledger['free'],
                "occupied": occupied,
                "usage_rate": occupied / ledger['total'] if ledger['total'] else 0,
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
        """开持仓段：从 free 拨 budget，建账户，记 audit。"""
        conn = self._conn()
        try:
            with conn:
                free = self._get_free(conn)
                if free is None:
                    raise ValueError("总池未初始化，请先 ptrade2 master-pool-init")
                if amount <= 0:
                    raise ValueError(f"分配金额必须 > 0")
                if amount > free:
                    raise ValueError(f"总池空闲不足：需 ¥{amount:,.0f}，空闲 ¥{free:,.0f}")
                total = conn.execute("SELECT total FROM pool_ledger WHERE id=1").fetchone()[0]
                if amount > 0.3 * total:
                    raise ValueError(f"单股分配超过总池 30%：¥{amount:,.0f} > 30%×{total:,.0f}")
                # 冷却检查（非 L1）
                strat, pool_code = self._get_strategy(conn, stock)
                if code is None:
                    code = pool_code
                if strat != 'L1':
                    cool = conn.execute(
                        "SELECT cooldown_until FROM position WHERE stock=? ORDER BY id DESC LIMIT 1",
                        (stock,)).fetchone()
                    if cool and cool[0] and datetime.now() < datetime.fromisoformat(cool[0]):
                        raise ValueError(f"{stock} 在冷却期内（至 {cool[0]}），禁止 allocate")
                    # 段位上限 8（非 L1）
                    open_count = conn.execute(
                        "SELECT COUNT(*) c FROM position WHERE status='open' AND strategy!='L1'"
                    ).fetchone()['c']
                    if open_count >= 8:
                        raise ValueError(f"持仓段已满（{open_count}/8），需先 release 再开新段")
                # 扣 free
                new_free = free - amount
                conn.execute("UPDATE pool_ledger SET free=?, updated_at=? WHERE id=1",
                             (new_free, datetime.now().isoformat()))
                # 建持仓段
                conn.execute("INSERT INTO position (stock, code, strategy, status, budget, "
                             "topup_total, opened_at) VALUES (?,?,?,'open',?,0,?)",
                             (stock, code, strat, amount, datetime.now().isoformat()))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'allocate', stock, amount, free,
                              new_free, reason, source))
            # 建账户（传 code 避免网络验证）— 必须在池事务提交后，避免同库写锁冲突
            from paper_trading_v2.trading import PaperTrader
            trader = PaperTrader()
            trader.init_account(stock_name=stock, capital=amount, stock_code=code)
            return True
        finally:
            conn.close()

    def topup(self, stock, amount, reason, source="agent"):
        """段内注资：从 free 拨差额进账户，同步加 total/available。"""
        conn = self._conn()
        try:
            with conn:
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
                conn.execute("UPDATE position SET budget=budget+?, topup_total=topup_total+? "
                             "WHERE id=?", (amount, amount, seg['id']))
                new_free = free - amount
                conn.execute("UPDATE pool_ledger SET free=?, updated_at=? WHERE id=1",
                             (new_free, datetime.now().isoformat()))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'topup', stock, amount, free,
                              new_free, reason, source))
            # 同步账户资金 — 事务提交后，避免同库写锁冲突
            from paper_trading_v2.trading import PaperTrader
            trader = PaperTrader()
            acct = trader.get_account(stock)
            if acct is None:
                raise ValueError(f"账户 {stock} 不存在")
            acct.capital_pool.total += amount
            acct.capital_pool.available += amount
            trader.storage.save_account(acct)
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
            strat = seg['strategy']
            if strat == 'L1' and source != 'manual':
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
            with conn:
                conn.execute("UPDATE pool_ledger SET free=?, updated_at=? WHERE id=1",
                             (new_free, datetime.now().isoformat()))
                realized = value - seg['budget']
                conn.execute("UPDATE position SET status='closed', closed_at=?, close_value=?, "
                             "realized_pnl=?, cooldown_until=? WHERE id=?",
                             (datetime.now().isoformat(), value, realized,
                              (datetime.now() + timedelta(days=7)).isoformat(), seg['id']))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'release', stock, value, free,
                              new_free, reason, source))
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
