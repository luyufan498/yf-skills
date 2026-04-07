# Paper Trading - 模拟盘交易系统

一个功能完善的模拟盘交易系统，支持A股、港股和美股的模拟交易，管理独立资金池和持仓。

## 功能特性

- 📊 **独立资金池管理** - 为每只股票创建独立的资金池
- 💰 **模拟买卖操作** - 支持买入和卖出操作，自动计算盈亏
- 📈 **持仓跟踪** - 实时跟踪持仓成本、市值和浮动盈亏
- 📊 **多种报表** - 持仓报表、操作历史、收益分析
- 💼 **投资组合管理** - 统一管理多股票账户
- 📤 **数据导出** - 支持导出为JSON、CSV格式
- 🔍 **性能分析** - 收益率、胜率、盈亏比等指标
- 📊 **市场数据查询** - 实时价格、K线数据、分时数据、股票代码搜索
- 📰 **市场新闻** - 实时获取财联社、新浪财经、TradingView 市场新闻
- 🎯 **CLI工具** - 友好的命令行交互界面

## 安装

### 使用 uv 安装（推荐）

```bash
cd paper-trading/scripts
uv tool install --editable .
```

### 使用 pip 安装

```bash
cd paper-trading/scripts
pip install -e .
```

## 快速开始

### 1. 初始化资金池

```bash
# 为股票"赛力斯"初始化100,000元资金池
ptrade init "赛力斯" --capital 100000

# 使用股票代码初始化
ptrade init "赛力斯" --capital 100000 --code sh603527

# 强制重新初始化（覆盖现有数据）
ptrade init "赛力斯" --capital 100000 --force
```

### 2. 买入股票

```bash
# 按股数买入
ptrade buy "赛力斯" --qty 100

# 按金额买入
ptrade buy "赛力斯" --amount 5000

# 带备注买入
ptrade buy "赛力斯" --qty 100 --note "首次建仓"
```

### 3. 卖出股票

```bash
# 按股数卖出
ptrade sell "赛力斯" --qty 50

# 全部卖出
ptrade sell "赛力斯" --all

# 带备注卖出
ptrade sell "赛力斯" --qty 50 --note "减仓"
```

### 4. 查询信息

```bash
# 查看资金池状态
ptrade pool "赛力斯"

# 查看持仓
ptrade holdings "赛力斯"

# 查看操作历史
ptrade operations "赛力斯"

# 查看收益报告
ptrade profit "赛力斯"

# 查看投资组合（所有账户）
ptrade portfolio

# 列出所有账户
ptrade list
```

### 5. 性能分析

```bash
# 查看所有账户的收益统计
ptrade analyze
```

### 6. 数据导出

```bash
# 导出单只股票的操作记录为CSV
ptrade export --stock "赛力斯" --format csv

# 导出持仓数据为JSON
ptrade export --stock "赛力斯" --format json

# 导出所有数据为JSON
ptrade export --format json --output backup.json
```

### 7. 删除账户

```bash
# 删除账户（需先清仓）
ptrade delete "赛力斯"

# 强制删除（即使有持仓）
ptrade delete "赛力斯" --force
```

### 8. 市场新闻查询

```bash
# 获取最新新闻（所有来源）
ptrade fetch-news --source all --limit 10

# 只获取财联社新闻
ptrade fetch-news --source cls --limit 5

# 只获取新浪财经新闻
ptrade fetch-news --source sina --limit 5

# 只获取 TradingView 新闻
ptrade fetch-news --source tv --limit 3

# 输出 JSON 格式
ptrade fetch-news --source all --format json
```

### 9. 市场数据查询

```bash
# 查询实时价格（A股、港股、美股）
ptrade fetch-price sh600000
ptrade fetch-price hk00700
ptrade fetch-price GB_AAPL

# 查询K线数据
ptrade fetch-kline sh600000 --type day --count 30
ptrade fetch-kline sh600000 --type week --count 20
ptrade fetch-kline sh600000 --type 5min --count 48

# 搜索股票代码
ptrade search 腾讯 --limit 5
ptrade search 苹果 --limit 5
```

## 命令参考

### 交易命令

