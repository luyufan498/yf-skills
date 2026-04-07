"""核心交易逻辑

提供买入、卖出等核心交易功能
"""

from typing import Optional
from paper_trading.models import (
    Account,
    CapitalPool,
    Position,
    Operation,
    OperationType,
    StockInfo,
)
from paper_trading.storage import StorageBackend
from paper_trading.price_fetcher import StockPriceFetcher


class PaperTrader:
    """模拟盘交易器"""

    def __init__(self, storage: Optional[StorageBackend] = None):
        """
        初始化交易器

        Args:
            storage: 存储后端，默认使用JSON存储
        """
        if storage is None:
            from paper_trading.storage import StorageFactory
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
            stock_code: 股票代码（可选）
            force: 是否强制重新初始化

        Returns:
            Account 对象

        Raises:
            ValueError: 如果账户已存在且未使用force参数
        """
        from paper_trading.storage import sanitize_stock_name

        clean_name = sanitize_stock_name(stock_name)

        existing = self.storage.load_account(stock_name)
        if existing:
            if force:
                self.storage.delete_account(stock_name)
            else:
                raise ValueError(f"账户 '{clean_name}' 已存在，现有资金：¥{existing.capital_pool.total:,.2f}")

        if stock_code is None:
            from paper_trading.code_searcher import StockCodeSearcher
            searcher = StockCodeSearcher()
            results = searcher.search_cn_stocks(stock_name, limit=3)
            if results:
                stock_code = results[0].get('code', '')
            else:
                stock_code = None

        account = Account(
            stock_name=clean_name,
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
        return self.storage.load_account(stock_name)

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
            from paper_trading.code_searcher import StockCodeSearcher
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

        self.storage.save_account(account)

        buy_operation = Operation(
            type=OperationType.BUY,
            price=current_price,
            quantity=trade_qty,
            amount=required,
            note=note
        )
        self.storage.save_operation(stock_name, buy_operation)

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

        total_quantity = sum(
            p.quantity for p in account.positions
            if p.operation == OperationType.BUY
        )

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

        cost_amount = self._calculate_cost(account.positions, trade_qty)

        profit = trade_amount - cost_amount

        account.capital_pool.deposit(trade_amount)
        account.capital_pool.used -= cost_amount

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

        return account

    def _calculate_cost(self, positions: list, quantity: int) -> float:
        """
        计算卖出成本（先进先出）

        Args:
            positions: 持仓列表
            quantity: 卖出数量

        Returns:
            总成本
        """
        cost_amount = 0.0
        remaining_qty = quantity

        for pos in positions:
            if remaining_qty <= 0:
                break
            if pos.operation == OperationType.SELL:
                continue

            pos_qty = pos.quantity
            cost_per_share = pos.total_cost / pos_qty if pos_qty > 0 else 0

            if pos_qty <= remaining_qty:
                cost_amount += pos.total_cost
                remaining_qty -= pos_qty
            else:
                cost_amount += remaining_qty * cost_per_share
                remaining_qty = 0

        return cost_amount
