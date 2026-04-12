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
# 查询实时价格 (⚠️ 使用股票代码，如 sh600000、sz300731)
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

### 分析报告管理

```bash
# 保存分析报告
ptrade analysis 赛力斯 --action save --file report.md

# 查看最新分析
ptrade analysis 赛力斯 --action read

# 列出分析历史
ptrade analysis 赛力斯 --action list

# 查看所有已分析的股票
ptrade analysis all --action list
```

详细文档：[分析报告管理](references/analysis.md)

### 临时数据存储

支持的数据类别：
- `deep-search`: 深度搜索结果
- `history-continuity`: 历史连续性分析
- `gf-summary`: 广发证券摘要

```bash
# 保存临时数据（从文件读取）
ptrade temp-data 赛力斯 --action save --category deep-search --file search_result.md

# 保存临时数据（从 stdin 读取）
ptrade temp-data 赛力斯 --action save --category gf-summary --stdin << 'EOF'
# 广发证券数据分析
...
EOF

# 读取最新数据
ptrade temp-data 赛力斯 --action read --category deep-search
```

详细文档：[临时数据存储](references/temp-data.md)

## 使用指南

对于详细的功能说明、参数详解和最佳实践，请查阅 [references/](references/) 目录下的参考文档。

### 按场景查阅

| 场景 | 参考文档 |
|------|---------|
| 首次使用 | [基础交易操作](references/basic-operations.md) |
| 分析报告管理 | [分析报告管理](references/analysis.md) |
| 查看数据 | [查询命令说明](references/query-commands.md) |
| 管理多个账户 | [投资组合管理](references/portfolio-management.md) |
| 备份数据 | [数据管理](references/data-management.md) |
| 临时数据存储 | [临时数据存储](references/temp-data.md) |
| 市场数据分析 | [市场数据查询](references/market-data.md) |
| 交易策略 | [交易原则与策略](references/trading-principles.md) |
| 理解数据结构 | [数据存储结构](references/data-storage.md) |
| 解决问题 | [常见问题与故障排除](references/troubleshooting.md) |

### 按功能查阅

| 功能类别 | 参考文档 | 说明 |
|---------|---------|------|
| **基础交易** | [basic-operations.md](references/basic-operations.md) | 初始化、买入、卖出操作 |
| **分析报告** | [analysis.md](references/analysis.md) | 保存、读取、查询分析报告 |
| **数据查询** | [query-commands.md](references/query-commands.md) | 资金池、持仓、历史、收益 |
| **多账户管理** | [portfolio-management.md](references/portfolio-management.md) | 投资组合、性能分析 |
| **数据备份** | [data-management.md](references/data-management.md) | 导出、删除、恢复 |
| **临时数据** | [temp-data.md](references/temp-data.md) | 中间数据存储、读取、管理 |
| **市场数据** | [market-data.md](references/market-data.md) | 价格、K线、搜索、新闻 |
| **交易策略** | [trading-principles.md](references/trading-principles.md) | 纪律、止盈止损 |
| **数据机制** | [data-storage.md](references/data-storage.md) | 文件结构、存储原理 |
| **故障排除** | [troubleshooting.md](references/troubleshooting.md) | 22个常见问题诊断 |

### Agent 使用建议

当需要详细说明时，请：

1. **先查阅对应参考文档** - 每个功能都有专门的详细文档
2. **理解核心机制** - [数据存储结构](references/data-storage.md) 解释了数据如何组织和计算
3. **遵循交易原则** - [交易原则与策略](references/trading-principles.md) 提供最佳实践
4. **遇到问题查阅** - [常见问题与故障排除](references/troubleshooting.md) 包含典型问题解决方案

**不要**在 SKILL.md 中查找详细的参数说明、错误处理或实现细节，这些都在参考文档中。
