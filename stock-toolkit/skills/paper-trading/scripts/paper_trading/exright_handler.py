"""除权除息检测与注入引擎

自动检测除权事件，注入虚拟 position，同步成本保护
"""

from collections import deque
from datetime import datetime
from typing import Tuple, Optional

from paper_trading.models import (
    Account,
    Position,
    OperationType,
    ExRightAppliedRecord,
)
from paper_trading.exright_cache import ExRightCache


class ExRightHandler:
    """除权除息检测与注入引擎"""

    def __init__(self, trader, cache: ExRightCache = None):
        self.trader = trader
        self.cache = cache or ExRightCache()
        self._processing = set()

    def check_and_apply(self, stock_name: str, account: Account, force: bool = False) -> Tuple[bool, str]:
        """
        检查并应用除权，返回 (是否发生变更, 消息)
        """
        # 1. 防重入检查
        key = f"{stock_name}_{account.stock_code}"
        if key in self._processing:
            return False, "正在处理中，跳过"
        self._processing.add(key)

        try:
            if not account.stock_code:
                return False, "股票代码为空，无法检测除权"

            # 2. 首次迁移
            if not account.exright_applied:
                self._migrate_account(account)

            # 3. 获取除权事件
            events = self.cache.get_events(account.stock_code)
            if not events:
                return False, "无除权事件"

            # 4. 过滤已应用
            applied_cqrs = {r.cqr for r in account.exright_applied}
            unapplied = [e for e in events if e['cqr'] not in applied_cqrs]
            if not unapplied:
                return False, "所有除权已处理"

            changed = False
            messages = []

            for event in unapplied:
                cqr = event['cqr']
                djr = event.get('djr', '')
                fhcontent = event.get('fhcontent', '')
                bonus_per_10 = event.get('bonus_per_10', 0.0)
                split_per_10 = event.get('split_per_10', 0.0)

                # 5. 检查登记日是否持仓，同时获取登记日持仓数量
                djr_qty = self._get_position_qty_at_date(account, djr)
                if djr_qty <= 0:
                    account.exright_applied.append(ExRightAppliedRecord(
                        cqr=cqr,
                        fhcontent=fhcontent,
                        reason="登记日未持仓，跳过",
                    ))
                    self.trader.storage.save_account(account)
                    continue

                # 7. 计算送转股和分红（基于登记日持仓）
                bonus_shares = int(djr_qty * split_per_10 / 10)
                dividend = djr_qty * (bonus_per_10 / 10)

                # 8. 注入虚拟 position（按 timestamp 排序插入）
                timestamp = f"{cqr}T09:30:00"
                exright_positions = []
                if bonus_shares > 0:
                    exright_positions.append(Position(
                        stock_code=account.stock_code,
                        quantity=bonus_shares,
                        price=0.0,
                        total_cost=0.0,
                        operation=OperationType.EXRIGHT_BONUS,
                        timestamp=timestamp,
                        note=f"除权送转: {fhcontent}",
                    ))

                if dividend > 0:
                    exright_positions.append(Position(
                        stock_code=account.stock_code,
                        quantity=0,
                        price=0.0,
                        total_cost=-round(dividend, 2),
                        operation=OperationType.EXRIGHT_DIVIDEND,
                        timestamp=timestamp,
                        note=f"除权分红: {fhcontent}, 每股{bonus_per_10 / 10:.2f}元",
                    ))

                # 按 timestamp 插入到正确位置
                for pos in exright_positions:
                    inserted = False
                    for i, existing in enumerate(account.positions):
                        if existing.timestamp > pos.timestamp:
                            account.positions.insert(i, pos)
                            inserted = True
                            break
                    if not inserted:
                        account.positions.append(pos)

                # 9. 记录已应用
                account.exright_applied.append(ExRightAppliedRecord(
                    cqr=cqr,
                    fhcontent=fhcontent,
                    reason=f"自动应用: 送转{bonus_shares}股, 分红{dividend:.2f}元",
                ))

                # 10. 保存账户
                self.trader.storage.save_account(account)

                # 11. 同步条件系统
                total_qty_new, total_cost_new = self.trader.get_remaining_position(account)
                if total_qty_new > 0:
                    avg_cost = total_cost_new / total_qty_new
                    self._sync_cost_protection(stock_name, avg_cost)

                changed = True
                messages.append(f"{cqr} {fhcontent}: +{bonus_shares}股, -¥{dividend:.2f}成本")

            return changed, "; ".join(messages) if messages else "无变更"

        finally:
            self._processing.discard(key)

    def _migrate_account(self, account: Account):
        """首次迁移：有持仓时只标记建仓前除权，让正常流程处理持仓期内除权；空仓时标记所有"""
        events = self.cache.get_events(account.stock_code)
        if not events:
            return

        # 找到首次买入日期
        first_buy_date = None
        for pos in account.positions:
            if pos.operation == OperationType.BUY:
                first_buy_date = pos.timestamp[:10]
                break

        # 检查当前是否有持仓
        total_qty, _ = self.trader.get_remaining_position(account)

        for event in events:
            cqr = event.get('cqr', '')

            # 建仓前的除权一律标记为已处理
            if first_buy_date and cqr < first_buy_date:
                account.exright_applied.append(ExRightAppliedRecord(
                    cqr=cqr,
                    fhcontent=event.get('fhcontent', ''),
                    migrated=True,
                    reason="首次迁移：建仓前除权",
                ))
                continue

            # 空仓：建仓后的除权也标记为已处理（不注入）
            if total_qty <= 0:
                account.exright_applied.append(ExRightAppliedRecord(
                    cqr=cqr,
                    fhcontent=event.get('fhcontent', ''),
                    migrated=True,
                    reason="首次迁移：空仓，标记为已处理",
                ))
            # 有持仓：建仓后的除权不标记，让 check_and_apply 正常注入

        self.trader.storage.save_account(account)

    @staticmethod
    def _get_position_qty_at_date(account: Account, target_date: str) -> float:
        """获取目标日期时的持仓数量（登记日持仓）"""
        if not target_date:
            # 无登记日信息：返回当前持仓
            qty, _ = PaperTrader().get_remaining_position(account)
            return qty

        buy_queue = deque()
        for pos in account.positions:
            pos_date = pos.timestamp[:10]
            if pos_date > target_date:
                break
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

        return sum(q[0] for q in buy_queue)

    def _sync_cost_protection(self, stock_name: str, avg_cost: float):
        """同步成本保护"""
        try:
            from paper_trading.conditions_manager import ConditionsManager
            cond_mgr = ConditionsManager(self.trader.storage)
            cond_mgr.sync_cost_protection(stock_name, avg_cost)
        except Exception:
            pass
