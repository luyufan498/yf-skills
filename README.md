# YF Skills - Claude Code Marketplace

这是一个 Claude Code 技能（Skills）的集合项目，包含多个专业化技能，用于扩展 Claude 的能力。

[![Claude Code](https://img.shields.io/badge/Claude%20Code-Marketplace-blue)](https://github.com/luyufan498/yf-skills)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

## 快速开始

### 安装 Marketplace

```bash
# 方法1：通过 GitHub（推荐）
/plugin marketplace add luyufan498/yf-skills

# 方法2：通过完整 URL
/plugin marketplace add https://github.com/luyufan498/yf-skills
```

### 安装单个技能

```bash
# 安装股票市场数据技能
/plugin install stock-market-data@yf-skills

# 安装模拟盘交易系统
/plugin install paper-trading@yf-skills

# 安装 NBL PPT 构建工具
/plugin install nbl-ppt-builder@yf-skills
```

### 查看可用技能

```bash
/plugin list --marketplace yf-skills
```

### 更新 Marketplace

```bash
/plugin marketplace update yf-skills
```

## 项目概述

本项目将常用的工作流程和工具封装成独立的 Claude 技能，提供专业、高效的 AI 辅助开发体验。

## 包含的技能

| 技能名称 | 描述 | 类别 | 安装命令 |
|---------|------|------|---------|
| **stock-market-data** | 中国A股、港股、美股实时股价和历史数据 | finance | `/plugin install stock-market-data@yf-skills` |
| **paper-trading** | 模拟盘交易系统，独立资金池管理 | finance | `/plugin install paper-trading@yf-skills` |
| **stock-daily-analysis** | 股票每日分析，市场新闻聚合 | finance | `/plugin install stock-daily-analysis@yf-skills` |
| **nbl-ppt-builder** | NBL 企业 PPT 构建工具 | productivity | `/plugin install nbl-ppt-builder@yf-skills` |
| **searxng-search** | SearXNG 增强搜索工具 | productivity | `/plugin install searxng-search@yf-skills` |
| **siyuan-notes** | 思源笔记命令行工具 | productivity | `/plugin install siyuan-notes@yf-skills` |
| **gf-finance** | 广发证券工具接口 | finance | `/plugin install gf-finance@yf-skills` |

## 技能详情

### Stock Market Data

中国股市数据查询技能，提供 A股、港股、美股市场的实时数据查询能力。

**支持的市场：**
- A股市场：上海证券交易所(SH)、深圳证券交易所(SZ)、北京证券交易所(BJ)
- 港股市场：香港交易所(HK)
- 美股市场：NASDAQ、NYSE

**核心功能：**
- 实时股价查询（支持多只股票批量查询）
- K线历史数据（日K/周K/月K/分钟K线）
- 分时走势数据
- 市场新闻聚合（财联社、新浪、TradingView）
- 股票代码搜索

**快速使用：**
```bash
python3 scripts/fetch_realtime_stock.py sh600000
python3 scripts/fetch_kline_data.py sh600000 -t day -c 30
python3 scripts/fetch_market_news.py -n 20
```

### Paper Trading

模拟盘交易系统，支持A股和港股的独立资金池管理，提供完整的交易、持仓、分析功能。

**核心功能：**
- 独立资金池管理
- 买入/卖出/撤单交易
- 持仓分析
- 收益统计
- 报表导出

**快速使用：**
```bash
ptrade --init
ptrade --portfolio
ptrade buy sh600000 --quantity 100
```

### Stock Daily Analysis

股票每日分析系统，支持市场新闻聚合和深度分析。

**核心功能：**
- 市场新闻聚合（多源数据）
- 深度分析报告生成
- 历史分析记录管理

### NBL PPT Builder

专门用于构建 NBL 企业 PPT 的 Skill，包含标准模板、配色方案和内容规范。

**核心特性：**
- 5 步构建流程，确保质量符合要求
- 每个内容页由独立子代理完成
- 强调模板多样性，避免重复单调
- 支持生成 HTML、PPTX 和 PDF 格式

### SearXNG Search

使用 SearXNG 实例增强的 Web 和软件包仓库搜索，支持多个搜索引擎和软件包仓库的聚合搜索。

**支持类别：**
- `general` - 通用网页搜索
- `cargo` - Rust crates (crates.io)
- `packages` - 多仓库 (npm, rubygems, hex, docker hub 等)
- `it` - IT/技术资源
- `repos` - 代码仓库
- `q&a` - 问答社区

### Siyuan Notes

思源笔记全功能命令行工具，提供完整的笔记管理功能。

**核心功能：**
- 笔记本管理（列表、创建、删除、重命名）
- 文档操作（创建、读取、更新、移动、删除）
- 块级编辑（更新、追加、移动、删除）
- 内容搜索和 SQL 查询
- 导出为 Markdown/ZIP

**环境配置：**
```bash
export SIYUAN_ENDPOINT=http://127.0.0.1:6806
export SIYUAN_TOKEN=your-api-token
```

### GF Finance

广发证券相关工具和数据访问接口。

## 配置

### Stock Market Data

```bash
# SearXNG 配置（可选）
export SEARXNG_URL=http://your-searxng-server:38080
```

### Siyuan Notes

```bash
# 必需的环境变量
export SIYUAN_ENDPOINT=http://127.0.0.1:6806
export SIYUAN_TOKEN=your-api-token
```

## 管理技能

```bash
# 更新 marketplace
/plugin marketplace update yf-skills

# 更新所有已安装的技能
/plugin update

# 禁用技能
/plugin disable skill-name@yf-skills

# 启用技能
/plugin enable skill-name@yf-skills

# 删除技能
/plugin remove skill-name@yf-skills

# 移除 marketplace
/plugin marketplace remove yf-skills
```

## 贡献

欢迎为技能提供反馈和改进建议！提交 Issue 或 Pull Request。

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 相关链接

- [Claude Code 文档](https://code.claude.com/docs/zh-CN/plugin-marketplaces)
- [GitHub Issues](https://github.com/luyufan498/yf-skills/issues)
- [GitHub Discussions](https://github.com/luyufan498/yf-skills/discussions)

## 技能设计原则

- **简洁是关键**：只添加 Claude 尚未具备的上下文
- **适当自由度**：将特异性级别与任务的脆弱性相匹配
- **渐进式披露**：使用三级加载系统高效管理上下文
