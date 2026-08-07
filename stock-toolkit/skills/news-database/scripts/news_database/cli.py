"""newsdb 命令行入口。"""

import typer

app = typer.Typer(
    name="newsdb",
    help="📰 News Database - 独立新闻库",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def _main() -> None:
    """newsdb 独立新闻库（agent 整理后的消息源）。"""

    # 空实现占位：后续任务在 app 上注册子命令（schema/storage/query/fts 等）。
