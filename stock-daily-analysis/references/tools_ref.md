# 🛠️ 工具使用参考手册

本文档汇总股票深度分析任务中涉及的所有工具及其使用方法。

---

## analysis_manager

分析报告管理器，用于保存、读取和管理股票分析结果。

### 核心命令

```bash
# 保存报告
python3 scripts/analysis_manager.py save "<股票名>" --stdin

# 查看历史
python3 scripts/analysis_manager.py history "<股票名>"

# 读取最新
python3 scripts/analysis_manager.py read "<股票名>"

# 查看持仓
python3 scripts/analysis_manager.py holdings "<股票名>" --current-price <当前价>
```

### 进阶功能

```bash
# 设置持仓
python3 scripts/analysis_manager.py set "<股票名>" --price <成本价> --qty <数量> --note "备注"

# 记录买入
python3 scripts/analysis_manager.py buy "<股票名>" --price <价格> --qty <数量> --note "备注"

# 记录卖出
python3 scripts/analysis_manager.py sell "<股票名>" --price <价格> --qty <数量> --note "备注"
```

---

## stock-market-data (独立 skill)

实时股价和 K 线数据获取。

### 命令

```bash
# 实时股价
python3 ~/.openclaw/skills/stock-market-data/scripts/fetch_realtime_stock.py "<股票代码>"

# K 线数据
python3 ~/.openclaw/skills/stock-market-data/scripts/fetch_kline_data.py "<股票代码>" -t day -c 30
```

### 股票代码格式

- A 股：`sh601127` 或 `sz000001`
- 港股：`hk00700`
- 美股：`US.AAPL` 或 `AAPL`

---

## searxng-search-skill (独立 skill)

深度搜索引擎（优先使用）。

```bash
# 搜索新闻
~/.openclaw/skills/searxng-search-skill/scripts/searx-bash "股票名 新闻"
```

**搜索服务器**：`http://192.168.100.2:38080`

---

## brave-search (内置)

备用搜索引擎（当 searxng 遇到验证码时使用）。

---

## gf-finance (广发证券 SKILL)

广发证券专业数据获取。

### 工作目录

```bash
cd /home/catmouse/Github_Project/yf-skills/gf-finance
```

### 龙虎榜分析

```bash
# 检查今日龙虎榜
python3 scripts/lhb_analysis.py daily 20260310 sh
python3 scripts/lhb_analysis.py daily 20260310 sz

# 获取个股上榜详情
python3 scripts/lhb_analysis.py stock 601127 20260310 sh
```

### 财务数据

```bash
# 获取基本指标
python3 scripts/quant_analysis.py basic SH601127

# 获取行业信息
python3 scripts/quant_analysis.py industry SH601127

# 获取 PE/PB 走势
python3 scripts/quant_analysis.py trend SH601127 1y
```

### ETF 榜单

```bash
# ETF 涨幅榜
python3 scripts/etf_rank.py rise 1 20

# ETF 主力资金榜
python3 scripts/etf_rank.py fund 1 20

# ETF 搜索
python3 scripts/etf_rank.py search 芯片
```

### 股票代码格式

- 上海：`SH601127` (必须大写 SH 前缀)
- 深圳：`SZ000776` (必须大写 SZ 前缀)

---

## 注意事项


### 数据验证

- 如股价数据获取失败，在报告中标注"数据暂不可用"
- 如广发数据获取失败，检查 Token 有效性

---

**创建时间**: 2026-03-26
**用途**: 股票分析任务中各工具使用参考
