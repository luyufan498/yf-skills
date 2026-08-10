"""SqlStorage — SQLite 深迁移存储（接口与 JsonStorage 一致）"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from paper_trading_v2.models import (
    Account, AccountHistory, Operation, Position, ExRightAppliedRecord, CapitalPool,
)
from paper_trading_v2.db import get_connection, migrate_db


class StorageBackend:
    """存储后端抽象（兼容保留）"""
    pass


class SqlStorage(StorageBackend):
    """SQLite 规范化表存储。水合：表 → 内存模型，领域逻辑照常。"""

    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection(self.db_path)
        migrate_db(conn)
        conn.close()

    def _conn(self):
        return get_connection(self.db_path)

    def _account_id(self, conn, stock_name: str) -> Optional[int]:
        row = conn.execute("SELECT id FROM accounts WHERE stock_name=?", (stock_name,)).fetchone()
        return row[0] if row else None

    # ---- Account ----
    def load_account(self, stock_name: str) -> Optional[Account]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM accounts WHERE stock_name=?", (stock_name,)).fetchone()
            if not row:
                return None
            positions = self._load_positions(conn, row['id'])
            exright = self._load_exright(conn, row['id'])
            return Account(
                stock_name=row['stock_name'],
                stock_code=row['stock_code'],
                capital_pool=CapitalPool(
                    total=row['capital_total'],
                    available=row['capital_available'],
                    used=row['capital_used'],
                ),
                positions=positions,
                fifo_index=row['fifo_index'],
                fifo_offset=row['fifo_offset'],
                exright_applied=exright,
                created_at=row['created_at'],
                updated_at=row['updated_at'],
            )
        finally:
            conn.close()

    def _load_positions(self, conn, account_id: int) -> List[Position]:
        rows = conn.execute("SELECT * FROM positions WHERE account_id=? ORDER BY seq", (account_id,)).fetchall()
        return [Position(
            stock_code=r['stock_code'] or '', quantity=r['quantity'] or 0,
            price=r['price'] or 0.0, total_cost=r['total_cost'] or 0.0,
            operation=r['operation'], timestamp=r['timestamp'] or '', note=r['note'] or '',
        ) for r in rows]

    def _load_exright(self, conn, account_id: int) -> List[ExRightAppliedRecord]:
        rows = conn.execute("SELECT * FROM exright_applied WHERE account_id=? ORDER BY seq", (account_id,)).fetchall()
        return [ExRightAppliedRecord(
            cqr=r['cqr'] or '', fhcontent=r['fhcontent'] or '',
            applied_at=r['applied_at'] or '', reason=r['reason'] or '',
            migrated=bool(r['migrated']),
        ) for r in rows]

    def save_account(self, account: Account) -> Path:
        account.updated_at = datetime.now().isoformat()
        conn = self._conn()
        try:
            existing = self._account_id(conn, account.stock_name)
            with conn:
                if existing is None:
                    cur = conn.execute(
                        "INSERT INTO accounts (stock_name, stock_code, capital_total, "
                        "capital_available, capital_used, fifo_index, fifo_offset, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (account.stock_name, account.stock_code,
                         account.capital_pool.total, account.capital_pool.available,
                         account.capital_pool.used, account.fifo_index, account.fifo_offset,
                         account.created_at, account.updated_at))
                    account_id = cur.lastrowid
                else:
                    account_id = existing
                    conn.execute(
                        "UPDATE accounts SET stock_code=?, capital_total=?, "
                        "capital_available=?, capital_used=?, fifo_index=?, fifo_offset=?, "
                        "updated_at=? WHERE id=?",
                        (account.stock_code, account.capital_pool.total,
                         account.capital_pool.available, account.capital_pool.used,
                         account.fifo_index, account.fifo_offset,
                         account.updated_at, account_id))
                conn.execute("DELETE FROM positions WHERE account_id=?", (account_id,))
                for i, pos in enumerate(account.positions):
                    conn.execute(
                        "INSERT INTO positions (account_id, seq, operation, stock_code, "
                        "quantity, price, total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?)",
                        (account_id, i, pos.operation, pos.stock_code, pos.quantity,
                         pos.price, pos.total_cost, pos.timestamp, pos.note))
                conn.execute("DELETE FROM exright_applied WHERE account_id=?", (account_id,))
                for i, ex in enumerate(account.exright_applied):
                    conn.execute(
                        "INSERT INTO exright_applied (account_id, seq, cqr, fhcontent, "
                        "applied_at, reason, migrated) VALUES (?,?,?,?,?,?,?)",
                        (account_id, i, ex.cqr, ex.fhcontent, ex.applied_at,
                         ex.reason, int(ex.migrated)))
            return Path(self.db_path)
        finally:
            conn.close()

    # ---- Operations ----
    def load_operations(self, stock_name: str) -> Optional[AccountHistory]:
        conn = self._conn()
        try:
            account_id = self._account_id(conn, stock_name)
            if account_id is None:
                return None
            rows = conn.execute("SELECT * FROM operations WHERE account_id=? ORDER BY seq", (account_id,)).fetchall()
            ops = [Operation(
                type=r['type'], price=r['price'], quantity=r['quantity'],
                amount=r['amount'], cost=r['cost'], profit=r['profit'],
                capital=r['capital'], timestamp=r['timestamp'] or '', note=r['note'] or '',
            ) for r in rows]
            return AccountHistory(
                stock_name=stock_name,
                created_at=rows[0]['timestamp'] if rows else datetime.now().isoformat(),
                operations=ops,
            )
        finally:
            conn.close()

    def save_operations(self, stock_name: str, operations: AccountHistory) -> Path:
        conn = self._conn()
        try:
            account_id = self._account_id(conn, stock_name)
            if account_id is None:
                raise ValueError(f"账户 '{stock_name}' 不存在，请先初始化")
            with conn:
                conn.execute("DELETE FROM operations WHERE account_id=?", (account_id,))
                for i, op in enumerate(operations.operations):
                    conn.execute(
                        "INSERT INTO operations (account_id, seq, type, price, quantity, "
                        "amount, cost, profit, capital, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (account_id, i, op.type, op.price, op.quantity, op.amount,
                         op.cost, op.profit, op.capital, op.timestamp or '', op.note or ''))
            return Path(self.db_path)
        finally:
            conn.close()

    def save_operation(self, stock_name: str, operation: Operation) -> Path:
        ops = self.load_operations(stock_name)
        if ops is None:
            ops = AccountHistory(stock_name=stock_name)
        ops.operations.append(operation)
        return self.save_operations(stock_name, ops)

    # ---- 账户列表 / 删除 ----
    def list_accounts(self) -> List[str]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT stock_name FROM accounts ORDER BY id").fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def delete_account(self, stock_name: str) -> bool:
        conn = self._conn()
        try:
            account_id = self._account_id(conn, stock_name)
            if account_id is None:
                return False
            with conn:
                conn.execute("DELETE FROM positions WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM operations WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM condition_history WHERE condition_id IN "
                             "(SELECT id FROM conditions WHERE account_id=?)", (account_id,))
                conn.execute("DELETE FROM conditions WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM exright_applied WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            return True
        finally:
            conn.close()


# ---- 兼容别名：vendored 领域层 import 这些符号 ----
JsonStorage = SqlStorage


class StorageFactory:
    @staticmethod
    def create_storage(backend="json", **kwargs):
        return SqlStorage(**kwargs)
