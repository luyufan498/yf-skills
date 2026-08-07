"""config.get_db_path 测试：env 优先，缺省回退到 cwd/data/news/news.db。"""

from pathlib import Path
from news_database import config


def test_get_db_path_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCK_NEWS_DB", str(tmp_path / "env.db"))
    assert config.get_db_path() == tmp_path / "env.db"


def test_get_db_path_default(monkeypatch, tmp_path):
    monkeypatch.delenv("STOCK_NEWS_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.get_db_path() == Path.cwd() / "data" / "news" / "news.db"
