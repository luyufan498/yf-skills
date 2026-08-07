"""路径配置：定位 SQLite 数据库文件。"""

import os
from pathlib import Path


def get_db_path() -> Path:
    """返回数据库路径。STOCK_NEWS_DB 环境变量优先，缺省 cwd/data/news/news.db。"""
    env = os.getenv("STOCK_NEWS_DB")
    if env:
        return Path(env)
    return Path(os.getcwd()) / "data" / "news" / "news.db"
