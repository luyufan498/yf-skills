"""命令行接口

使用 Typer 提供友好的CLI交互
"""

import typer
from typing import Optional
from pathlib import Path
import importlib.metadata
import re

from paper_trading.trading import PaperTrader
from paper_trading.portfolio import PortfolioManager
from paper_trading.reporting import ReportGenerator
from paper_trading.export import DataExporter
from paper_trading.price_fetcher import StockPriceFetcher
from paper_trading.kline_fetcher import KLineDataFetcher
from paper_trading.code_searcher import StockCodeSearcher
from paper_trading.analysis import AnalysisManager
from paper_trading.temp_data_manager import TempDataManager
from paper_trading.config import get_workspace_config

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
    code: Optional[str] = typer.Option(None, "--code", help="股票代码（可选，如提供则跳过股票名称验证）"),
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
        error_msg = str(e)
        if "未能通过验证" in error_msg:
            typer.echo(f"❌ 股票名称验证失败", err=True)
            typer.echo(f"   {error_msg}", err=True)
            typer.echo(f"   💡 提示：使用 --code 参数可跳过验证，或使用正确的股票名称", err=True)
        else:
            typer.echo(f"❌ {error_msg}", err=True)
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
            stock_code = summary.get('stock_code', 'N/A')
            typer.echo(f"  • {name} ({stock_code}): ¥{pool['available']:,.2f} 可用")


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


@app.command()
def fetch_price(
    code: str = typer.Argument(..., help="股票代码"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/json)")
):
    """获取股票实时价格"""
    try:
        fetcher = StockPriceFetcher()
        info = fetcher.get_realtime_price(code)

        if not info:
            typer.echo(f"❌ 未找到股票代码 '{code}' 的数据")
            raise typer.Exit(1)

        if format == "json":
            import json
            typer.echo(json.dumps({
                "code": info.code,
                "name": info.name,
                "market": info.market.value if hasattr(info.market, 'value') else str(info.market),
                "current_price": info.current_price,
                "pre_close": info.pre_close,
                "open_price": info.open_price,
                "high": info.high,
                "low": info.low,
                "volume": info.volume,
                "date": info.date,
                "time": info.time,
                "source": info.source
            }, ensure_ascii=False, indent=2))
        else:
            # 格式化价格变化百分比
            change_percent = 0
            if info.current_price and info.pre_close:
                change_percent = ((info.current_price - info.pre_close) / info.pre_close) * 100

            icon = "📈" if change_percent >= 0 else "📉"
            sign = "+" if change_percent >= 0 else ""

            typer.echo(f"📊 {info.name} ({info.code})\n")
            typer.echo(f"💰 当前价格: ¥{info.current_price:.2f}" if info.current_price else f"💰 当前价格: N/A")
            typer.echo(f"   昨收价格: ¥{info.pre_close:.2f}" if info.pre_close else f"   昨收价格: N/A")
            typer.echo(f"   开盘价格: ¥{info.open_price:.2f}" if info.open_price else f"   开盘价格: N/A")
            typer.echo(f"   最高价格: ¥{info.high:.2f}" if info.high else f"   最高价格: N/A")
            typer.echo(f"   最低价格: ¥{info.low:.2f}" if info.low else f"   最低价格: N/A")
            typer.echo(f"   成交量: {info.volume}" if info.volume else f"   成交量: N/A")

            typer.echo(f"   日期: {info.date}" if info.date else f"   日期: N/A")
            typer.echo(f"   时间: {info.time}" if info.time else f"   时间: N/A")
            typer.echo(f"   数据源: {info.source}")

            if change_percent != 0:
                typer.echo(f"\n{icon} 涨跌幅: {sign}{change_percent:.2f}%")

    except Exception as e:
        typer.echo(f"❌ 获取价格失败: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def fetch_kline(
    code: str = typer.Argument(..., help="股票代码"),
    kline_type: str = typer.Option("day", "--type", "-t", help="K线类型 (day/week/month/5min/10min/15min/30min/60min)"),
    count: int = typer.Option(120, "--count", "-n", help="获取最近N条数据"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/json)")
):
    """获取股票K线数据"""
    try:
        fetcher = KLineDataFetcher()
        klines = fetcher.fetch_kline_data(code, kline_type=kline_type, count=count)

        if not klines:
            typer.echo(f"❌ 未找到股票代码 '{code}' 的K线数据")
            raise typer.Exit(1)

        if format == "json":
            import json
            typer.echo(json.dumps({
                "code": code,
                "kline_type": kline_type,
                "data": klines
            }, ensure_ascii=False, indent=2))
        else:
            typer.echo(f"📊 {code} {kline_type}K 数据（最近 {len(klines)} 条）\n")

            for kline in klines[-20:]:  # 显示最近20条
                time_str = kline.get('date', '')
                if 'time' in kline and kline['time']:
                    time_str = f"{time_str} {kline['time']}"

                typer.echo(f"📅 {time_str}:")
                typer.echo(f"   开: {kline['open']:.2f}, 收: {kline['close']:.2f}, "
                          f"高: {kline['high']:.2f}, 低: {kline['low']:.2f}")
                typer.echo(f"   成交量: {kline['volume']}")

            if len(klines) > 20:
                typer.echo(f"\n... 共 {len(klines)} 条数据")

    except Exception as e:
        typer.echo(f"❌ 获取K线数据失败: {e}", err=True)
        raise typer.Exit(1)


