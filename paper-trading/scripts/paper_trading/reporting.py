"""报告生成模块

提供持仓、操作历史、收益等报告生成功能
"""

from typing import Optional
from paper_trading.models import (
    Account,
    Operation,
    OperationType,
)
from paper_trading.storage import StorageBackend


class ReportGenerator:
    """报告生成器"""

    def generate_holdings_report(
        self,
        stock_name: str,
        storage: Optional[StorageBackend] = None
    ) -> str:
        """
        生成持仓报告

        Args:
            stock_name: 股票名称
            storage: 存储后端

        Returns:
            Markdown格式的报告
        """
        from paper_trading.trading import PaperTrader
        from paper_trading.portfolio import PortfolioManager

        manager = PortfolioManager(storage=storage)
        summary = manager.get_account_summary(stock_name)

        if not summary:
            return f"❌ 未找到股票 '{stock_name}' 的持仓记录"

        account = manager.trader.get_account(stock_name)

        output = f"# 📊 {stock_name} 持仓报告\n\n"

        pool = summary["capital_pool"]
        output += f"## 💰 资金池\n\n"
        output += f"- **总资金**: ¥{pool['total']:,.2f}\n"
        output += f"- **可用资金**: ¥{pool['available']:,.2f}\n"
        output += f"- **占用资金**: ¥{pool['used']:,.2f}\n"
        output += f"- **资金使用率**: {(pool['used']/pool['total']*100):.1f}%\n\n"

        positions = summary["positions"]
        if positions["total_quantity"] == 0:
            output += "## 📭 当前无持仓\n\n"
        else:
            output += f"## 📈 当前持仓\n\n"
            output += f"- **持股数量**: {positions['total_quantity']} 股\n"
            output += f"- **持仓成本**: ¥{positions['total_cost']:,.2f}\n"
            if positions["current_price"]:
                output += f"- **当前价格**: ¥{positions['current_price']:.2f}\n"
                market_value = positions["total_quantity"] * positions["current_price"]
                floating_profit = market_value - positions["total_cost"]
                if floating_profit >= 0:
                    output += f"- **持仓市值**: ¥{market_value:,.2f}\n"
                    output += f"- **浮动盈亏**: 📈 +¥{floating_profit:,.2f}\n"
                else:
                    output += f"- **持仓市值**: ¥{market_value:,.2f}\n"
                    output += f"- **浮动盈亏**: 📉 ¥{floating_profit:,.2f}\n"

        profit = summary["profit"]
        output += f"## 💵 收益统计\n\n"
        output += f"- **已实现盈亏**: "
        if profit["realized"] >= 0:
            output += f"📈 +¥{profit['realized']:,.2f}\n"
        else:
            output += f"📉 ¥{profit['realized']:,.2f}\n"

        output += f"- **浮动盈亏**: "
        if profit["floating"] >= 0:
            output += f"📈 +¥{profit['floating']:,.2f}\n"
        else:
            output += f"📉 ¥{profit['floating']:,.2f}\n"

        output += f"- **总盈亏**: "
        if profit["total"] >= 0:
            output += f"📈 +¥{profit['total']:,.2f}\n"
        else:
            output += f"📉 ¥{profit['total']:,.2f}\n"

        return_rate = (profit["total"] / pool["total"] * 100) if pool["total"] > 0 else 0
        output += f"- **总收益率**: "
        if return_rate >= 0:
            output += f"📈 +{return_rate:.2f}%\n"
        else:
            output += f"📉 {return_rate:.2f}%\n"

        return output

    def generate_operations_report(
        self,
        stock_name: str,
        storage: Optional[StorageBackend] = None
    ) -> str:
        """
        生成操作历史报告

        Args:
            stock_name: 股票名称
            storage: 存储后端

        Returns:
            Markdown格式的报告
        """
        from paper_trading.storage import StorageFactory

        if storage is None:
            storage = StorageFactory.create_storage("json")

        ops_data = storage.load_operations(stock_name)

        if not ops_data:
            return f"❌ 未找到股票 '{stock_name}' 的操作记录"

        output = f"# 📝 {stock_name} 操作历史\n\n"

        operations = ops_data.operations

        if not operations:
            output += "📭 暂无操作记录\n"
            return output

        total_buy = 0.0
        total_sell = 0.0
        total_profit = 0.0
        sell_count = 0

        for op in operations:
            if op.type == OperationType.BUY:
                total_buy += op.amount or 0
            elif op.type == OperationType.SELL:
                total_sell += op.amount or 0
                total_profit += op.profit or 0
                sell_count += 1

        output += f"## 📊 操作统计\n\n"
        output += f"- **总买入金额**: ¥{total_buy:,.2f}\n"
        output += f"- **总卖出金额**: ¥{total_sell:,.2f}\n"

        if sell_count > 0:
            output += f"- **累计盈亏**: "
            if total_profit >= 0:
                output += f"📈 +¥{total_profit:,.2f}\n"
            else:
                output += f"📉 ¥{total_profit:,.2f}\n"
            output += f"- **交易次数**: {sell_count} 次\n\n"

        output += f"## 📋 操作流水\n\n"
        output += "| 时间 | 类型 | 价格 | 数量 | 金额 | 盈亏 | 备注 |\n"
        output += "|------|------|------|------|------|------|------|\n"

        for op in reversed(operations):
            time_str = op.timestamp[:16].replace('T', ' ')

            if op.type == OperationType.INIT:
                output += f"| {time_str} | 初始化 | - | - | ¥{op.capital or 0:,.2f} | - | 初始资金池 |\n"
            elif op.type == OperationType.BUY:
                output += f"| {time_str} | 买入 | ¥{op.price or 0:.2f} | {op.quantity or 0} | ¥{op.amount or 0:,.2f} | - | {op.note} |\n"
            elif op.type == OperationType.SELL:
                profit = op.profit or 0
                profit_str = f"+¥{profit:,.2f}" if profit >= 0 else f"¥{profit:,.2f}"
                output += f"| {time_str} | 卖出 | ¥{op.price or 0:.2f} | {op.quantity or 0} | ¥{op.amount or 0:,.2f} | {profit_str} | {op.note} |\n"

        return output

    def generate_profit_report(
        self,
        stock_name: str,
        storage: Optional[StorageBackend] = None
    ) -> str:
        """
        生成收益报告

        Args:
            stock_name: 股票名称
            storage: 存储后端

        Returns:
            Markdown格式的报告
        """
        from paper_trading.portfolio import PortfolioManager

        manager = PortfolioManager(storage=storage)
        summary = manager.get_account_summary(stock_name)

        if not summary:
            return f"❌ 未找到股票 '{stock_name}' 的数据"

        output = f"# 💰 {stock_name} 收益报告\n\n"

        profit = summary["profit"]
        pool = summary["capital_pool"]

        output += f"## 📊 总体收益\n\n"
        output += f"- **实现盈亏（已平仓）**: "
        if profit["realized"] >= 0:
            output += f"📈 +¥{profit['realized']:,.2f}\n"
        else:
            output += f"📉 ¥{profit['realized']:,.2f}\n"

        output += f"- **浮动盈亏（持仓中）**: "
        if profit["floating"] >= 0:
            output += f"📈 +¥{profit['floating']:,.2f}\n"
        else:
            output += f"📉 ¥{profit['floating']:,.2f}\n"

        output += f"- **总盈亏**: "
        if profit["total"] >= 0:
            output += f"📈 +¥{profit['total']:,.2f}\n"
        else:
            output += f"📉 ¥{profit['total']:,.2f}\n"

        return_rate = (profit["total"] / pool["total"] * 100) if pool["total"] > 0 else 0
        output += f"- **总收益率**: {return_rate:.2f}%\n\n"

        account = manager.trader.get_account(stock_name)
        operations = manager.trader.storage.load_operations(stock_name)

        sell_operations = []
        if operations:
            for op in operations.operations:
                if op.type == OperationType.SELL:
                    sell_operations.append(op)

        if sell_operations:
            output += f"## 📋 单笔收益明细\n\n"
            output += "| 时间 | 卖出价格 | 数量 | 成交金额 | 买入均价 | 盈亏 | 收益率 |\n"
            output += "|------|----------|------|----------|----------|------|--------|\n"

            for op in reversed(sell_operations):
                sell_price = op.price or 0
                buy_price = (op.cost / op.quantity) if op.quantity > 0 else 0
                time_str = op.timestamp[:16].replace('T', ' ')
                qty = op.quantity or 0
                amount = op.amount or 0
                cost = op.cost or 0
                profit = op.profit or 0
                return_rate = (profit / cost * 100) if cost > 0 else 0

                profit_str = f"+¥{profit:,.2f}" if profit >= 0 else f"¥{profit:,.2f}"
                rate_str = f"+{return_rate:.2f}%" if return_rate >= 0 else f"{return_rate:.2f}%"

                output += f"| {time_str} | ¥{sell_price:.2f} | {qty} | ¥{amount:,.2f} | ¥{buy_price:.2f} | {profit_str} | {rate_str} |\n"

        return output

    def generate_portfolio_report(
        self,
        storage: Optional[StorageBackend] = None
    ) -> str:
        """
        生成投资组合报告

        Args:
            storage: 存储后端

        Returns:
            Markdown格式的报告
        """
        from paper_trading.portfolio import PortfolioManager

        manager = PortfolioManager(storage=storage)
        summary = manager.get_portfolio_summary()

        if not summary:
            return "❌ 没有投资组合数据"

        output = "# 💼 投资组合报告\n\n"

        output += "## 📊 总体概况\n\n"
        output += f"- **总资金**: ¥{summary.total_capital:,.2f}\n"
        output += f"- **可用资金**: ¥{summary.total_available:,.2f}\n"
        output += f"- **占用资金**: ¥{summary.total_used:,.2f}\n"
        output += f"- **持仓数量**: {summary.total_positions}\n"
        output += f"- **持仓市值**: ¥{summary.total_market_value:,.2f}\n"
        output += f"- **持仓成本**: ¥{summary.total_cost:,.2f}\n\n"

        output += "## 💵 收益分析\n\n"
        output += f"- **已实现盈亏**: "
        if summary.realized_profit >= 0:
            output += f"📈 +¥{summary.realized_profit:,.2f}\n"
        else:
            output += f"📉 ¥{summary.realized_profit:,.2f}\n"

        output += f"- **浮动盈亏**: "
        if summary.floating_profit >= 0:
            output += f"📈 +¥{summary.floating_profit:,.2f}\n"
        else:
            output += f"📉 ¥{summary.floating_profit:,.2f}\n"

        output += f"- **总盈亏**: "
        if summary.total_profit >= 0:
            output += f"📈 +¥{summary.total_profit:,.2f}\n"
        else:
            output += f"📉 ¥{summary.total_profit:,.2f}\n"

        output += f"- **总收益率**: "
        if summary.return_rate >= 0:
            output += f"📈 +{summary.return_rate:.2f}%\n"
        else:
            output += f"📉 {summary.return_rate:.2f}%\n"

        account_names = manager.list_accounts()
        if account_names:
            output += f"\n## 📋 持仓列表\n\n"
            for name in account_names:
                account_summary = manager.get_account_summary(name)
                if account_summary and account_summary["capital_pool"]["total"] > 0:
                    pos_qty = account_summary["positions"]["total_quantity"]
                    output += f"- **{name}**: ¥{account_summary['capital_pool']['available']:,.2f} 可用, "
                    if pos_qty > 0:
                        output += f"{pos_qty} 股"
                    else:
                        output += "空仓"
                    output += "\n"

        return output
