---
name: paper-trading
description: 模拟盘交易系统，支持 A股、港股和美股的模拟交易，提供独立资金池管理、市场数据查询和新闻获取功能。当用户需要进行股票模拟交易、市场数据查询或获取市场新闻时使用。
---

# 模拟盘交易系统

## 概览

模拟盘交易系统是一个功能完善的虚拟交易平台，支持 A股、港股和美股交易，每个股票有独立的资金池管理，提供完整的交易流程追踪、市场数据查询和市场新闻获取功能。

**安装方式**：
```bash
cd scripts
uv tool install --editable .
```

**要求**：需要先安装 uv 工具：`pip install uv`

## 核心功能

### 交易管理
- **独立资金池**：每只股票享有独立的资金池系统
- **买入/卖出**：支持按股数、按金额交易，自动获取实时价格
- **持仓追踪**：实时跟踪持仓成本、市值和浮动盈亏
- **收益分析**：统计收益率、胜率、盈亏比等指标

### 市场数据
- **实时价格**：查询 A股、港股、美股实时股价
- **K线数据**：获取日K、周K、月K、分钟K线
- **股票搜索**：搜索 A股、港股、美股股票代码
- **市场新闻**：获取财联社、新浪财经、TradingView 市场新闻

### 数据管理
- **导出功能**：支持 JSON、CSV 格式导出
- **投资组合**：统一管理多股票账户和收益统计

## 快速开始

### 基础交易流程

```bash
# 初始化资金池
ptrade init "股票名称" --capital 100000

# 买入股票
ptrade buy "股票名称" --qty 100

# 卖出股票
ptrade sell "股票名称" --qty 50

# 查看完整信息
ptrade info "股票名称"
```

### 市场数据查询

```bash
# 查询实时价格
ptrade fetch-price sh600000

# 查询K线数据
ptrade fetch-kline sh600000 --type day --count 30

# 搜索股票代码
ptrade search 茅台 --limit 5

# 获取市场新闻
ptrade fetch-news --source all --limit 10
```

### 投资组合管理

```bash
# 查看所有账户
ptrade list

# 查看投资组合
ptrade portfolio

# 性能分析
ptrade analyze

# 导出数据
ptrade export --format json
```

## 详细文档

详细的使用指南和功能说明，请查阅 [references/](paper-trading/references/) 目录下的文档：

- **[基础交易操作](paper-trading/references/basic-operations.md)** - 初始化、买入、卖出操作详解
- **[查询命令说明](paper-trading/references/query-commands.md)** - 资金池、持仓、历史、收益查询
- **[投资组合管理](paper-trading/references/portfolio-management.md)** - 多账户管理和性能分析
- **[数据管理](paper-trading/references/data-management.md)** - 数据导出、删除、备份
- **[市场数据查询](paper-trading/references/market-data.md)** - 价格、K线、搜索、新闻功能
- **[交易原则与策略](paper-trading/references/trading-principles.md)** - 交易纪律和决策流程
- **[数据存储结构](paper-trading/references/data-storage.md)** - 数据文件结构和存储机制
- **[常见问题与故障排除](paper-trading/references/troubleshooting.md)** - 问题诊断和解决方案
