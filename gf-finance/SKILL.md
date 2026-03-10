---
name: gf-finance
description: 广发证券易淘金金融服务，提供沪深股市实时数据分析功能。当用户需要金融数据查询时使用：(1) 龙虎榜分析 - 查看上榜股票、营业部排名、资金流向，(2) 财务对比 - 对比上市公司财务数据（估值、盈利、现金流等五大维度），(3) ETF榜单 - 热门ETF排行榜（涨幅榜、跌幅榜、资金榜、换手榜、关注榜、5日涨跌榜、连涨连跌榜、主力资金榜、净申购榜、溢价率榜等多种榜单），(4) 指数估值 - 沪深指数PE/PB分位与ETF联动分析。适用于投资研究、市场分析和财务数据查询。
---

# 广发证券 MCP 金融服务

## 概览

此技能提供对广发证券易淘金 MCP (Model Context Protocol) 服务器的访问，为投资研究和市场分析提供沪深股市实时数据查询功能。

## 核心能力

### 1. 🔥 龙虎榜分析
沪深股市龙虎榜实时数据分析平台
- **上榜统计**: 实时监控热门股票上榜情况
- **营业部排名**: 机构席位活跃度分析
- **资金流向**: 主力资金进出追踪
- **多时间维度**: 支持1月/3月/6月/12月数据查询
- **历史查询**: 个股上榜历史记录

### 2. 📊 财务对比分析
智能化上市公司财务对比分析工具
- **五维对比**: 估值、盈利、现金流、偿债力、成长性
- **直观展示**: 清晰呈现公司优势和差异点
- **长周期分析**: 多年期财务数据趋势分析
- **行业对比**: 行业Top2指标排行榜
- **趋势分析**: PE/PB历史走势数据

### 3. ⭐ 热门ETF榜单
沪深市场ETF热点排行服务
- **13种榜单**: 涨幅榜、跌幅榜、主力资金榜、换手榜、关注榜、搜索榜、5日涨跌幅榜、连涨连跌榜、5日主力资金榜、净申购榜、溢价率榜
- **一站式发现**: 快速定位资金关注、市场表现突出的ETF产品
- **详细指标**: 涨跌幅、成交量、换手率、资金流入、基金规模、溢价率等

### 4. 📈 沪深指数估值分析
市场指数估值与ETF联动
- **指数估值**: 主流宽基、行业、主题指数PE/PB分位
- **ETF联动**: 指数与相关ETF配对，辅助投资决策
- **决策参考**: 基于估值的投资建议和历史收益数据

## 快速开始

### 基本配置

广发证券 MCP 服务使用 `streamable-http` 方式接入，需要授权 Token：

```json
{
  "mcpServers": {
    "gf_mcp": {
      "type": "streamable-http",
      "url": "https://mcp-api.gf.com.cn/server/mcp/{service}/mcp",
      "headers": {
        "Authorization": "Bearer {your_token}"
      }
    }
  }
}
```

**服务选择**:
- 龙虎榜分析: `{service}` → `lhb`
- 财务对比: `{service}` → `quant`
- ETF榜单: `{service}` → `etf_rank`
- 指数估值: `{service}` → `windmill`

