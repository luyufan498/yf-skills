"""核心交易逻辑

提供买入、卖出等核心交易功能
"""

from typing import Optional
import os
from paper_trading_v2.models import (
    Account,
    CapitalPool,
    Position,
    Operation,
    OperationType,
    StockInfo,
)
from paper_trading_v2.storage import StorageBackend
from paper_trading_v2.price_fetcher import StockPriceFetcher
from paper_trading_v2.sleeve_slots import SEGMENT_TRANSFER_MARK


class PaperTrader:
    """模拟盘交易器"""

    def __init__(self, storage: Optional[StorageBackend] = None):
        """
        初始化交易器

        Args:
            storage: 存储后端，默认使用JSON存储
        """
        if storage is None:
            from paper_trading_v2.storage import StorageFactory
            self.storage = StorageFactory.create_storage("json")
        else:
            self.storage = storage

        self.price_fetcher = StockPriceFetcher()

    def init_account(
        self,
        stock_name: str,
        capital: float,
        stock_code: Optional[str] = None,
        force: bool = False
    ) -> Account:
        """
        初始化账户

        Args:
            stock_name: 股票名称
            capital: 初始资金
            stock_code: 股票代码（可选，如未提供会自动查询并验证股票名称）
            force: 是否强制重新初始化

        Returns:
            Account 对象

        Raises:
            ValueError: 如果账户已存在且未使用force参数，或股票名称验证失败
        """
        from paper_trading_v2.code_searcher import validate_stock_name

        # Validate stock name if code not provided
        if stock_code is None:
            is_valid, auto_code = validate_stock_name(stock_name)
            if not is_valid:
                raise ValueError(f"❌ 股票名称 '{stock_name}' 未能通过验证，请确保使用正确的股票名称")
            stock_code = auto_code

        existing = self.storage.load_account(stock_name)
        if existing:
            if force:
                self.storage.delete_account(stock_name)
            else:
                raise ValueError(f"账户 '{stock_name}' 已存在，现有资金：¥{existing.capital_pool.total:,.2f}")

        if stock_code is None:
            from paper_trading_v2.code_searcher import StockCodeSearcher
            searcher = StockCodeSearcher()
            results = searcher.search_cn_stocks(stock_name, limit=3)
            if results:
                stock_code = results[0].get('code', '')
            else:
                stock_code = None

        account = Account(
            stock_name=stock_name,
            stock_code=stock_code,
            capital_pool=CapitalPool(total=capital, available=capital, used=0.0)
        )

        self.storage.save_account(account)

        init_operation = Operation(
            type=OperationType.INIT,
            capital=capital,
            note="初始化资金池"
        )
        self.storage.save_operation(stock_name, init_operation)

        return account

    def get_account(self, stock_name: str) -> Optional[Account]:
        """
        获取账户信息

        Args:
            stock_name: 股票名称

        Returns:
            Account 对象，如果不存在返回None
        """
        account = self.storage.load_account(stock_name)
        if not account:
            return None

        _, remaining_cost = self.get_remaining_position(account)

        # 修复 used：与 FIFO 剩余持仓成本比对
        used_changed = abs(account.capital_pool.used - remaining_cost) > 1
        if used_changed:
            account.capital_pool.used = remaining_cost

        # M1.7/F1 幻影现金：迁移票（段内含段转随迁行）不得按全史现金流重建——
        # 迁移前已实现盈亏已随 sleeve 回款结算（sleeve_migrate.refund=现金+承接成本），
        # 随迁 buy/sell 行再进公式=盈利双算/亏损双扣（实测 ±40,000 传导 release 污染主池）。
        # 迁移票口径（与承接语义恒等，承接瞬间 =C−C+0=0）：
        #   available = 资金基线 − Σ(open FIFO cost) + Σ(迁移后卖出已实现盈亏)
        # 未标记行=迁移后真实现金流：买入已体现在 FIFO 成本里，卖出按价差计入。
        # v9 资金基线=段 budget（open 段）；closed 段=0（资金已随 release 结算回池，
        # 原子资产基线归零——沃森生物 −9,270.80 费用损失幽灵值即按此复现，不回写）。
        baseline = (account.capital_pool.total
                    if getattr(account, 'segment_status', 'open') == 'open' else 0.0)
        operations = self.storage.load_operations(stock_name)
        ops = operations.operations if operations else []
        if any((op.note or '').find(SEGMENT_TRANSFER_MARK) >= 0 for op in ops):
            realized_after = sum(
                (op.amount or 0) - (op.cost or 0) for op in ops
                if op.type == OperationType.SELL
                and (op.note or '').find(SEGMENT_TRANSFER_MARK) < 0)
            expected_available = (baseline - remaining_cost
                                  + realized_after)
        else:
            # 从操作记录重建 available（包含已实现盈利）
            expected_available = baseline
            for op in ops:
                if op.type == OperationType.BUY:
                    expected_available -= op.amount or 0
                elif op.type == OperationType.SELL:
                    expected_available += op.amount or 0

        avail_changed = abs(account.capital_pool.available - expected_available) > 1
        if avail_changed:
            account.capital_pool.available = expected_available

        if used_changed or avail_changed:
            self.storage.save_account(account)

        return account

    def buy_stock(
        self,
        stock_name: str,
        quantity: Optional[int] = None,
        amount: Optional[float] = None,
        note: str = ""
    ) -> Account:
        """
        买入股票

        Args:
            stock_name: 股票名称
            quantity: 买入股数（与amount二选一）
            amount: 买入金额（与quantity二选一）
            note: 备注

        Returns:
            更新后的Account对象

        Raises:
            ValueError: 如果参数错误、资金不足或账户不存在
        """
        account = self.get_account(stock_name)
        if not account:
            raise ValueError(f"账户 '{stock_name}' 不存在，请先初始化")

        if not account.stock_code:
            from paper_trading_v2.code_searcher import StockCodeSearcher
            searcher = StockCodeSearcher()
            results = searcher.search_cn_stocks(stock_name, limit=3)
            if results:
                account.stock_code = results[0].get('code', '')
            else:
                raise ValueError(f"无法获取股票代码")

        price_info = self.price_fetcher.get_realtime_price(account.stock_code)
        if not price_info or not price_info.current_price:
            raise ValueError("无法获取实时价格")

        current_price = price_info.current_price

        if quantity is not None:
            trade_qty = quantity
            trade_amount = trade_qty * current_price
        elif amount is not None:
            trade_amount = amount
            trade_qty = int(trade_amount / current_price)
            if trade_qty == 0:
                raise ValueError(f"金额 ¥{amount:,.2f} 不足以买入1股")
        else:
            raise ValueError("请指定 quantity 或 amount 参数")

        required = trade_qty * current_price
        if not account.capital_pool.withdraw(required):
            shortage = required - account.capital_pool.available
            raise ValueError(f"资金不足。需要：¥{required:,.2f}，可用：¥{account.capital_pool.available:,.2f}，缺口：¥{shortage:,.2f}")

        position = Position(
            stock_code=account.stock_code,
            quantity=trade_qty,
            price=current_price,
            total_cost=required,
            operation=OperationType.BUY,
            note=note
        )
        account.positions.append(position)

        # 初始化FIFO指针（首次买入）
        if account.fifo_index < 0:
            account.fifo_index = len(account.positions) - 1
            account.fifo_offset = 0

        self.storage.save_account(account)

        buy_operation = Operation(
            type=OperationType.BUY,
            price=current_price,
            quantity=trade_qty,
            amount=required,
            note=note
        )
        self.storage.save_operation(stock_name, buy_operation)

        # 同步条件：买入后更新成本保护（如果设置了auto_link_cost）
        self._sync_conditions_after_buy(stock_name, account)

        return account

    def sell_stock(
        self,
        stock_name: str,
        quantity: Optional[int] = None,
        sell_all: bool = False,
        note: str = ""
    ) -> Account:
        """
        卖出股票

        Args:
            stock_name: 股票名称
            quantity: 卖出股数
            sell_all: 是否全部卖出
            note: 备注

        Returns:
            更新后的Account对象

        Raises:
            ValueError: 如果参数错误、持仓不足或账户不存在
        """
        account = self.get_account(stock_name)
        if not account:
            raise ValueError(f"账户 '{stock_name}' 不存在")

        total_quantity, _ = self.get_remaining_position(account)

        if total_quantity == 0:
            raise ValueError("当前无持仓")

        if sell_all:
            trade_qty = total_quantity
        elif quantity is not None:
            trade_qty = quantity
        else:
            raise ValueError("请指定 quantity 或 sell_all=True")

        if trade_qty > total_quantity:
            raise ValueError(f"持仓不足。想卖：{trade_qty} 股，持仓：{total_quantity} 股")

        if not account.stock_code:
            raise ValueError("未找到股票代码")

        price_info = self.price_fetcher.get_realtime_price(account.stock_code)
        if not price_info or not price_info.current_price:
            raise ValueError("无法获取实时价格")

        current_price = price_info.current_price

        trade_amount = trade_qty * current_price

        cost_amount = self._consume_fifo(account, trade_qty)

        profit = trade_amount - cost_amount

        # 可用资金增加卖出所得，占用资金减少卖出部分的成本
        account.capital_pool.available += trade_amount
        account.capital_pool.used = max(0.0, account.capital_pool.used - cost_amount)

        sell_position = Position(
            stock_code=account.stock_code,
            quantity=trade_qty,
            price=current_price,
            total_cost=cost_amount,
            operation=OperationType.SELL,
            note=note
        )
        account.positions.append(sell_position)

        self.storage.save_account(account)

        sell_operation = Operation(
            type=OperationType.SELL,
            price=current_price,
            quantity=trade_qty,
            amount=trade_amount,
            cost=cost_amount,
            profit=profit,
            note=note
        )
        self.storage.save_operation(stock_name, sell_operation)

        # v9 段现金恒等式 runtime 维护：段已实现盈亏随每笔卖出同步落段列
        # （v8 时代 realized_pnl 只在 release 落值，open 段恒 0 → 恒等式破缺）
        bump = getattr(self.storage, 'bump_segment_realized', None)
        if bump is not None:
            try:
                bump(stock_name, profit)
            except Exception:
                pass

        # 同步条件：卖出后更新成本保护或暂停条件
        self._sync_conditions_after_sell(stock_name, account)

        # 2026-08-27 修复：清仓后自动 release（position 标 closed + 资金回 free + 档位降 L2）
        # 下沉到 CLI 层——避免 agent 漏调 master-pool-release（中芯 8/27 事故根因）
        # R5：传入卖出账户的组（resolve_account 段锚定产物），双组并存时锁定本侧路由
        remaining_qty, _ = self.get_remaining_position(account)
        if remaining_qty == 0:
            self._auto_release_on_clear(stock_name, grp=getattr(account, 'grp', None))

        return account

    def _auto_release_on_clear(self, stock_name: str, grp: Optional[str] = None):
        """清仓后自动释放空仓段（R5/B1/F6 去异常化：显式查段路由，禁 try/release 探测身份）。

        路由键 = 该票 open 段 strategy（显式查询，异常只兜执行故障不兜路由判断）：
        - NEWS 段 → 消息组 sleeve 结算（资金回 sleeve_ledger + 槽对账，资金路由不受 flag 影响）
        - 非 NEWS 段（L1/L2/人工）→ 主池 release；人工段豁免（纪律 8.3，提示手动）
        - 无 open 段 → no-op（卖出已完成）
        grp（卖出账户的组，sell_stock 传入）：同票双组并存时锁定本侧段，防误路由另一组；
        未传（None）时按任务书原义以 open 段 strategy 为路由键（最新段）。
        归档语义挂 SLEEVE_ARCHIVE_ON_CLEAR=1 flag 后面（flag 关=默认，旧行为原样）；
        迁移票（event_key→migrated 槽）清仓在 master_pool.release 侧恒 archived（2.6b v4.2 行）。
        """
        archive_flag = os.environ.get('SLEEVE_ARCHIVE_ON_CLEAR', '') == '1'
        try:
            from paper_trading_v2.master_pool import MasterPoolManager
            m = MasterPoolManager()
            import sqlite3
            conn = sqlite3.connect(m.db_path)
            conn.row_factory = sqlite3.Row
            try:
                if grp == 'news':
                    seg = conn.execute(
                        "SELECT strategy FROM position WHERE stock=? AND status='open' "
                        "AND strategy='NEWS' ORDER BY id DESC LIMIT 1",
                        (stock_name,)).fetchone()
                elif grp == 'tech':
                    seg = conn.execute(
                        "SELECT strategy FROM position WHERE stock=? AND status='open' "
                        "AND COALESCE(strategy,'')!='NEWS' ORDER BY id DESC LIMIT 1",
                        (stock_name,)).fetchone()
                else:
                    seg = conn.execute(
                        "SELECT strategy FROM position WHERE stock=? AND status='open' "
                        "ORDER BY id DESC LIMIT 1", (stock_name,)).fetchone()
                if seg is None:
                    return                     # 无 open 段 → no-op（卖出已完成）
                if (seg['strategy'] or '') == 'NEWS':
                    m.release(stock_name, reason="清仓自动释放（CLI 层，消息组成员）",
                              source="sleeve", pool='sleeve', archive=archive_flag)
                    print(f"[auto-release] {stock_name} 消息组成员清仓，资金已回消息池"
                          + ("，池行/槽已档案化" if archive_flag else
                             "（SLEEVE_ARCHIVE_ON_CLEAR=0：池/槽档案化标记未启用，建议开启 flag）"))
                    return
                # 技术组路径：人工段豁免（pool.strategy 含 manual 标记 → 跳过自动 release）
                pool = conn.execute(
                    "SELECT strategy FROM pool WHERE stock=?", (stock_name,)).fetchone()
                if pool is not None and "manual" in str(pool["strategy"] or ""):
                    print(f"[auto-release] {stock_name} 人工 L1 段——跳过自动 release，需人工确认释放")
                    return
                m.release(stock_name, reason="清仓自动释放（CLI 层）", archive=archive_flag)
                if archive_flag:
                    print(f"[auto-release] {stock_name} 清仓完成，空仓段已释放（archived 终态，不降档）")
                else:
                    print(f"[auto-release] {stock_name} 清仓完成，空仓段已自动释放（降回 L2）")
            finally:
                conn.close()
        except ValueError:
            pass  # release 侧拒绝（如仍持仓）——卖出已完成，不阻断
        except Exception as e:
            print(f"[auto-release] {stock_name} 自动释放失败（不影响卖出结果）: {e}")

    def _ensure_fifo_pointer(self, account: Account):
        """
        确保FIFO指针已初始化，指向第一个未完全卖出的BUY position
        """
        if account.fifo_index >= 0:
            return

        for i, pos in enumerate(account.positions):
            if pos.operation == OperationType.BUY:
                account.fifo_index = i
                account.fifo_offset = 0
                return

    def _consume_fifo(self, account: Account, quantity: int) -> float:
        """
        按FIFO消耗持仓，返回成本，并更新account的fifo指针

        支持除权除息（exright_bonus / exright_dividend）的前复权计算
        buy_queue 存储: [数量, 每股成本]
        """
        from collections import deque
        buy_queue = deque()

        for pos in account.positions:
            if pos.operation == OperationType.BUY:
                cost_per_share = pos.total_cost / pos.quantity if pos.quantity > 0 else 0
                buy_queue.append([float(pos.quantity), cost_per_share])
            elif pos.operation == OperationType.SELL:
                qty = pos.quantity
                while qty > 0 and buy_queue:
                    if buy_queue[0][0] <= qty:
                        qty -= buy_queue[0][0]
                        buy_queue.popleft()
                    else:
                        buy_queue[0][0] -= qty
                        qty = 0
            elif pos.operation == OperationType.EXRIGHT_BONUS:
                if buy_queue:
                    split_ratio = 1 + (pos.quantity / sum(q[0] for q in buy_queue))
                    for item in buy_queue:
                        item[0] *= split_ratio
                        item[1] /= split_ratio
            elif pos.operation == OperationType.EXRIGHT_DIVIDEND:
                if buy_queue:
                    total_dividend = abs(pos.total_cost)
                    total_qty = sum(q[0] for q in buy_queue)
                    if total_qty > 0:
                        dividend_per_share = total_dividend / total_qty
                        for item in buy_queue:
                            item[1] -= dividend_per_share

        # 从队列中消耗 quantity
        cost_amount = 0.0
        remaining = quantity
        while remaining > 0 and buy_queue:
            if buy_queue[0][0] <= remaining:
                cost_amount += buy_queue[0][0] * buy_queue[0][1]
                remaining -= buy_queue[0][0]
                buy_queue.popleft()
            else:
                cost_amount += remaining * buy_queue[0][1]
                buy_queue[0][0] -= remaining
                remaining = 0

        # 更新 fifo 指针：找到第一个未完全消耗的 BUY position
        account.fifo_index = -1
        account.fifo_offset = 0
        for i, pos in enumerate(account.positions):
            if pos.operation != OperationType.BUY:
                continue
            if buy_queue:
                if pos.quantity == buy_queue[0][0]:
                    account.fifo_index = i
                    break
                elif buy_queue[0][0] < pos.quantity:
                    account.fifo_index = i
                    account.fifo_offset = pos.quantity - buy_queue[0][0]
                    break
                buy_queue.popleft()

        return cost_amount

    def get_realized_profit_from_positions(self, account: Account) -> float:
        """从 positions 按 FIFO 重新计算已实现盈亏（感知除权除息）"""
        from collections import deque
        buy_queue = deque()
        realized = 0.0
        for pos in account.positions:
            if pos.operation == OperationType.BUY:
                cost_per_share = pos.total_cost / pos.quantity if pos.quantity > 0 else 0
                buy_queue.append([float(pos.quantity), cost_per_share])
            elif pos.operation == OperationType.SELL:
                qty = pos.quantity
                cost = 0.0
                while qty > 0 and buy_queue:
                    if buy_queue[0][0] <= qty:
                        cost += buy_queue[0][0] * buy_queue[0][1]
                        qty -= buy_queue[0][0]
                        buy_queue.popleft()
                    else:
                        cost += qty * buy_queue[0][1]
                        buy_queue[0][0] -= qty
                        qty = 0
                realized += (pos.quantity * pos.price) - cost
            elif pos.operation == OperationType.EXRIGHT_BONUS:
                if buy_queue:
                    split_ratio = 1 + (pos.quantity / sum(q[0] for q in buy_queue))
                    for item in buy_queue:
                        item[0] *= split_ratio
                        item[1] /= split_ratio
            elif pos.operation == OperationType.EXRIGHT_DIVIDEND:
                if buy_queue:
                    total_dividend = abs(pos.total_cost)
                    total_qty = sum(q[0] for q in buy_queue)
                    if total_qty > 0:
                        dividend_per_share = total_dividend / total_qty
                        for item in buy_queue:
                            item[1] -= dividend_per_share
        return realized

    def fix_operations(self, stock_name: str) -> dict:
        """
        根据 FIFO 重新修正 SELL operation 的 cost 和 profit（修复旧 Bug 数据污染）

        Returns:
            {"fixed": 修正笔数, "total_sell": SELL 总笔数}
        """
        account = self.get_account(stock_name)
        if not account:
            raise ValueError(f"账户 '{stock_name}' 不存在")

        ops_data = self.storage.load_operations(stock_name)
        if not ops_data:
            raise ValueError(f"操作记录 '{stock_name}' 不存在")

        from collections import deque
        fifo_queue = deque()

        sell_ops = [(i, o) for i, o in enumerate(ops_data.operations) if o.type == OperationType.SELL]
        sell_pos = [(i, p) for i, p in enumerate(account.positions) if p.operation == OperationType.SELL]

        if len(sell_ops) != len(sell_pos):
            raise ValueError(
                f"SELL 记录不一致: operations 中有 {len(sell_ops)} 笔 SELL，"
                f"positions 中有 {len(sell_pos)} 笔 SELL，无法自动修复"
            )

        fixed = 0
        sell_idx = 0

        for pos in account.positions:
            if pos.operation == OperationType.BUY:
                cost_per_share = pos.total_cost / pos.quantity if pos.quantity > 0 else 0
                fifo_queue.append([float(pos.quantity), cost_per_share])
            elif pos.operation == OperationType.SELL:
                qty = pos.quantity
                cost = 0.0
                while qty > 0 and fifo_queue:
                    if fifo_queue[0][0] <= qty:
                        cost += fifo_queue[0][0] * fifo_queue[0][1]
                        qty -= fifo_queue[0][0]
                        fifo_queue.popleft()
                    else:
                        cost += qty * fifo_queue[0][1]
                        fifo_queue[0][0] -= qty
                        qty = 0

                profit = (pos.quantity * pos.price) - cost
                op = ops_data.operations[sell_ops[sell_idx][0]]

                old_cost = op.cost
                old_profit = op.profit

                if (old_cost is None or abs(old_cost - cost) > 1) or \
                   (old_profit is None or abs(old_profit - profit) > 1):
                    pos.total_cost = round(cost, 2)
                    op.cost = round(cost, 2)
                    op.profit = round(profit, 2)
                    fixed += 1

                sell_idx += 1
            elif pos.operation == OperationType.EXRIGHT_BONUS:
                if fifo_queue:
                    split_ratio = 1 + (pos.quantity / sum(q[0] for q in fifo_queue))
                    for item in fifo_queue:
                        item[0] *= split_ratio
                        item[1] /= split_ratio
            elif pos.operation == OperationType.EXRIGHT_DIVIDEND:
                if fifo_queue:
                    total_dividend = abs(pos.total_cost)
                    total_qty = sum(q[0] for q in fifo_queue)
                    if total_qty > 0:
                        dividend_per_share = total_dividend / total_qty
                        for item in fifo_queue:
                            item[1] -= dividend_per_share

        self.storage.save_account(account)
        self.storage.save_operations(stock_name, ops_data)

        return {"fixed": fixed, "total_sell": len(sell_ops)}

    def get_remaining_position(self, account: Account) -> tuple[int, float]:
        """
        获取当前剩余持仓数量和成本（感知除权除息）

        buy_queue 存储: [数量, 每股成本]
        """
        from collections import deque
        buy_queue = deque()

        for pos in account.positions:
            if pos.operation == OperationType.BUY:
                cost_per_share = pos.total_cost / pos.quantity if pos.quantity > 0 else 0
                buy_queue.append([float(pos.quantity), cost_per_share])
            elif pos.operation == OperationType.SELL:
                qty = pos.quantity
                while qty > 0 and buy_queue:
                    if buy_queue[0][0] <= qty:
                        qty -= buy_queue[0][0]
                        buy_queue.popleft()
                    else:
                        buy_queue[0][0] -= qty
                        qty = 0
            elif pos.operation == OperationType.EXRIGHT_BONUS:
                if buy_queue:
                    split_ratio = 1 + (pos.quantity / sum(q[0] for q in buy_queue))
                    for item in buy_queue:
                        item[0] *= split_ratio
                        item[1] /= split_ratio
            elif pos.operation == OperationType.EXRIGHT_DIVIDEND:
                if buy_queue:
                    total_dividend = abs(pos.total_cost)
                    total_qty = sum(q[0] for q in buy_queue)
                    if total_qty > 0:
                        dividend_per_share = total_dividend / total_qty
                        for item in buy_queue:
                            item[1] -= dividend_per_share

        total_quantity = int(sum(q[0] for q in buy_queue))
        total_cost = max(0.0, sum(q[0] * q[1] for q in buy_queue))

        # 清仓保护
        if total_quantity == 0:
            total_cost = 0.0

        # 更新 fifo 指针
        account.fifo_index = -1
        account.fifo_offset = 0
        for i, pos in enumerate(account.positions):
            if pos.operation != OperationType.BUY:
                continue
            if buy_queue:
                if pos.quantity == buy_queue[0][0]:
                    account.fifo_index = i
                    break
                elif buy_queue[0][0] < pos.quantity:
                    account.fifo_index = i
                    account.fifo_offset = pos.quantity - buy_queue[0][0]
                    break
                buy_queue.popleft()

        return total_quantity, total_cost

    def _sync_conditions_after_buy(self, stock_name: str, account: Account):
        """
        买入后同步条件：
        - 更新成本保护（如果 auto_link_cost=True）
        - 如果之前是空仓（条件暂停），恢复条件
        """
        try:
            from paper_trading_v2.conditions_manager import ConditionsManager
            cond_mgr = ConditionsManager(self.storage)

            total_qty, total_cost = self.get_remaining_position(account)
            if total_qty > 0:
                avg_cost = total_cost / total_qty
                cond_mgr.sync_cost_protection(stock_name, avg_cost)
                cond_mgr.resume_all(stock_name)
        except Exception:
            pass

    def _sync_conditions_after_sell(self, stock_name: str, account: Account):
        """
        卖出后同步条件：
        - 更新成本保护（如果 auto_link_cost=True）
        - 如果全部清仓，暂停所有硬条件
        """
        try:
            from paper_trading_v2.conditions_manager import ConditionsManager
            cond_mgr = ConditionsManager(self.storage)

            total_qty, total_cost = self.get_remaining_position(account)
            if total_qty > 0:
                avg_cost = total_cost / total_qty
                cond_mgr.sync_cost_protection(stock_name, avg_cost)
            else:
                # 全部清仓，暂停硬条件
                cond_mgr.suspend_all(stock_name)
        except Exception:
            pass
