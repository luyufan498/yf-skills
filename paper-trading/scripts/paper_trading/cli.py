"""命令行接口

使用 Typer 提供友好的CLI交互
"""

import typer
from typing import Optional
import importlib.metadata

from paper_trading.trading import PaperTrader
from paper_trading.portfolio import PortfolioManager
from paper_trading.reporting import ReportGenerator
from paper_trading.export import DataExporter

# 从已安装包读取版本号
try:
    __version__ = importlib.metadata.version("paper-trading")
except importlib.metadata.PackageNotFoundError:
    __version__ = "unknown"

app = typer.Typer(
    name="paper-trading",
    help="📊 Paper Trading System - 模拟盘交易系统",
    add_completion=False,
    no_args_is_help=True
)


@app.command()
def version():
    """显示版本信息"""
    typer.echo(f"paper-trading v{__version__}")


@app.command()
def init(
    stock_name: str = typer.Argument(..., help="股票名称"),
    capital: float = typer.Option(..., "--capital", "-c", help="初始资金"),
    code: Optional[str] = typer.Option(None, "--code", help="股票代码（可选）"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重新初始化")
):
    """初始化资金池"""
    try:
        trader = PaperTrader()
        account = trader.init_account(
            stock_name=stock_name,
            capital=capital,
            stock_code=code,
            force=force
        )

        typer.echo(f"✅ 资金池初始化成功：{account.stock_name}")
        typer.echo(f"   初始资金：¥{capital:,.2f}")
        typer.echo(f"   股票代码：{account.stock_code or '未知'}")

    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command()
def buy(
    stock_name: str = typer.Argument(..., help="股票名称"),
    qty: Optional[int] = typer.Option(None, "--qty", "-q", help="买入股数"),
    amount: Optional[float] = typer.Option(None, "--amount", "-a", help="买入金额"),
    note: str = typer.Option("", "--note", "-n", help="备注信息")
):
    """买入股票"""
    try:
        trader = PaperTrader()
        account = trader.buy_stock(
            stock_name=stock_name,
            quantity=qty,
            amount=amount,
            note=note
        )

        typer.echo(f"✅ 买入成功：{stock_name}")
        typer.echo(f"   剩余可用：¥{account.capital_pool.available:,.2f}")

    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command()
def sell(
    stock_name: str = typer.Argument(..., help="股票名称"),
    qty: Optional[int] = typer.Option(None, "--qty", "-q", help="卖出股数"),
    all: bool = typer.Option(False, "--all", help="全部卖出"),
    note: str = typer.Option("", "--note", "-n", help="备注信息")
):
    """卖出股票"""
    try:
        trader = PaperTrader()
        account = trader.sell_stock(
            stock_name=stock_name,
            quantity=qty,
            sell_all=all,
            note=note
        )

        typer.echo(f"✅ 卖出成功：{stock_name}")
        typer.echo(f"   可用资金：¥{account.capital_pool.available:,.2f}")

    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


@app.command()
def info(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查看账户详情（合并显示资金池、持仓、收益）"""
    manager = PortfolioManager()

    if stock_name:
        # 查询单个股票的完整信息
        summary = manager.get_account_summary(stock_name)

        if not summary:
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的账户记录")
            raise typer.Exit(1)

        typer.echo(f"📊 {stock_name} 账户详情\n")

        # 资金池信息
        pool = summary["capital_pool"]
        typer.echo("💰 资金池状态")
        typer.echo(f"   总资金：¥{pool['total']:,.2f}")
        typer.echo(f"   可用资金：¥{pool['available']:,.2f}")
        typer.echo(f"   占用资金：¥{pool['used']:,.2f}")
        typer.echo(f"   资金使用率：{pool['used']/pool['total']*100:.1f}%")

        # 持仓信息
        positions = summary["positions"]
        if positions["total_quantity"] == 0:
            typer.echo(f"\n📈 持仓状况：空仓")
        else:
            typer.echo(f"\n📈 持仓状况")
            typer.echo(f"   股票代码：{summary['stock_code']}")
            typer.echo(f"   持仓数量：{positions['total_quantity']} 股")
            typer.echo(f"   持仓成本：¥{positions['total_cost']:,.2f}")
            if positions.get('current_price'):
                typer.echo(f"   当前价格：¥{positions['current_price']:.2f}")

        # 收益信息
        profit = summary["profit"]
        typer.echo(f"\n💵 收益状况")
        typer.echo(f"   实现盈亏：{'📈 +' if profit['realized'] >= 0 else '📉 '}¥{profit['realized']:,.2f}")
        typer.echo(f"   浮动盈亏：{'📈 +' if profit['floating'] >= 0 else '📉 '}¥{profit['floating']:,.2f}")
        typer.echo(f"   总盈亏：{'📈 +' if profit['total'] >= 0 else '📉 '}¥{profit['total']:,.2f}")

        return_rate = (profit["total"] / pool["total"] * 100) if pool["total"] > 0 else 0
        typer.echo(f"   总收益率：{'📈 +' if return_rate >= 0 else '📉 '}{return_rate:.2f}%")

    else:
        # 列出所有账户的简要信息
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"📊 所有账户概览（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary:
                pool = summary["capital_pool"]
                profit = summary["profit"]
                positions = summary["positions"]

                # 持仓状态
                pos_status = f"{positions['total_quantity']}股" if positions["total_quantity"] > 0 else "空仓"

                # 收益图标
                profit_icon = "📈" if profit["total"] >= 0 else "📉"

                typer.echo(f"\n  • {name}:")
                typer.echo(f"    💰 ¥{pool['available']:,.2f} 可用 / ¥{pool['total']:,.2f} 总计")
                typer.echo(f"    📈 {pos_status}")
                typer.echo(f"    {profit_icon} 收益: ¥{profit['total']:+.2f} ({(profit['total']/pool['total']*100):+.2f}%)")


@app.command()
def pool(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查询资金池状态"""
    manager = PortfolioManager()

    if stock_name:
        # 查询单个股票
        summary = manager.get_account_summary(stock_name)

        if not summary:
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的资金池记录")
            raise typer.Exit(1)

        pool = summary["capital_pool"]
        typer.echo(f"💰 {stock_name} 资金池状态")
        typer.echo(f"   总资金：¥{pool['total']:,.2f}")
        typer.echo(f"   可用资金：¥{pool['available']:,.2f}")
        typer.echo(f"   占用资金：¥{pool['used']:,.2f}")
        typer.echo(f"   资金使用率：{pool['used']/pool['total']*100:.1f}%")
    else:
        # 列出所有股票
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"💰 所有资金池状态（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary:
                pool = summary["capital_pool"]
                typer.echo(f"  • {name}: ¥{pool['available']:,.2f} 可用 / ¥{pool['total']:,.2f} 总计")


@app.command()
def holdings(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查看持仓"""
    manager = PortfolioManager()

    if stock_name:
        # 查询单个股票
        summary = manager.get_account_summary(stock_name)

        if not summary:
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的持仓记录")
            raise typer.Exit(1)

        positions_data = summary["positions"]
        if positions_data["total_quantity"] == 0:
            typer.echo(f"📊 {stock_name}: 暂无持仓")
        else:
            typer.echo(f"📊 {stock_name}:")
            typer.echo(f"   股票代码: {summary['stock_code']}")
            typer.echo(f"   持仓数量: {positions_data['total_quantity']} 股")
            typer.echo(f"   持仓成本: ¥{positions_data['total_cost']:,.2f}")
            if positions_data.get('current_price'):
                typer.echo(f"   当前价格: ¥{positions_data['current_price']:.2f}")
    else:
        # 列出所有股票
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"📊 所有持仓（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary and summary["positions"]["total_quantity"] > 0:
                positions_data = summary["positions"]
                price_str = f"¥{positions_data['current_price']:.2f}" if positions_data.get('current_price') else "N/A"
                typer.echo(f"\n  📈 {name}:")
                typer.echo(f"     • {summary['stock_code']}: {positions_data['total_quantity']}股 @ {price_str}")
            else:
                typer.echo(f"  📈 {name}: 暂无持仓")


@app.command()
def operations(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查看操作历史"""
    generator = ReportGenerator()

    if stock_name:
        # 查询单个股票
        report = generator.generate_operations_report(stock_name)
        typer.echo(report)
    else:
        # 列出所有股票
        manager = PortfolioManager()
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"📋 所有操作历史（共 {len(accounts)} 个）")
        for name in accounts:
            operations = manager.trader.storage.load_operations(name)
            if operations and operations.operations:
                typer.echo(f"\n  📅 {name}: {len(operations.operations)} 笔操作")
                for op in operations.operations[-5:]:  # 最近5笔
                    type_value = op.type.value if hasattr(op.type, 'value') else str(op.type)
                    # init 操作显示 capital，其他操作显示 amount
                    if hasattr(op, 'capital') and op.capital is not None:
                        amount_value = op.capital
                    else:
                        amount_value = op.amount if op.amount else 0
                    typer.echo(f"     • {op.timestamp[:10]} {type_value:4s}: {amount_value:.2f}")
            else:
                typer.echo(f"  📅 {name}: 暂无操作")


@app.command()
def profit(stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则列出所有）")):
    """查看收益报告"""
    generator = ReportGenerator()

    if stock_name:
        # 查询单个股票
        report = generator.generate_profit_report(stock_name)
        typer.echo(report)
    else:
        # 列出所有股票
        manager = PortfolioManager()
        accounts = manager.list_accounts()

        if not accounts:
            typer.echo("📭 暂无账户")
            return

        typer.echo(f"💵 所有收益报告（共 {len(accounts)} 个）")
        for name in accounts:
            summary = manager.get_account_summary(name)
            if summary:
                pool = summary["capital_pool"]
                total_capital = pool["total"]
                profit = summary["profit"]["total"]
                profit_rate = (profit / total_capital) * 100

                icon = "📈" if profit >= 0 else "📉"
                typer.echo(f"{icon} {name}: ¥{profit:+.2f} ({profit_rate:+.2f}%)")


@app.command()
def portfolio():
    """查看投资组合报告"""
    generator = ReportGenerator()
    report = generator.generate_portfolio_report()
    typer.echo(report)


@app.command()
def list():
    """列出所有账户"""
    manager = PortfolioManager()
    accounts = manager.list_accounts()

    if not accounts:
        typer.echo("📭 暂无账户")
        return

    typer.echo(f"📊 共有 {len(accounts)} 个账户：")
    for name in accounts:
        summary = manager.get_account_summary(name)
        if summary:
            pool = summary["capital_pool"]
            typer.echo(f"  • {name}: ¥{pool['available']:,.2f} 可用")


@app.command()
def export(
    stock_name: Optional[str] = typer.Option(None, "--stock", "-s", help="股票名称"),
    format: str = typer.Option("json", "--format", "-f", help="导出格式 (json/csv)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="输出文件路径")
):
    """导出数据"""
    exporter = DataExporter()

    if stock_name:
        if format == "csv":
            path = exporter.export_operations_to_csv(stock_name, output)
        else:
            path = exporter.export_holdings_to_json(stock_name, output)
    else:
        if format != "json":
            typer.echo("❌ 批量导出只支持JSON格式", err=True)
            raise typer.Exit(1)
        path = exporter.export_all_to_json(output)

    if path:
        typer.echo(f"✅ 导出成功: {path}")
    else:
        typer.echo("❌ 导出失败", err=True)
        raise typer.Exit(1)


@app.command()
def delete(
    stock_name: str = typer.Argument(..., help="股票名称"),
    force: bool = typer.Option(False, "--force", "-f", help="强制删除（即使有持仓）")
):
    """删除账户"""
    manager = PortfolioManager()

    try:
        result = manager.delete_account(stock_name, force=force)
        if result:
            typer.echo(f"✅ 已删除账户: {stock_name}")
        else:
            typer.echo(f"❌ 未找到账户: {stock_name}")
            raise typer.Exit(1)
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
