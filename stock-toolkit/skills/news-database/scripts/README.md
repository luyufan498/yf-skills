# news-database - 独立新闻库 CLI

Agent 整理后的消息源数据库，供新闻采集 agent 与分析 agent 共享。

## 安装
```bash
cd scripts
uv tool install --editable .
```

## 数据库位置
由 `STOCK_NEWS_DB` 环境变量指定，默认 `<cwd>/data/news/news.db`。

## 命令
见 `newsdb --help`。
