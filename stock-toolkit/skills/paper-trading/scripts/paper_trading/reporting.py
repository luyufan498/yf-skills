"""报告生成模块

提供持仓、操作历史、收益等报告生成功能
"""

from typing import Optional
from datetime import datetime, timedelta
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
        output += f"- **初始资金**: ¥{pool['total']:,.2f}\n"
        output += f"- **当前总资产**: ¥{pool['current_total']:,.2f}\n"
        output += f"- **可用资金**: ¥{pool['available']:,.2f}\n"
        output += f"- **占用资金**: ¥{pool['used']:,.2f}\n"
        output += f"- **资金使用率**: {pool['usage_rate']:.1f}%\n\n"

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

    def generate_info_markdown_table(
        self,
        stock_name: str,
        storage: Optional[StorageBackend] = None
    ) -> str:
        """
        生成模拟盘状态的 Markdown 表格，供分析报告直接复制粘贴

        Args:
            stock_name: 股票名称
            storage: 存储后端

        Returns:
            Markdown 格式的表格字符串
        """
        from paper_trading.portfolio import PortfolioManager

        manager = PortfolioManager(storage=storage)
        summary = manager.get_account_summary(stock_name)

        if not summary:
            return f"❌ 未找到股票 '{stock_name}' 的账户记录"

        pool = summary["capital_pool"]
        positions = summary["positions"]
        profit = summary["profit"]

        total_capital = pool["total"]
        available = pool["available"]
        used = pool["used"]
        usage_rate = pool["usage_rate"]

        qty = positions["total_quantity"]
        total_cost = positions["total_cost"]
        current_price = positions.get("current_price")

        realized = profit["realized"]
        floating = profit["floating"]
        total_profit = profit["total"]
        return_rate = (total_profit / total_capital * 100) if total_capital > 0 else 0.0

        if qty > 0:
            avg_cost = total_cost / qty
            market_value = qty * current_price if current_price else 0.0
            qty_str = f"{qty}股"
            avg_cost_str = f"¥{avg_cost:,.2f}"
            market_value_str = f"¥{market_value:,.2f}"
            price_str = f"¥{current_price:.2f}" if current_price else "-"
            cost_str = f"¥{total_cost:,.2f}"
            used_str = f"¥{used:,.2f}"
            usage_str = f"{usage_rate:.1f}%"
        else:
            qty_str = "空仓"
            avg_cost_str = "-"
            market_value_str = "-"
            price_str = "-"
            cost_str = "¥0.00"
            used_str = "¥0.00"
            usage_str = "0.0%"

        def fmt_profit(value: float) -> str:
            if value >= 0:
                return f"📈 +¥{value:,.2f}"
            return f"📉 -¥{abs(value):,.2f}"

        realized_str = fmt_profit(realized)
        floating_str = fmt_profit(floating)
        total_str = fmt_profit(total_profit)

        if return_rate >= 0:
            ret_str = f"📈 +{return_rate:.2f}%"
        else:
            ret_str = f"📉 -{abs(return_rate):.2f}%"

        # 构建对齐的 Markdown 表格
        rows = [
            ["资金池状态", "-", "持仓信息", "-", "收益情况", "-"],
            ["总资金", f"¥{total_capital:,.2f}", "当前持仓", qty_str, "已投入总成本", cost_str],
            ["可用资金", f"¥{available:,.2f}", "平均成本", avg_cost_str, "已实现盈亏", realized_str],
            ["已用资金", used_str, "持仓市值", market_value_str, "浮动盈亏", floating_str],
            ["使用率", usage_str, "当前价格", price_str, "总盈亏", total_str],
            ["", "", "", "", "总收益率", ret_str],
        ]

        import unicodedata

        def _display_width(s: str) -> int:
            """计算字符串在等宽字体中的显示宽度（CJK/emoji 占 2 列）"""
            w = 0
            for ch in s:
                eaw = unicodedata.east_asian_width(ch)
                if eaw in ('F', 'W', 'A'):
                    w += 2
                elif eaw == 'C':
                    w += 0
                else:
                    w += 1
            return w

        def _pad(cell: str, width: int, align: str = "left") -> str:
            """按显示宽度填充，使 | 严格垂直对齐"""
            pad_len = width - _display_width(cell)
            if align == "right":
                return " " * pad_len + cell
            return cell + " " * pad_len

        # 按显示宽度计算每列最大宽度
        col_widths = [max(_display_width(cell) for cell in col) for col in zip(*rows)]

        lines = []
        for i, row in enumerate(rows):
            if i == 0:
                # 表头
                padded = [_pad(cell, col_widths[j], "left") for j, cell in enumerate(row)]
                lines.append("| " + " | ".join(padded) + " |")
                # 分隔线
                separators = ["-" * w for w in col_widths]
                lines.append("| " + " | ".join(separators) + " |")
            else:
                # 数据行：标签列左对齐，数值列右对齐
                aligned = []
                for j, cell in enumerate(row):
                    align = "right" if j % 2 == 1 else "left"
                    aligned.append(_pad(cell, col_widths[j], align))
                lines.append("| " + " | ".join(aligned) + " |")

        # 追加除权复权记录（直接从 positions 中提取，体现股数和成本变化）
        exright_positions = summary.get("exright_positions", [])
        if exright_positions:
            lines.append("")
            lines.append("> **除权复权记录**")
            for p in exright_positions:
                ts = p.get("timestamp", "")[:10]
                op = p.get("operation", "")
                qty = p.get("quantity", 0)
                cost = p.get("total_cost", 0.0)
                note = p.get("note", "")
                if op == "exright_bonus" and qty > 0:
                    lines.append(f"> - 📅 {ts}: 送转 {qty}股 | 成本不变 | {note}")
                elif op == "exright_dividend" and cost < 0:
                    lines.append(f"> - 📅 {ts}: 分红 ¥{abs(cost):,.2f} | 股数不变 | {note}")
                elif note:
                    lines.append(f"> - 📅 {ts}: {note}")

        return "\n".join(lines)

    def generate_operations_report(
        self,
        stock_name: str,
        storage: Optional[StorageBackend] = None,
        days: Optional[int] = None,
        limit: Optional[int] = None
    ) -> str:
        """
        生成操作历史报告

        Args:
            stock_name: 股票名称
            storage: 存储后端
            days: 仅显示最近N天的操作记录
            limit: 最多显示最近N条操作记录

        Returns:
            Markdown格式的报告
        """
        from paper_trading.storage import StorageFactory

        if storage is None:
            storage = StorageFactory.create_storage("json")

        ops_data = storage.load_operations(stock_name)

        if not ops_data:
            return f"❌ 未找到股票 '{stock_name}' 的操作记录"

        operations = ops_data.operations

        # 按天数过滤
        if days is not None and days > 0:
            cutoff = datetime.now() - timedelta(days=days)
            operations = [
                op for op in operations
                if datetime.fromisoformat(op.timestamp) >= cutoff
            ]

        parts = []
        if days:
            parts.append(f"最近 {days} 天")
        if limit:
            parts.append(f"最近 {limit} 笔")
        title_suffix = f" ({', '.join(parts)})" if parts else ""
        output = f"# 📝 {stock_name} 操作历史{title_suffix}\n\n"

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

        display_ops = operations[-limit:] if limit else operations

        for op in reversed(display_ops):
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
        output += f"- **总初始资金**: ¥{summary.total_capital:,.2f}\n"
        output += f"- **当前总资产**: ¥{summary.total_current_assets:,.2f}\n"
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
