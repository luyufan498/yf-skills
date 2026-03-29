# Stock Market Data Scripts

提供中国A股、香港港股和美国NASDAQ市场的实时股票数据查询能力。

## 功能依赖说明

- **A股/港股查询**: 无需额外依赖，直接使用 Python 即可
- **美股查询**: 需要安装 yfinance 和 pandas，推荐使用 UV 环境

## 快速开始

### 只用 A股/港股（无需安装依赖）

```bash
# 直接使用 Python
python3 scripts/fetch_kline_data.py sh600000 -t day -c 3
python3 scripts/fetch_kline_data.py hk00700 -t day -c 3
```

### 使用美股功能（需要 UV 环境）

```bash
# 安装 UV 环境
uv venv
uv pip install -e ./scripts

# 测试美股数据
uv run python3 scripts/fetch_kline_data.py AAPL -t day -c 3

# 测试A股数据（也可用 UV）
uv run python3 scripts/fetch_kline_data.py sh600000 -t day -c 3
```

## 安装依赖

### 美股功能 - 使用 UV

仅在需要查询美股数据时执行：

```bash
# 确保在 skill 根目录（包含 SKILL.md 的目录）
uv venv
uv pip install -e ./scripts
```

详细的安装和使用说明请参考：`references/uv-environment-setup.md`

## 使用方式

### 1. 查询实时股价
```bash
uv run python3 scripts/fetch_realtime_stock.py sh600000
uv run python3 scripts/fetch_realtime_stock.py AAPL
```

### 2. 获取K线历史数据
```bash
# A股/港股
uv run python3 scripts/fetch_kline_data.py sh600000 -t day -c 30
uv run python3 scripts/fetch_kline_data.py hk00700 -t week -c 20

# 美股
uv run python3 scripts/fetch_kline_data.py AAPL -t day -c 30
uv run python3 scripts/fetch_kline_data.py TSLA -t 60min -c 8
```

### 3. 查询市场新闻
```bash
uv run python3 scripts/fetch_market_news.py -n 20 --source all -f simple
```

### 4. 搜索股票代码
```bash
uv run python3 scripts/search_stock_code.py 腾讯 --all -n 10
```

## 依赖说明

- `yfinance`: 美股历史数据获取（支持 AAPL, TSLA, SPX 等）
- `pandas`: 数据处理和 MultiIndex 处理
- `requests`: HTTP 请求（用于 A股/港股数据获取）

## 数据源

- **A股/港股**: 腾讯证券 Web 接口（实时）、新浪财经（备用）
- **美股**: Yahoo Finance（YFinance）
- **新闻**: 财联社、新浪财经直播、TradingView 外媒

## 注意事项

- 所有数据源均为免费公开接口
- 新浪、腾讯接口无需认证
- YFinance 无需 API Key
- 建议添加适当的请求间隔，避免频繁请求
- 部分数据可能有延迟（1-3分钟）