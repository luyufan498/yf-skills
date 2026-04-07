"""性能分析模块

提供收益统计、性能指标等功能
"""

from typing import Optional, Dict, List
from datetime import datetime, timedelta
from paper_trading.models import (
    Account,
    Operation,
    OperationType,
    PerformanceMetrics,
)
from paper_trading.storage import StorageBackend


class PerformanceAnalyzer:
    """性能分析器"""

    def __init__(self, storage: Optional[StorageBackend] = None):
        """
        初始化分析器

        Args:
            storage: 存储后端
        """
        from paper_trading.storage import StorageFactory
        self.storage = storage or StorageFactory.create_storage("json")

    def get_basic_metrics(self, stock_name: str) -> Optional[Dict]:
        """
        获取基础指标

        Args:
            stock_name: 股票名称

        Returns:
            指标字典
        """
        from paper_trading.portfolio import PortfolioManager

        manager = PortfolioManager(storage=self.storage)
        summary = manager.get_account_summary(stock_name)

        if not summary:
            return None

        profit = summary["profit"]
        pool = summary["capital_pool"]

        total_return = profit["total"] / pool["total"] * 100 if pool["total"] > 0 else 0

        operations = self.storage.load_operations(stock_name)
        total_trades = 0
        if operations:
            for op in operations.operations:
                if op.type == OperationType.SELL:
                    total_trades += 1

        return {
            "total_return": total_return,
            "total_profit": profit["total"],
            "total_trades": total_trades,
            "capital": pool["total"],
            "available": pool["available"],
        }

    def get_performance_stats(self, stock_name: str) -> Optional[PerformanceMetrics]:
        """
        获取完整的性能指标

        Args:
            stock_name: 股票名称

        Returns:
            PerformanceMetrics 对象
        """
        summary = self.get_basic_metrics(stock_name)
        if not summary:
            return None

        operations = self.storage.load_operations(stock_name)
        if not operations:
            return None

        buy_trades = []
        sell_trades = []

        for op in operations.operations:
            if op.type == OperationType.BUY:
                buy_trades.append(op)
            elif op.type == OperationType.SELL:
                sell_trades.append(op)

        win_trades = 0
        win_amounts = []
        loss_amounts = []

        for sell_op in sell_trades:
            profit = sell_op.profit or 0
            if profit > 0:
                win_trades += 1
                win_amounts.append(profit)
            elif profit < 0:
                loss_amounts.append(abs(profit))

        win_rate = (win_trades / len(sell_trades) * 100) if sell_trades else None
        avg_win = sum(win_amounts) / len(win_amounts) if win_amounts else None
        avg_loss = sum(loss_amounts) / len(loss_amounts) if loss_amounts else None

        profit_loss_ratio = (avg_win / avg_loss) if avg_win and avg_loss and avg_loss > 0 else None

        return PerformanceMetrics(
            total_return=summary["total_return"],
            annualized_return=None,
            sharpe_ratio=None,
            max_drawdown=None,
            total_trades=summary["total_trades"],
            win_rate=win_rate,
            avg_win_amount=avg_win,
            avg_loss_amount=avg_loss,
            profit_loss_ratio=profit_loss_ratio,
        )

    def analyze_trading_patterns(self, stock_name: str) -> Optional[Dict]:
        """
        分析交易模式

        Args:
            stock_name: 股票名称

        Returns:
            分析结果字典
        """
        operations = self.storage.load_operations(stock_name)
        if not operations:
            return None

        buys = [op for op in operations.operations if op.type == OperationType.BUY]
        sells = [op for op in operations.operations if op.type == OperationType.SELL]

        pattern = {
            "total_buys": len(buys),
            "total_sells": len(sells),
            "avg_buy_amount": 0.0,
            "avg_sell_amount": 0.0,
            "most_active_time": None,
        }

        if buys:
            avg_buy = sum(op.amount or 0 for op in buys) / len(buys)
            pattern["avg_buy_amount"] = avg_buy

        if sells:
            avg_sell = sum(op.amount or 0 for op in sells) / len(sells)
            pattern["avg_sell_amount"] = avg_sell

        time_slots = {}
        for op in operations.operations:
            hour = op.timestamp.split('T')[1][:5] if 'T' in op.timestamp else None
            if hour:
                time_slots[hour] = time_slots.get(hour, 0) + 1

        if time_slots:
            pattern["most_active_time"] = max(time_slots.keys(), key=lambda k: time_slots[k])

        return pattern