# --- 市场新闻命令 ---

@app.command("fetch-news")
def fetch_news(
    source: str = typer.Option("all", "--source", "-s", help="新闻源: all, cls (财联社), sina (新浪财经), tv (TradingView)"),
    limit: int = typer.Option(10, "--limit", "-n", help="新闻数量"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式: pretty, json")
) -> None:
    """
    获取市场新闻

    支持多个新闻源：
      - all: 合并所有源的新闻
      - cls: 财联社电报
      - sina: 新浪财经直播
      - tv: TradingView外媒

    示例:
      ptrade fetch-news --source all --limit 20
      ptrade fetch-news -s cls -n 10 -f json
    """
    from paper_trading.news_fetcher import MarketNewsFetcher

    try:
        fetcher = MarketNewsFetcher()

        # 根据参数获取新闻
        if source == 'cls':
            news_data = fetcher.fetch_cls_news(limit)
        elif source == 'sina':
            news_data = fetcher.fetch_sina_live_news(limit)
        elif source == 'tv':
            news_data = fetcher.fetch_tradingview_news(limit)
        else:  # all
            news_data = fetcher.get_latest_news(limit)

        # 检查空结果
        if not news_data and format != 'json':
            print("📭 未找到任何新闻")
            return

        # 输出结果
        if format == 'json':
            import json
            print(json.dumps({
                'source': source,
                'total': len(news_data),
                'news': news_data
            }, indent=2, ensure_ascii=False))
        else:
            print(f"\n{'='*60}")
            print(f"最新市场新闻 (来源: {source}, 数量: {len(news_data)})")
            print(f"{'='*60}\n")

            for i, item in enumerate(news_data, 1):
                print(f"{i}. [{item['time']}] {item['source']}")
                if item.get('title'):
                    print(f"   标题: {item['title']}")
                print(f"   内容: {item['content'][:150]}...")
                if item.get('tags'):
                    print(f"   标签: {', '.join(item['tags'])}")
                print()

    except ValueError as e:
        print(f"❌ 参数错误: {e}")
        raise typer.Exit(1)
    except Exception as e:
        print(f"❌ 获取新闻失败: {e}")
        raise typer.Exit(1)


# --- 临时数据命令 ---

@app.command("temp-data")
def temp_data_command(
    stock_name: str = typer.Argument(..., help="股票名称"),
    action: str = typer.Option("save", "--action", "-a", help="操作: save/read/list"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="数据类别"),
    content: Optional[str] = typer.Option(None, "--content", help="数据内容"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="从文件读取内容"),
    stdin_flag: bool = typer.Option(False, "--stdin", help="从标准输入读取内容"),
    limit: int = typer.Option(30, "--limit", "-l", help="最多显示的记录数")
):
    """
    临时数据管理

    支持的数据类别（示例）:
      - deep-search: 深度搜索结果
      - history-continuity: 历史连续性分析
      - gf-summary: 广发证券摘要

    示例:
      ptrade temp-data 赛力斯 --action save --category deep-search --content "内容"
      ptrade temp-data 赛力斯 --action save --category deep-search --file data.md --stdin
      ptrade temp-data 赛力斯 --action read --category deep-search
      ptrade temp-data --action list
    """
    manager = TempDataManager(validate_stock=False)

    if action == "save":
        # 验证类别参数
        if not category:
            typer.echo("❌ 错误：必须指定数据类别（--category）", err=True)
            raise typer.Exit(1)

        # 获取内容
        temp_content = None
        if content:
            temp_content = content
        elif file:
            try:
                temp_content = Path(file).read_text(encoding='utf-8')
            except Exception as e:
                typer.echo(f"❌ 读取文件失败: {e}", err=True)
                raise typer.Exit(1)
        elif stdin_flag:
            temp_content = typer.get_text_stream("stdin").read()
        else:
            typer.echo("❌ 错误：必须提供数据内容（--content, --file 或 --stdin）", err=True)
            raise typer.Exit(1)

        try:
            record = manager.save_temp_data(
                stock_name=stock_name,
                category=category,
                content=temp_content
            )
            typer.echo(f"\n✅ 临时数据保存成功")
            typer.echo(f"   股票: {record.stock_name}")
            typer.echo(f"   类别: {record.category}")
            typer.echo(f"   时间: {record.timestamp}")
            typer.echo(f"   路径: {record.file_path}")
        except ValueError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(1)

    elif action == "read":
        # 验证类别参数
        if not category:
            typer.echo("❌ 错误：必须指定数据类别（--category）", err=True)
            raise typer.Exit(1)

        record = manager.read_temp_data(stock_name=stock_name, category=category)

        if not record:
            typer.echo(f"❌ 未找到股票 '{stock_name}' 类别 '{category}' 的临时数据", err=True)
            raise typer.Exit(1)

        typer.echo(f"📄 {record.stock_name} - {record.category}（{record.timestamp[:10]}）\n")
        typer.echo(record.content)

    elif action == "list":
        if stock_name == "all":
            # 列出所有股票的所有类别
            config = get_workspace_config()
            temp_data_dir = config['temp_data_dir']

            if not temp_data_dir.exists():
                typer.echo("📭 暂无临时数据")
                return

            typer.echo(f"📊 所有临时数据（根目录: {temp_data_dir}）:\n")

            for stock_dir in sorted(temp_data_dir.iterdir()):
                if not stock_dir.is_dir():
                    continue

                stock_name = stock_dir.name
                categories = manager.list_categories(stock_name)

                if categories:
                    typer.echo(f"  📈 {stock_name}:")
                    for cat in categories:
                        files = manager.list_temp_data(stock_name, cat, limit=999)
                        typer.echo(f"    • {cat}: {len(files)} 条数据")
        else:
            # 列出某股票的所有类别或某类别下的数据
            if category:
                # 列出某类别下的数据
                files = manager.list_temp_data(stock_name, category, limit)

                if not files:
                    typer.echo(f"📭 未找到 '{stock_name}' 类别 '{category}' 的临时数据")
                    return

                typer.echo(f"📊 {stock_name} - {category}（共 {len(files)} 条）:")
                for f in files:
                    # 从文件名解析时间
                    time_match = re.search(r'(\d{4}-\d{2}-\d{2}-\d{6})', f.name)
                    time_str = time_match.group(1) if time_match else f.name
                    typer.echo(f"  • {f.resolve()} ({time_str})")
            else:
                # 列出某股票的所有类别
                categories = manager.list_categories(stock_name)

                if not categories:
                    typer.echo(f"📭 未找到 '{stock_name}' 的临时数据")
                    return

                typer.echo(f"📊 {stock_name} 的数据类别:")
                for cat in categories:
                    files = manager.list_temp_data(stock_name, cat, limit=999)
                    typer.echo(f"  • {cat}: {len(files)} 条数据")

    else:
        typer.echo(f"❌ 不支持的操作: {action}", err=True)
        raise typer.Exit(1)


# --- 分析报告命令 ---

@app.command()
def analysis(
    stock_name: str = typer.Argument(..., help="股票名称"),
    action: str = typer.Option("save", "--action", "-a", help="操作: save/read/list"),
    content: Optional[str] = typer.Option(None, "--content", "-c", help="分析内容"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="从文件读取内容"),
    limit: int = typer.Option(15, "--limit", "-l", help="最多显示的记录数"),
    count: int = typer.Option(1, "--count", "-n", help="读取的报告数量（仅 read 操作，默认 1）")
):
    """
    分析报告管理

    示例:
      ptrade analysis 赛力斯 --action save --content "# 分析内容"
      ptrade analysis 赛力斯 --action save --file analysis.md
      ptrade analysis 赛力斯 --action read
      ptrade analysis 赛力斯 --action read --count 3
      ptrade analysis --action list
    """
    manager = AnalysisManager()

    if action == "save":
        # 保存分析报告
        if content:
            analysis_content = content
        elif file:
            try:
                analysis_content = Path(file).read_text(encoding='utf-8')
            except Exception as e:
                typer.echo(f"❌ 读取文件失败: {e}", err=True)
                raise typer.Exit(1)
        else:
            typer.echo("❌ 错误：必须提供分析内容（--content 或 --file）", err=True)
            raise typer.Exit(1)

        record = manager.save_analysis(stock_name, analysis_content)
        typer.echo(f"\n✅ 分析报告保存成功")
        typer.echo(f"   股票: {record.stock_name}")
        typer.echo(f"   时间: {record.timestamp}")
        typer.echo(f"   路径: {record.file_path}")

    elif action == "read":
        # 读取分析报告（支持读取最近 N 份）
        records = manager.read_analyses_count(stock_name, count=count)

        if not records:
            typer.echo(f"❌ 未找到股票 '{stock_name}' 的分析记录")
            raise typer.Exit(1)

        # 如果只有一份报告，保持简洁显示
        if len(records) == 1:
            record = records[0]
            typer.echo(f"📄 {record.stock_name} 分析报告（{record.timestamp[:10]}）\n")
            typer.echo(record.content)
        else:
            # 多份报告时，显示分隔符和文件名
            total = len(records)
            for idx, record in enumerate(records, 1):
                file_name = Path(record.file_path).name
                typer.echo(f"{'='*60}")
                typer.echo(f"报告 {idx}/{total}")
                typer.echo(f"{'='*60}")
                typer.echo(f"📄 {record.stock_name} 分析报告（{record.timestamp[:10]}）")
                typer.echo(f"📁 文件: {file_name}\n")
                typer.echo(record.content)
                if idx < total:
                    typer.echo()  # 报告之间添加空行

    elif action == "list":
        # 列出分析记录
        if stock_name == "all":
            # 列出所有股票
            stocks = manager.list_stocks()

            if not stocks:
                typer.echo("📭 暂无分析记录")
                return

            typer.echo(f"📊 已分析的股票（共 {len(stocks)} 只）:")
            for stock in stocks:
                files = manager.list_analyses(stock, limit=999)
                typer.echo(f"  • {stock} - {len(files)} 次分析")
        else:
            # 列出某股票的分析记录
            files = manager.list_analyses(stock_name, limit)

            if not files:
                typer.echo(f"📭 未找到 '{stock_name}' 的分析记录")
                return

            typer.echo(f"📈 {stock_name} 分析历史（共 {len(files)} 条）:")
            for f in files:
                time_match = f.name.replace(".md", "")[-16:]
                typer.echo(f"  • {f.resolve()}")

    else:
        typer.echo(f"❌ 不支持的操作: {action}", err=True)
        raise typer.Exit(1)


@app.command()
def search(
    keyword: str = typer.Argument(..., help="搜索关键词"),
    limit: int = typer.Option(10, "--limit", "-n", help="返回结果数量"),
    format: str = typer.Option("pretty", "--format", "-f", help="输出格式 (pretty/json)")
):
    """搜索股票代码（支持A股、港股、美股热门股票）"""
    try:
        searcher = StockCodeSearcher()
        # 使用综合搜索，包括新浪财经 API 和内置热门股票库
        search_results = searcher.search(keyword, limit=limit)

        # 合并 A股搜索和热门股票库的结果
        results = search_results.get('A_share', []) + search_results.get('hot_funds', [])

        if not results:
            typer.echo(f"❌ 未找到 '{keyword}' 相关的股票")
            raise typer.Exit(1)

        if format == "json":
            import json
            typer.echo(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            typer.echo(f"📊 搜索 '{keyword}' 的结果（共 {len(results)} 条）:\n")

            for idx, result in enumerate(results, 1):
                typer.echo(f"{idx}. {result['name']}")
                typer.echo(f"   代码: {result['code']}")
                typer.echo(f"   市场: {result['market']}")
                typer.echo(f"   来源: {result['source']}")
                typer.echo()

    except Exception as e:
        typer.echo(f"❌ 搜索失败: {e}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
