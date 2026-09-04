"""SqlStorage — SQLite 深迁移存储（接口与 JsonStorage 一致）

v9（M1.6 账户层退役）：段即账户。名字寻址终点=position 段行（不再是 accounts 行）；
accounts 表退役为 accounts_old，其 cash/FIFO 状态由 position 段列承载。
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from paper_trading_v2.models import (
    Account, AccountHistory, Operation, Position, ExRightAppliedRecord, CapitalPool,
)
from paper_trading_v2.db import get_connection, migrate_db, grp_of_strategy


class StorageBackend:
    """存储后端抽象（兼容保留）"""
    pass


def resolve_account(conn, stock_name: str, prefer_grp: Optional[str] = None):
    """名字寻址终局（v9/U3）：名字 → open 段 → 段即账户。

    返回 position 段行（sqlite3.Row）或 None；`row['id']` 即"account_id"
    （trades/operations/conditions/exright_applied 的 join 键，v9 语义=段 id）。
    组由段 strategy 推导（strategy='NEWS' ⟺ news，其余 ⟺ tech，U2 不设 grp 列）。
    规则（确定性，零歧义，与 v8 双壳消歧行为等价）：
      1. 该名仅一个段 → 直接返回（单段票行为不变）
      2. prefer_grp 指定组（sell_stock 锁定本侧）→ 该组 open 段最高优先
      3. 有非 NEWS 的 open 段 → 技术组 L1 段锚定（迁移票主寻址，方案 2.3 v4.2）
      4. 有 NEWS open 段 → sleeve 成员段
      5. 无 open 段（清仓/历史票）：FIFO 剩余持仓>0 的段优先，并列取非 NEWS（tech 优先，
         迁移后 tech 是活跃承接方）；仍无 → 最后关闭段（历史可读，info/operations 查询用）
    """
    segs = conn.execute("SELECT * FROM position WHERE stock=? ORDER BY id",
                        (stock_name,)).fetchall()
    if not segs:
        return None
    if len(segs) == 1:
        return segs[0]
    if prefer_grp:
        for s in segs:
            if s['status'] == 'open' and grp_of_strategy(s['strategy']) == prefer_grp:
                return s
    for s in reversed(segs):                       # 最新段优先
        if s['status'] == 'open' and (s['strategy'] or '') != 'NEWS':
            return s
    for s in segs:
        if s['status'] == 'open':
            return s
    # 无 open 段：FIFO 剩余持仓>0 的段优先（与 v8 双壳规则等价），并列取非 NEWS
    def _qty(seg_id):
        rows = conn.execute("SELECT operation, quantity FROM trades WHERE account_id=? "
                            "ORDER BY seq", (seg_id,)).fetchall()
        live = 0
        for r in rows:
            q = r['quantity'] or 0
            live += q if (r['operation'] or '') == 'buy' else \
                (-q if (r['operation'] or '') == 'sell' else 0)
        return live
    best, best_qty = None, 0
    for s in segs:
        q = _qty(s['id'])
        if q > best_qty or (q == best_qty and q > 0 and best is not None
                            and (s['strategy'] or '') != 'NEWS'):
            best, best_qty = s, q
    if best is not None and best_qty > 0:
        return best
    return segs[-1]                                # 最后关闭段（历史查询兜底）


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

    def _get_account_dir(self, stock_name: str) -> Path:
        """[v10 债标注 2026-09-04] 兼容 shim：conditions_manager 文件模式写条件——
        仅 migrate_existing（旧文件数据迁移）依赖；运行时保护链已全 SQL 化（v12 E6
        读 conditions 表），新代码勿走文件 CM。"""
        from paper_trading_v2.config import get_workspace_config
        return get_workspace_config()['tradings_dir'] / stock_name

    def _account_id(self, conn, stock_name: str) -> Optional[int]:
        """名字寻址唯一入口：经 resolve_account 段锚定（v9 段即账户，全链路复用）。"""
        row = resolve_account(conn, stock_name)
        return row['id'] if row else None

    # ---- Account ----
    def load_account(self, stock_name: str) -> Optional[Account]:
        """段 → Account 内存模型（v9 段即账户）。

        capital_pool：total=段 budget（资金标签）、available=段 cash（段现金列，U2）、
        used=FIFO 占用成本（从 trades 现算，段表不存冗余列）。
        """
        conn = self._conn()
        try:
            # 名字寻址统一走 resolve_account 段锚定——迁移票同票双段并存时命中
            # 非 NEWS open 段（tech 侧），不得命中已结算的 news/关闭段
            row = resolve_account(conn, stock_name)
            if not row:
                return None
            positions = self._load_positions(conn, row['id'])
            exright = self._load_exright(conn, row['id'])
            used = 0.0
            for pos in positions:
                if pos.operation == 'buy':
                    used += pos.total_cost or 0.0
                elif pos.operation == 'sell':
                    used -= pos.total_cost or 0.0
            used = max(0.0, used)
            return Account(
                stock_name=row['stock'],
                stock_code=row['code'],
                capital_pool=CapitalPool(
                    total=row['budget'] or 0.0,
                    available=row['cash'] or 0.0,
                    used=used,
                ),
                positions=positions,
                fifo_index=row['fifo_index'] if row['fifo_index'] is not None else -1,
                fifo_offset=row['fifo_offset'] or 0.0,
                exright_applied=exright,
                grp=grp_of_strategy(row['strategy']),
                segment_status=row['status'],
                created_at=row['opened_at'],
                updated_at=row['closed_at'] or row['opened_at'],
            )
        finally:
            conn.close()

    def _load_positions(self, conn, account_id: int) -> List[Position]:
        rows = conn.execute("SELECT * FROM trades WHERE account_id=? ORDER BY seq", (account_id,)).fetchall()
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
        """段直写（v9）：现金/FIFO 状态落 position 段列，FIFO 流水落 trades。

        无段可寻址时建 manual L1 open 段（段直建，U5 fixtures 同款；等价 v8 的
        save_account 兜底建户语义——资金标签=capital_pool.total）。
        """
        account.updated_at = datetime.now().isoformat()
        conn = self._conn()
        try:
            with conn:
                row = resolve_account(conn, account.stock_name)
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO position (stock, code, strategy, status, budget, "
                        "topup_total, opened_at, cash, fifo_index, fifo_offset) "
                        "VALUES (?,?,'L1','open',?,0,?,?,?,?)",
                        (account.stock_name, account.stock_code,
                         account.capital_pool.total, datetime.now().isoformat(),
                         account.capital_pool.available, account.fifo_index,
                         account.fifo_offset))
                    account_id = cur.lastrowid
                else:
                    account_id = row['id']
                    if row['status'] == 'open':
                        conn.execute(
                            "UPDATE position SET cash=?, fifo_index=?, fifo_offset=?, "
                            "code=COALESCE(?, code) WHERE id=?",
                            (account.capital_pool.available, account.fifo_index,
                             account.fifo_offset, account.stock_code, account_id))
                    else:
                        # 关闭段只读：现金/FIFO 已随 release 结算，不回写（防幽灵复活）
                        pass
                conn.execute("DELETE FROM trades WHERE account_id=?", (account_id,))
                for i, pos in enumerate(account.positions):
                    conn.execute(
                        "INSERT INTO trades (account_id, seq, operation, stock_code, "
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

    def has_v11_flows(self, stock_name: str) -> bool:
        """v11 原生段探测（buy/sell 路径据此决定 grant/return 接线，与
        master_pool._is_v11_native 同判据）：段列 source 非空（v11 allocate 建段或
        pool_publicize 盖章）或该票已有 v11 资金流水。

        ⚠ 移交桥承接段（[段转随迁] 标记）刻意**不**在此列：其 available 由标记重建
        公式（baseline−FIFO+迁移后已实现）维护，grant/return 接线会与现金流重建双重
        记账（m17 F1 幻影现金防线）。桥票 topup/sell 走旧物理路径直到 release——
        桥是兼容层活路径，语义以 v9 重建式为准。"""
        conn = self._conn()
        try:
            row = resolve_account(conn, stock_name)
            if row is None:
                return False
            if ('source' in row.keys() and (row['source'] or '')):
                return True
            return conn.execute(
                "SELECT 1 FROM audit WHERE stock=? AND action IN "
                "('pool_grant','pool_return','v11_publicize') LIMIT 1",
                (stock_name,)).fetchone() is not None
        finally:
            conn.close()

    def bump_segment_realized(self, stock_name: str, profit_delta: float) -> None:
        """段已实现盈亏增量落列（v9 段现金恒等式 runtime 维护）。

        sell_stock 每笔卖出后调用：realized_pnl += profit。恒等式
        cash + FIFO成本 − realized == budget 依赖该列与段现金同步
        （release 时按 value−budget 重写，FIFO=0 时两者恒等，无双计）。
        """
        conn = self._conn()
        try:
            with conn:
                row = resolve_account(conn, stock_name)
                if row is None:
                    return
                conn.execute("UPDATE position SET realized_pnl=COALESCE(realized_pnl,0)+? "
                             "WHERE id=?", (profit_delta, row['id']))
        finally:
            conn.close()

    # ---- 段列表 / 删除（v9 段视角）----
    def list_accounts(self, include_closed: bool = False) -> List[str]:
        """段视角列表（U4）：默认=open 段（活跃持仓实体）；include_closed=True 含关闭段。

        （v8 语义=accounts 全列表；v9 段即账户，死壳段不再混入活跃列表，
        但仍可按名查询：info <股> 经 resolve_account 落到其最后关闭段。）
        """
        conn = self._conn()
        try:
            q = "SELECT stock FROM position"
            if not include_closed:
                q += " WHERE status='open'"
            q += " ORDER BY id"
            seen, out = set(), []
            for r in conn.execute(q):
                if r[0] not in seen:
                    seen.add(r[0])
                    out.append(r[0])
            return out
        finally:
            conn.close()

    def delete_account(self, stock_name: str) -> bool:
        """删除段及其全部子行（v9：段即账户，删段=删户；v8 同语义换锚点）。"""
        conn = self._conn()
        try:
            account_id = self._account_id(conn, stock_name)
            if account_id is None:
                return False
            with conn:
                conn.execute("DELETE FROM trades WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM operations WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM condition_history WHERE condition_id IN "
                             "(SELECT id FROM conditions WHERE account_id=?)", (account_id,))
                conn.execute("DELETE FROM conditions WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM exright_applied WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM position WHERE id=?", (account_id,))
            return True
        finally:
            conn.close()


# ---- 兼容别名：vendored 领域层 import 这些符号 ----
JsonStorage = SqlStorage


class StorageFactory:
    @staticmethod
    def create_storage(backend="json", **kwargs):
        return SqlStorage(**kwargs)
