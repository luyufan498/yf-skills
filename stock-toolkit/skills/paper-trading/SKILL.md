---
name: paper-trading
description: 模拟盘交易系统，支持 A股、港股和美股的模拟交易，提供独立资金池管理、市场数据查询和新闻获取功能。当用户需要进行股票模拟交易、市场数据查询或获取市场新闻时使用。
---

# 模拟盘交易系统

## ⚡ ptrade2（V2 弹性组合总池，推荐）

> **2026-08-10 起 ptrade2 已完整上线**：命令面与 v1 完全对齐（35 命令），SQLite 深迁移存储 + 弹性组合总池 + 三档策略。旧 ptrade v1 保留但仅用于回退。

**核心区别**：
- **存储**：`ptrade2` 用 SQLite（`master_pool.db`）单一事实源，取代 v1 的每账户 JSON 文件。命令 `ptrade2` 与 v1 命令同名同参，直接替换前缀即可。
- **资金模型**：v1"每只股票独立资金池互不相通" → v2"**一个总池 1000 万 + 按股分配/释放**"。池 ≠ 持仓 ≠ 预算三者解耦。
- **三档策略**：L1 人工锁定（AI 无权 release）/ L2 稳健（agent 自主+三重释放条件）/ L3 投机（agent 完全自由，空仓即剔）。当前 8 股全部 L2。

**快速开始（ptrade2）**：
```bash
# 总池状态（每次交易/审查先看）
ptrade2 master-pool-show              # total/free/占用率/活跃段/已实现盈亏

# 池名单
ptrade2 watchlist-list                # 三档名单
ptrade2 watchlist-add 股票 --strategy L2 --source agent --reason 依据
ptrade2 watchlist-add 股票 --strategy L1 --source manual --reason 人工锁定  # L1 必须 manual

# 开持仓段（从 free 拨预算建账户）——替代 v1 的 ptrade init
ptrade2 master-pool-allocate 股票 --amount 200000 --reason 右侧建仓

# 段内注资（买入缺口时）
ptrade2 master-pool-topup 股票 --amount 50000 --reason 补弹药

# 关段回池（空仓后，7 日冷却）
ptrade2 master-pool-release 股票 --reason 空仓释放

# 交易命令与 v1 同名（buy/sell/conditions/atr-sync/check-triggers/fetch-* 全部可用）
ptrade2 buy 股票 --qty 100
ptrade2 atr-sync 股票
ptrade2 check-triggers 股票

# 历史迁移（一次性，把 v1 JSON 账户归档为 closed 段）
ptrade2 migrate-existing
```

详细文档：[弹性组合总池（V2）](references/elastic-master-pool.md)、[V2 资金纪律](references/trading-principles.md)

---

## 概览（v1 兼容说明）

> 以下为 v1 `ptrade` 的说明。功能命令与 ptrade2 一致，但存储为每账户独立 JSON、资金互不相通。ptrade2 已上线后，**新操作请用 `ptrade2`**。

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
- **操作历史**：查看所有交易记录和资金变动详情
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

> 以下列出常用命令，完整命令列表请使用 `ptrade --help` 查看。

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

# 多周期市场趋势汇总 (月K/周K/日K/分时)
ptrade market-summary sh600000

# 搜索股票代码
ptrade search 茅台 --limit 5

# 获取市场新闻
ptrade fetch-news --source all --limit 10
```

### 查看操作历史

```bash
# 查看单只股票的操作历史
ptrade operations "股票名称"

# 查看所有股票的操作历史
ptrade operations
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

### 条件管理（交易纪律）

```bash
# 查看当前条件
ptrade conditions "股票名称" --format markdown --template all

# 设定标准条件（5种固定类型，会覆盖同类型旧条件）
ptrade conditions "股票名称" --action set --type trailing_stop --price 65.0 --action-str "减仓50%" --category hard

# 设定事件条件（支持同类型多实例，不覆盖）
ptrade conditions "股票名称" --action event-set --event-type loss_protect --price 68.6 --action-str "减仓20%" --category hard

# 查看事件条件列表
ptrade conditions "股票名称" --action event-list

# 移除事件条件
ptrade conditions "股票名称" --action event-remove --event-id XXX
```

详细文档：[条件管理](references/conditions.md)

### ATR 止损同步与触发检测

```bash
# 同步 ATR 动态止损（trailing_stop = peak−2.5×ATR，cost_protection = 成本−2×ATR）。持仓时每日跑
ptrade atr-sync "股票名称"              # 算 ATR(14)+更新 peak+同步止损位（只升不降）
ptrade atr-sync "股票名称" --dry-run    # 只算不写，预览止损位变化
ptrade atr-sync "股票名称" --reset-peak # 重新建仓后重置 peak 为当前价（不继承上一轮）

# 检测现价是否已跌破硬条件触发价（只读，不自动卖出）
ptrade check-triggers "股票名称"        # 对比实时价与所有硬条件，返回已破位清单；有破位退出码 1
ptrade check-triggers                   # 省略股票名则遍历所有持仓账户
```

> ⚠️ `conditions --template trigger-table` 的"未触发/已触发"只反映**手动标记**，不反映实时破位——止损位设了必须跑 `check-triggers` 才知道有没有被跌破。详见 [交易纪律](references/trading-principles.md) 规则 2.2/2.5。

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
| 条件管理 | [条件管理](references/conditions.md) |
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
| **条件管理** | [conditions.md](references/conditions.md) | 标准条件、事件条件、纪律规则 |
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