**Token 获取**: 登录广发通 (https://hd.gf.com.cn/bortal/mcp-login.html?channel=volcengine) 自动完成注册和获取

### 使用流程

1. **识别需求**: 确定用户需要的服务类型
2. **选择服务**: 根据需求选择对应的 MCP 服务
3. **构造请求**: 按照服务文档构造适当的参数
4. **执行查询**: 调用对应的工具获取数据
5. **结果呈现**: 将数据转化为用户友好的格式

## 服务详解

### 龙虎榜分析服务

**何时使用**: 用户询问龙虎榜、热门股票、营业部席位、资金流向相关信息

**常用查询场景**:
- "查看今日龙虎榜"
- "哪些股票最近上榜频繁？"
- "华泰证券营业部最近操作了哪些股票？"
- "最近资金流入最多的股票"

**可用功能**:
- `get_outline(plate)` - 获取龙虎榜概括
- `get_daily_stocks(date, market)` - 获取指定日期上榜股票
- `get_stock_detail(code, date, market)` - 获取个股上榜明细
- `get_stock_history(code, market)` - 获取个股上榜历史
- `get_top_stocks(months, page, page_size)` - 获取上榜个股排行
- `get_stock_stats(code, market, months)` - 获取个股统计数据
- `get_dept_stats(dept_id, months)` - 获取营业部统计数据
- `get_calendar(market, month)` - 获取龙虎榜日历

**详细文档**: 参见 [lhb.md](references/lhb.md)

### 财务对比分析服务

**何时使用**: 用户需要对比上市公司财务数据、分析公司基本面

**常用查询场景**:
- "对比茅台和五粮液的财务状况"
- "分析平安银行的盈利能力"
- "查看招商银行的资产负债表"
- "对比两家银行的估值指标"

**核心指标对比**:
- **基本指标**: 市值、PE、PB等估值指标
- **盈利能力**: ROE、ROA、净利率等
- **现金流分析**: 经营活动现金流、自由现金流
- **偿债能力**: 资产负债率、流动比率等
- **成长性分析**: 营收增长率、利润增长率

**可用功能**:
- `get_basic_data(stock_codes)` - 获取基本指标对比
- `compare_indicators(stock_codes, report_type, year)` - 对比指标
- `get_industry_info(stock_codes)` - 获取股票行业信息
- `get_industry_top2(stock_code)` - 获取行业指标前二
- `get_common_report_type(stock_codes)` - 获取公共报告期
- `get_trend(stock_code, cycle)` - 获取PE/PB走势图
- `analyze_profit_ability(stock_code, report_type)` - 盈利能力分析
- `analyze_capital_structure(stock_code, report_type)` - 资本结构分析
- `analyze_cashflow(stock_code, report_type)` - 现金流量分析

**详细文档**: 参见 [quant.md](references/quant.md)

### ETF榜单服务

**何时使用**: 用户需要查询热门ETF、市场指数ETF表现

**常用查询场景**:
- "查看涨幅最大的ETF"
- "最近资金流入最多的ETF"
- "推荐几个科技类ETF"
- "查看债券类ETF表现"

**榜单类型**（通过type参数区分）:
- `type=1` - **涨幅榜**: 按当日涨跌幅排名的ETF
- `type=2` - **跌幅榜**: 按当日跌跌幅排名的ETF
- `type=3` - **换手榜**: 按换手率排名的ETF
- `type=4` - **主力资金榜**: 按主力资金流入排名的ETF
- `type=5` - **搜索榜**: ETF搜索功能
- `type=6` - **关注榜**: 按用户关注度排名的ETF
- `type=7` - **5日涨幅榜**: 按5日累计涨幅排名的ETF
- `type=8` - **5日跌幅榜**: 按5日累计跌幅排名的ETF
- `type=9` - **连涨榜**: 连续上涨的ETF榜单
- `type=10` - **连跌榜**: 连续下跌的ETF榜单
- `type=11` - **5日主力资金榜**: 按5日资金流入排名的ETF
- `type=12` - **净申购榜**: 按净申购金额排名的ETF
- `type=13` - **溢价率榜**: 按溢价率排名的ETF

**可用功能**:
- `get_rise_rank(page, page_size)` - 获取涨幅榜
- `get_fall_rank(page, page_size)` - 获取跌幅榜
- `get_fund_rank(page, page_size)` - 获取主力资金榜
- `get_feature_rank(page, page_size)` - 获取换手榜
- `get_hot_rank(page, page_size)` - 获取关注榜
- `get_five_day_rise_rank(page, page_size)` - 获取5日涨幅榜
- `get_five_day_fall_rank(page, page_size)` - 获取5日跌幅榜
- `get_continual_rise_rank(page, page_size, limit)` - 获取连涨榜
- `get_continual_fall_rank(page, page_size)` - 获取连跌榜
- `get_five_day_fund_rank(page, page_size)` - 获取5日主力资金榜
- `get_net_subscription_rank(page, page_size)` - 获取净申购榜
- `get_premium_rate_rank(page, page_size)` - 获取溢价率榜

**详细文档**: 参见 [etf_rank.md](references/etf_rank.md)

### 指数估值分析服务

**何时使用**: 用户需要分析市场指数估值、寻找指数对应的ETF产品

**常用查询场景**:
- "现在的上证指数估值高吗？"
- "创业板指的PE分位是多少？"
- "跟踪沪深300的ETF有哪些？"
- "哪个行业指数估值较低？"

**分析维度**:
- **估值分位**: PE、PB历史分位分析
- **指数分类**: 宽基指数、行业指数、主题指数
- **ETF联动**: 每个指数对应的可交易ETF产品
- **投资建议**: 基于估值情况的买入卖出建议

**核心工具**: `valuation_windmill_get` - 统一接口获取指数估值榜单数据
- **参数**: `page` (页码，从0开始), `perPage` (每页数量)
- **返回**: 包含指数代码、名称、PE/PB分位、推荐ETF等信息的列表

**详细文档**: 参见 [windmill.md](references/windmill.md)

## 参数说明

### 股票代码格式
- **上海市场**: `SH{6位代码}` (例如: `SH600000`)
- **深圳市场**: `SZ{6位代码}` (例如: `SZ000776`)

### 常用报告期代码
- `12`: 年报
- `13`: 一季报
- `14`: 半年报
- `15`: 三季报

### 时间周期
- `1y`: 1年
- `3y`: 3年
- `5y`: 5年

### 统计月份代码
- `m1`: 1个月
- `m3`: 3个月
- `m6`: 6个月
- `m12`: 12个月

### 市场代码
- `sh`: 上海市场
- `sz`: 深圳市场

## 资源管理

### Scripts/
便捷的 API 调用脚本，封装了所有 MCP 服务的复杂调用逻辑：

- [mcp_client.py](scripts/mcp_client.py) - 基础 MCP 客户端，提供统一的服务访问接口
- [lhb_analysis.py](scripts/lhb_analysis.py) - 龙虎榜分析工具，支持9种龙虎榜数据查询
- [quant_analysis.py](scripts/quant_analysis.py) - 财务对比分析工具，支持17种财务数据查询和分析
- [etf_rank.py](scripts/etf_rank.py) - 热门ETF榜单工具，支持13种ETF榜单查询
- [windmill_analysis.py](scripts/windmill_analysis.py) - 沪深指数估值分析工具，支持指数估值榜单查询
- [demo_all.py](scripts/demo_all.py) - 完整功能演示脚本，展示所有服务的使用方法

**脚本使用示例**:
```bash
# 龙虎榜分析
python3 scripts/lhb_analysis.py top m3 1 10              # 获取近3个月上榜个股排行
python3 scripts/lhb_analysis.py outline lhb            # 获取龙虎榜概括
python3 scripts/lhb_analysis.py daily 20250528 sh        # 获取指定日期上榜股票

# 财务对比分析
python3 scripts/quant_analysis.py basic SH600000        # 获取基本指标
python3 scripts/quant_analysis.py compare SH600000 SZ000776 2023  # 对比指标
python3 scripts/quant_analysis.py industry SH600000     # 获取行业信息
python3 scripts/quant_analysis.py toptwo SZ000776       # 获取行业指标Top2
python3 scripts/quant_analysis.py trend SZ000776 1y     # 获取PE/PB走势

# ETF榜单查询
python3 scripts/etf_rank.py rise 1 20                   # 获取涨幅榜 (type=1)
python3 scripts/etf_rank.py fall 1 20                   # 获取跌幅榜 (type=2)
python3 scripts/etf_rank.py fund 1 20                    # 获取主力资金榜 (type=4)
python3 scripts/etf_rank.py feature 1 20                # 获取换手榜 (type=3)
python3 scripts/etf_rank.py hot 1 20                    # 获取关注榜 (type=6)
python3 scripts/etf_rank.py search 芯片                 # 搜索ETF (type=5)

# 指数估值分析
python3 scripts/windmill_analysis.py list 1 20            # 获取指数估值榜单
python3 scripts/windmill_analysis.py list 1 5            # 获取前5个指数的估值信息

# 综合演示
python3 scripts/demo_all.py                           # 演示所有服务功能
```

### References/
- [lhb.md](references/lhb.md) - 龙虎榜分析完整API文档和使用示例
- [quant.md](references/quant.md) - 财务对比分析完整API文档和工具说明
- [etf_rank.md](references/etf_rank.md) - ETF榜单API参数与用法说明
- [windmill.md](references/windmill.md) - 指数估值分析API参数与用法说明

### Assets/
- [mcp_config.json](assets/mcp_config.json) - MCP服务配置模板（已包含授权Token）

## 重要注意事项

⚠️ **风险提示**: 服务提供的所有数据和分析结果仅供建投资研究参考，不构成任何投资建议或承诺。

⚠️ **数据时延**: 金融数据可能存在时延，现价格以实际交易为准。

⚠️ **服务策略**: 该服务目前限时免费体验，未来广发证券将根据平台规范和业务需求进行必要适时的服务策略调整。

⚠️ **API更新**: 注意修复后的工具名称和参数格式：
  - windmill 使用 `valuation_windmill_get` 统一接口
  - etf_rank 使用 `finance-api_product_etf_rank_get`，通过type参数区分榜单
  - quant 工具名称统一带后缀（如 `common_basic_post`, `analyze_profit_ability_get`）
  - 页码参数: API使用0基页码，脚本内部自动转换

## 应用场景

- **个人投资者**: 快速获取市场数据和资金流向信息
- **专业分析师**: 深度挖掘公司财务数据和行业对比分析
- **机构用户**: 批量数据查询和分析工具
- **金融科技**: API集成和数据服务解决方案

---

**广发证券易淘金 · 智能综合金融服务**
