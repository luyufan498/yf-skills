"""ptrade2 数据/分析命令组 — export / fix / fetch-news / temp-data / analysis

T5 CLI 对齐：从 v1 `paper_trading/cli.py` 移植，import 全部替换为 paper_trading_v2，
命令驱动 SQLite（经 DataExporter/SqlStorage/TempDataManager/AnalysisManager）。cli.py
保持薄分发，本模块通过显式 `register(app)` 注册（cli.py 末尾调用），避免隐式副作用导入。

cron 关键集：export/fix 是数据维护链路；fetch-news/temp-data/analysis 为采集与报告辅助。
"""
import re
from pathlib import Path
from typing import Optional

import typer

from paper_trading_v2.helpers import normalize_stock_name
from paper_trading_v2.analysis import AnalysisManager
from paper_trading_v2.config import get_workspace_config
from paper_trading_v2.export import DataExporter
from paper_trading_v2.portfolio import PortfolioManager
from paper_trading_v2.temp_data_manager import TempDataManager
from paper_trading_v2.trading import PaperTrader


def register(app):
    """注册数据/分析命令组到共享 app（cli.py 末尾显式调用）。"""

    @app.command()
    def export(
        stock_name: Optional[str] = typer.Option(None, "--stock", "-s", help="股票名称"),
        format: str = typer.Option("json", "--format", "-f", help="导出格式 (json/csv)"),
        output: Optional[str] = typer.Option(None, "--output", "-o", help="输出文件路径")
    ):
        """导出数据"""
        stock_name = normalize_stock_name(stock_name)
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
    def fix(
        stock_name: Optional[str] = typer.Argument(None, help="股票名称（不指定则修复所有账户）")
    ):
        """根据 FIFO 重新修正 SELL operation 的 cost 和 profit（修复旧 Bug 数据污染）"""
        stock_name = normalize_stock_name(stock_name)
        trader = PaperTrader()

        if stock_name:
            stock_names = [stock_name]
        else:
            manager = PortfolioManager()
            stock_names = manager.list_accounts()
            if not stock_names:
                typer.echo("📭 暂无账户")
                return

        total_fixed = 0
        has_error = False
        for name in stock_names:
            try:
                result = trader.fix_operations(name)
                if result["fixed"] > 0:
                    typer.echo(f"✅ {name}: 已修正 {result['fixed']}/{result['total_sell']} 笔 SELL 记录")
                    total_fixed += result["fixed"]
                else:
                    typer.echo(f"✅ {name}: 所有 {result['total_sell']} 笔 SELL 记录均正确，无需修正")
            except ValueError as e:
                typer.echo(f"❌ {name}: {e}", err=True)
                has_error = True

        if len(stock_names) > 1:
            if total_fixed > 0:
                typer.echo(f"\n🎉 共修正 {total_fixed} 笔 SELL 记录")
            else:
                typer.echo(f"\n📭 所有 {len(stock_names)} 个账户的 SELL 记录均正确")
        if has_error:
            raise typer.Exit(1)

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
        from paper_trading_v2.news_fetcher import MarketNewsFetcher

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
        """
        stock_name = normalize_stock_name(stock_name)

        """
        支持的数据类别（示例）:
          - deep-search: 深度搜索结果
          - history-continuity: 历史连续性分析
          - gf-summary: 广发证券摘要

        保存示例:
          ptrade temp-data 赛力斯 --action save --category deep-search --content "分析内容"
          ptrade temp-data 赛力斯 --action save --category deep-search --file search_result.md
          ptrade temp-data 赛力斯 --action save --category gf-summary --stdin << 'EOF'
          # 广发证券数据分析
          ...
          EOF

        读取示例:
          ptrade temp-data 赛力斯 --action read --category deep-search

        列出示例:
          ptrade temp-data 赛力斯 --action list
          ptrade temp-data 赛力斯 --action list --category deep-search
          ptrade temp-data all --action list
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

    @app.command()
    def analysis(
        stock_name: str = typer.Argument(..., help="股票名称"),
        action: str = typer.Option("save", "--action", "-a", help="操作: save/read/list"),
        content: Optional[str] = typer.Option(None, "--content", "-c", help="分析内容"),
        file: Optional[str] = typer.Option(None, "--file", "-f", help="从文件读取内容"),
        limit: int = typer.Option(15, "--limit", "-l", help="最多显示的记录数"),
        count: int = typer.Option(1, "--count", "-n", help="读取的报告数量（仅 read 操作，默认 1）"),
        report_id: Optional[str] = typer.Option(None, "--id", help="指定报告文件名（仅 read 操作）")
    ):
        """
        分析报告管理
        """
        stock_name = normalize_stock_name(stock_name)

        """
        示例:
          ptrade analysis 赛力斯 --action save --content "# 分析内容"
          ptrade analysis 赛力斯 --action save --file analysis.md
          ptrade analysis 赛力斯 --action read
          ptrade analysis 赛力斯 --action read --count 3
          ptrade analysis 赛力斯 --action read --id 赛力斯-2026-06-17-1007.md
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
            if report_id:
                # 按指定文件名读取单份报告
                record = manager.read_analysis(stock_name, filename=report_id)
                if not record:
                    typer.echo(f"❌ 未找到股票 '{stock_name}' 的报告 '{report_id}'")
                    raise typer.Exit(1)
                file_name = Path(record.file_path).name
                typer.echo(f"📄 {record.stock_name} 分析报告（{record.timestamp[:10]}）")
                typer.echo(f"📁 文件: {file_name}\n")
                typer.echo(record.content)
            else:
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

    return app
