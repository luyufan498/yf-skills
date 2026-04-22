"""投资组合管理

提供多股票账户管理和投资组合汇总功能
"""

from typing import List, Optional
from paper_trading.models import (
    Account,
    Operation,
    OperationType,
    PortfolioSummary,
    StockInfo,
)
from paper_trading.storage import StorageBackend
from paper_trading.price_fetcher import StockPriceFetcher
from paper_trading.trading import PaperTrader


class PortfolioManager:
    """投资组合管理器"""

    def __init__(self, storage: Optional[StorageBackend] = None):
        """
        初始化管理器

        Args:
            storage: 存储后端
        """
        self.trader = PaperTrader(storage=storage)
        self.price_fetcher = StockPriceFetcher()

    def list_accounts(self) -> List[str]:
        """
        列出所有账户

        Returns:
            股票名称列表
        """
        return self.trader.storage.list_accounts()

    def get_account_summary(self, stock_name: str) -> Optional[dict]:
        """
        获取账户汇总信息

        Args:
            stock_name: 股票名称

        Returns:
            汇总信息字典
        """
        account = self.trader.get_account(stock_name)
        if not account:
            return None

        operations = self.trader.storage.load_operations(stock_name)

        total_quantity, total_cost = self.trader.get_remaining_position(account)

        realized_profit = 0.0
        if operations:
            for op in operations.operations:
                if op.type == OperationType.SELL:
                    realized_profit += op.profit or 0.0

        floating_profit = 0.0
        current_price = None
        if total_quantity > 0 and account.stock_code:
            price_info = self.price_fetcher.get_realtime_price(account.stock_code)
            if price_info and price_info.current_price:
                current_price = price_info.current_price
                market_value = total_quantity * current_price
                floating_profit = market_value - total_cost

        total_profit = realized_profit + floating_profit

        return {
            "stock_name": account.stock_name,
            "stock_code": account.stock_code,
            "capital_pool": account.capital_pool.model_dump(),
            "positions": {
                "total_quantity": total_quantity,
                "total_cost": total_cost,
                "current_price": current_price,
            },
            "profit": {
                "realized": realized_profit,
                "floating": floating_profit,
                "total": total_profit,
            },
        }

    def get_account_with_realtime_price(self, stock_name: str) -> Optional[Account]:
        """
        获取账户信息并更新实时价格

        Args:
            stock_name: 股票名称

        Returns:
            Account对象，包含实时价格更新
        """
        account = self.trader.get_account(stock_name)
        if not account:
            return None

        if account.stock_code:
            price_info = self.price_fetcher.get_realtime_price(account.stock_code)
            if price_info:
                pass

        return account

    def get_portfolio_summary(self) -> Optional[PortfolioSummary]:
        """
        获取投资组合汇总

        Returns:
            PortfolioSummary 对象
        """
        account_names = self.list_accounts()
        if not account_names:
            return None

        total_capital = 0.0
        total_available = 0.0
        total_used = 0.0
        total_positions = 0

        for name in account_names:
            summary = self.get_account_summary(name)
            if summary:
                pool = summary["capital_pool"]
                total_capital += pool["total"]
                total_available += pool["available"]
                total_used += pool["used"]

                if summary["positions"]["total_quantity"] > 0:
                    total_positions += 1

        total_market_value = 0.0
        total_cost = 0.0
        realized_profit = 0.0
        floating_profit = 0.0

        for name in account_names:
            account = self.trader.get_account(name)
            if account:
                remaining_qty, remaining_cost = self.trader.get_remaining_position(account)
                total_cost += remaining_cost

                if account.stock_code and remaining_qty > 0:
                    price_info = self.price_fetcher.get_realtime_price(account.stock_code)
                    if price_info and price_info.current_price:
                        market_value = remaining_qty * price_info.current_price
                        total_market_value += market_value
                        floating_profit += (market_value - remaining_cost)

                operations = self.trader.storage.load_operations(name)
                if operations:
                    for op in operations.operations:
                        if op.type == OperationType.SELL:
                            realized_profit += op.profit or 0.0

        total_profit = realized_profit + floating_profit
        return_rate = (total_profit / total_capital * 100) if total_capital > 0 else 0.0

        return PortfolioSummary(
            total_capital=total_capital,
            total_available=total_available,
            total_used=total_used,
            total_positions=total_positions,
            total_market_value=total_market_value,
            total_cost=total_cost,
            realized_profit=realized_profit,
            floating_profit=floating_profit,
            total_profit=total_profit,
            return_rate=return_rate,
        )

    def delete_account(self, stock_name: str, force: bool = False) -> bool:
        """
        删除账户

        Args:
            stock_name: 股票名称
            force: 是否强制删除（即使有持仓）

        Returns:
            是否删除成功
        """
        account = self.trader.get_account(stock_name)
        if not account:
            return False

        has_position = any(
            p.operation == OperationType.BUY for p in account.positions
        )

        if has_position and not force:
            raise ValueError("账户仍有持仓，请先清仓或使用 force=True 强制删除")

        return self.trader.storage.delete_account(stock_name)
