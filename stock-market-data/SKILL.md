---
name: stock-market-data
description: 提供中国A股、香港港股和美国NASDAQ市场的实时股票数据查询能力，包括实时价格、K线历史数据（日K/周K/月K/分钟K线）、分时走势数据、市场新闻聚合和股票代码搜索功能。适用于查询实时股价、获取历史K线数据、查看分时走势、阅读市场新闻、搜索股票代码以及分析股票趋势等场景。所有数据来源于免费公开API，无需API密钥。
---

# 中国股市数据查询

## 概述

本技能提供中国A股、香港港股和美国NASDAQ市场的实时数据查询能力，使用免费的公开数据API，无需API密钥。

## 支持的市场

- **A股市场**: 上海证券交易所(SH)、深圳证券交易所(SZ)、北京证券交易所(BJ)
- **港股市场**: 香港交易所(HK)
- **美股市场**: NASDAQ、NYSE等(代码以gb_或us开头)

## 使用方式

### 1. 查询实时股价

**单只股票查询**:
```bash
python3 scripts/fetch_realtime_stock.py sh600000
python3 scripts/fetch_realtime_stock.py sz000001
python3 scripts/fetch_realtime_stock.py hk00700
python3 scripts/fetch_realtime_stock.py gb_aapl  # 美股
```

**多只股票批量查询**:
```bash
python3 scripts/fetch_realtime_stock.py sh600000 sz000001 hk00700 gb_aapl gb_goog
```

**返回数据格式**:
```json
{
  "sh600000": {
    "code": "sh600000",
    "name": "浦发银行",
    "open": 10.19,
    "pre_close": 10.18,
    "price": 10.18,
    "high": 10.24,
    "low": 10.15,
    "volume": "464298",
    "date": "",
    "time": "20260210161412",
    "source": "tencent"
  }
}
```

### 2. 获取K线历史数据

**分时数据** (每分钟tick数据):
```bash
python3 scripts/fetch_kline_data.py sh600000 -t minute -c 10    # 最近10分钟的分时数据
```

**日K数据** (前复权):
```bash
python3 scripts/fetch_kline_data.py sh600000 -t day -c 30     # 最近30个交易日
```

**周K/月K数据**:
```bash
python3 scripts/fetch_kline_data.py sh600000 -t week -c 20    # 最近20周
python3 scripts/fetch_kline_data.py sh600000 -t month -c 12   # 最近12个月
```

**分钟K线** (基于分时数据采样生成):
```bash
python3 scripts/fetch_kline_data.py sh600000 -t 5min -c 48    # 最近48根5分钟K线
python3 scripts/fetch_kline_data.py sh600000 -t 10min -c 24   # 最近24根10分钟K线
python3 scripts/fetch_kline_data.py sh600000 -t 15min -c 16   # 最近16根15分钟K线
python3 scripts/fetch_kline_data.py sh600000 -t 30min -c 8     # 最近8根30分钟K线
python3 scripts/fetch_kline_data.py sh600000 -t 60min -c 4     # 最近4根60分钟K线
```

**获取完整汇总** (包含所有周期的数据):
```bash
python3 scripts/fetch_kline_data.py sh600000 --summary
```

**参数说明**:
- `-t/--type`: 数据类型
  - `minute`: 分时数据（每分钟tick数据）
  - `5min`/`10min`/`15min`/`30min`/`60min`: 分钟K线（根据分时数据采样生成）
  - `day`: 日K线（前复权）
  - `week`: 周K线（前复权）
  - `month`: 月K线（前复权）
- `-c/--count`: 返回数据条数
  - 分时数据: 返回最近N分钟的数据
  - K线数据: 返回最近N条K线
- `--summary`: 获取所有类型汇总

### 3. 查询市场新闻

支持多个新闻源聚合：财联社电报、新浪财经直播、TradingView外媒。

**获取最新新闻** (所有源合并):
```bash
python3 scripts/fetch_market_news.py -n 20 --source all -f simple
```

**指定新闻源**:
```bash
# 财联社电报
python3 scripts/fetch_market_news.py --source cls -n 30

# 新浪财经直播
python3 scripts/fetch_market_news.py --source sina -n 20

# TradingView外媒
python3 scripts/fetch_market_news.py --source tv -n 10
```

**输出格式选项**:
- `-f json`: JSON格式输出（适合程序调用）
- `-f simple`: 简易文本格式输出（默认，适合人类阅读）

### 4. 搜索股票代码

支持搜索A股（使用新浪金融suggest API）和常用港股/美股（使用内置数据库）。

**搜索A股股票**:
```bash
python3 scripts/search_stock_code.py 平安银行 -n 5
```

**搜索所有市场** (包括港股/美股):
```bash
python3 scripts/search_stock_code.py 腾讯 --all -n 10
```

**JSON格式输出** (适合程序调用):
```bash
python3 scripts/search_stock_code.py 腾讯 --all -f json
```

**查看股票代码格式说明**:
```bash
python3 scripts/search_stock_code.py --guide
```

**参数说明**:
- `keyword`: 搜索关键词（股票名称或代码）
- `-n/--number`: 返回结果数量（默认10）
- `--all`: 搜索所有市场（包括港股/美股常用股票）
- `-f/--format`: 输出格式（text 或 json）
- `--guide`: 显示股票代码格式说明

## 数据源说明

### 实时价格数据

| 市场 | 数据源 | 接口 | 说明 |
|------|--------|------|------|
| A股/港股 | 腾讯证券 | `http://qt.gtimg.cn/` | 数据更新及时，优先使用 |
| A股/美股 | 新浪财经 | `http://hq.sinajs.cn/` | 作为备用数据源 |

### K线数据