#### init - 初始化账户

```bash
ptrade init STOCK_NAME --capital CAPITAL [--code CODE] [--force]
```

#### buy - 买入股票

```bash
ptrade buy STOCK_NAME (--qty QTY | --amount AMOUNT) [--note NOTE]
```

#### sell - 卖出股票

```bash
ptrade sell STOCK_NAME (--qty QTY | --all) [--note NOTE]
```

### 查询命令

#### pool - 查询资金池

```bash
ptrade pool STOCK_NAME
```

#### holdings - 查看持仓

```bash
ptrade holdings STOCK_NAME
```

#### operations - 查看操作历史

```bash
ptrade operations STOCK_NAME
```

#### profit - 查看收益报告

```bash
ptrade profit STOCK_NAME
```

#### portfolio - 查看投资组合

```bash
ptrade portfolio
```

#### list - 列出账户

```bash
ptrade list
```

### 市场新闻命令

#### fetch-news - 获取市场新闻

```bash
ptrade fetch-news [--source SOURCE] [--limit N] [--format FORMAT]

# 来源选项: all (所有), cls (财联社), sina (新浪财经), tv (TradingView)
# 格式选项: pretty (人可读), json

# 示例
ptrade fetch-news --source all --limit 10
ptrade fetch-news --source cls --limit 5 --format json
```

### 市场数据命令

#### fetch-price - 查询实时价格

```bash
ptrade fetch-price <code> [--format pretty|json]

# 示例
ptrade fetch-price sh600000 --format json
ptrade fetch-price hk00700
```

#### fetch-kline - 查询K线数据

```bash
ptrade fetch-kline <code> [--type TYPE] [--count N] [--format pretty|json]

# 类型选项: day, week, month, 5min, 10min, 15min, 30min, 60min

# 示例
ptrade fetch-kline sh600000 --type day --count 30
ptrade fetch-kline sh600000 --type 5min --count 48
ptrade fetch-kline hk00700 --type week --count 20
```

#### search - 搜索股票代码

```bash
ptrade search <keyword> [--limit N] [--format pretty|json]

# 示例
ptrade search 腾讯 --limit 10
ptrade search 苹果 --limit 5
```

### 工具命令

#### analyze - 性能分析

```bash
ptrade analyze
```

#### export - 导出数据

```bash
ptrade export [--stock STOCK] [--format FORMAT] [--output PATH]
```

#### delete - 删除账户

```bash
ptrade delete STOCK_NAME [--force]
```

## 数据存储

数据默认存储在当前工作目录的 `intermediate/股票名称/模拟买卖/` 下：

```
intermediate/
└── 赛力斯/
    └── 模拟买卖/
        ├── account.json      # 账户信息（持仓数据）
        └── operations.json   # 操作记录
```

## 支持的市场

- ✅ A股（上海证券交易所、深圳证券交易所）
- ✅ 港股
- ✅ 美股
- ✅ 市场新闻（财联社、新浪财经、TradingView）

## 数据来源

- **新闻数据**: 财联社电报、新浪财经直播、TradingView
- **股票代码查询**: 新浪财经
- **实时价格**: 腾讯财经、新浪财经
- **K线数据**: 腾讯财经（A股/港股）、Yahoo Finance（美股）

## 开发

### 运行测试

```bash
pytest tests/ -v
```

### 代码格式化

```bash
black paper_trading/ tests/
```

## 许可证

MIT License

## 更新日志

### v1.2.0 (2026-04-07)

重大更新：
- 新增市场新闻功能（ptrade fetch-news 命令）
- 支持三个新闻源：财联社电报、新浪财经直播、TradingView 外媒
- 支持综合搜索和多格式输出
- 新增 NewsItem 和 MarketNews 数据模型
- 新闻获取器包含重试机制、速率限制、时区处理等生产级特性

### v1.1.0 (2024-04-04)

改进版本：
- 修复价格数据获取错误处理
- 优化资金池计算逻辑

### v1.0.0 (2024-04-04)

初始版本发布，包含：
- 基础交易功能
- 资金池管理
- 持仓跟踪
- 报表生成
- 性能分析
- 数据导出
- CLI工具