- **数据源**: 腾讯证券 Web接口
- **接口**: `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`
- **复权类型**: 前复权 (qfq)
- **支持的周期**: 5min/10min/15min/30min/60min/day/week/month
- **支持市场**: A股（sh/sz/bj）、港股（hk）
  - **注意**: 美股（gb_开头）不支持K线数据，仅支持分时数据
- **分钟K线说明**: 5min/10min/15min/30min/60min 基于分时数据采样生成，数据格式包含 `time` 字段，与分时数据格式一致

### 市场新闻

| 新闻源 | 接口 | 用途 |
|--------|------|------|
| 财联社 | `https://www.cls.cn/nodeapi/telegraphList` | 电报新闻（国内） |
| 新浪财经 | `https://zhibo.sina.com.cn/api/zhibo/feed` | 实时直播 |
| TradingView | `https://news-mediator.tradingview.com/` | 外媒新闻 |

### 股票代码搜索

| 市场 | 数据源 | 接口 | 覆盖范围 |
|------|--------|------|----------|
| A股 | 新浪金融suggest API | `https://suggest3.sinajs.cn/suggest/` | 全部A股（实时搜索） |
| 港股 | 内置数据库 | - | 常用港股（几十只热门股） |
| 美股 | 内置数据库 | - | 常用美股（十几只热门股） |

**注意**: 港股/美股搜索仅限内置的热门股票库。如需查询完整港股/美股代码，建议访问[新浪财经](https://finance.sina.com.cn/)或[东方财富](https://quote.eastmoney.com/)查询。

## 股票代码格式

### A股
- 上海: `sh600000`（浦发银行）
- 深圳: `sz000001`（平安银行）
- 北京: `bj832566`（易实精密）

### 港股
- 格式: `hk00700`（香港交易所，5位代码）
- 示例: `hk00700`（腾讯控股）、`hk03690`（美团-W）

### 美股
- 格式1: `gb_aapl`（苹果）、`gb_goog`（谷歌）、`gb_msft`（微软）
- 格式2: `usAAPL`、`USGOOG`（与格式1等价，脚本会自动转换）

## 脚本详细功能

### fetch_realtime_stock.py

**主要功能**:
- 根据股票类型自动选择最佳数据源（港股/A股优先用腾讯，美股用新浪）
- 支持批量查询多只股票（一次可查多只）
- 解析不同数据源的数据格式并统一化输出
- 返回结构化的股票信息

**数据字段**:
- `code`: 股票代码
- `name`: 股票名称
- `price`: 当前价格
- `open`: 开盘价
- `pre_close`: 昨收价
- `high`: 最高价
- `low`: 最低价
- `volume`: 成交量
- `amount`: 成交额
- `date`: 日期
- `time`: 时间
- `source`: 数据来源（tencent 或 sina）

### fetch_kline_data.py

**主要功能**:
- 获取历史K线数据（日K/周K/月K）
- 获取分时数据（每分钟tick）
- 获取分钟K线（5min/10min/15min/30min/60min，基于分时数据采样生成）
- 支持多周期查询
- 提供数据汇总接口（`--summary`参数）

**日K/周K/月K数据字段**:
- `date`: 日期
- `open`: 开盘价
- `close`: 收盘价
- `high`: 最高价
- `low`: 最低价
- `volume`: 成交量
- `amount`: 成交额

**分时数据字段**:
- `time`: 时间点（格式如 "14:50"）
- `price`: 价格
- `volume`: 成交量
- `amount`: 成交额

**分钟K线数据字段**（5min/10min/15min/30min/60min，基于分时采样）:
- `time`: 时间点（格式与分时数据一致，如 "14:50"）
- `open`: 开盘价
- `close`: 收盘价
- `high`: 最高价
- `low`: 最低价
- `volume`: 成交量
- `amount`: 成交额

### fetch_market_news.py

**主要功能**:
- 多源新闻聚合（财联社、新浪、TradingView）
- 按时间排序
- 标签提取
- 重要新闻标记（红色标记）

**新闻字段**:
- `title`: 标题
- `content`: 内容
- `time`: 时间（HH:MM:SS格式）
- `date`: 日期（YYYY-MM-DD格式）
- `datetime`: 完整时间戳（ISO格式）
- `url`: 原文链接
- `source`: 来源
- `is_red`: 是否重要新闻（布尔值）
- `tags`: 标签列表

### search_stock_code.py

**主要功能**:
- 搜索A股股票代码（使用新浪金融suggest API，覆盖全面）
- 搜索常用港股/美股代码（使用内置数据库，覆盖几十只热门股）
- 支持按名称或代码进行模糊搜索
- 多种输出格式（文本或JSON）

**返回数据字段**:
- `name`: 股票名称
- `code`: 股票代码（格式化后，统一小写）
- `original_code`: 原始代码（6位数字）
- `full_code`: 完整代码（带市场前缀）
- `market`: 市场类型（A股/港股/美股/其他）
- `source`: 数据来源

**数据源说明**:
- **A股**: 新浪金融suggest API - 实时搜索，覆盖全面
- **港股/美股**: 内置数据库 - 覆盖几十只热门股票

## 使用限制

- 所有数据源均为免费公开接口
- 新浪、腾讯接口无需认证
- 建议添加适当的请求间隔，避免频繁请求
- 部分数据可能有延迟（1-3分钟）
- 港股、美股数据可能受限（访问频率、数据完整性）

## 调试和错误处理

脚本已包含基本的错误处理：
- 网络超时: 默认10-15秒
- 数据解析错误: 捕获并跳过无效数据
- 连接失败: 返回空结果或错误信息

如需更详细的日志输出，可修改脚本中的print语句或使用logging模块进行日志记录。