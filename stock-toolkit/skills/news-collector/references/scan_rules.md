# 时效性×层级扫描规则

按 **时效性**（多久会变）× **层级**（属于哪类）双维度决定扫描频率。

## 时效性

| 时效性 | 特征 | 扫描频率 | 触发方式 | 搜索 time-range |
|--------|------|----------|----------|----------------|
| high | 每日变 | 每天 1-3 次 | 定时轮询 | `--time-range day` |
| medium | 数周变 | 5 天一次 | 事件触发 + 兜底 | `--time-range week` |
| low | 固定/季度 | 事件触发 | 仅在相关事件发生时 | `--time-range month` |

## 搜索时间范围（重要）

用 searx-bash 搜索时**必须**按时效性传 `--time-range`，避免历史旧闻混入：

```bash
# high 时效性（大盘/个股异动）：只看今天
searx-bash "赛力斯 新闻" --time-range day

# medium（行业趋势/政策）：看本周
searx-bash "数据中心液冷 政策" --time-range week

# low（财报/一次性事件）：看本月
searx-bash "中科曙光 中报" --time-range month
```

**甄别原则**：即使加了 time-range，返回结果仍可能混入旧闻（搜索引擎索引延迟）。判断标准：
- 结果内容里的数字/事实与库中现有事件**矛盾**（如销量、业绩数字对不上）→ 很可能旧闻，跳过
- 发布日期不在预期窗口 → 跳过
- 与库里已有事件是同一件事（同公告/同新闻）→ 归属已有事件，不重复新建

## 层级 → 典型时效性

| 层级 | 例子 | 默认时效性 |
|------|------|-----------|
| market | 大盘、成交、资金流 | high |
| policy | 国务院/证监会/行业政策 | medium |
| industry | 行业趋势、竞争格局 | medium |
| stock | 个股异动、公告 | high |
| stock | 季度财报、一次性事件 | low |

## 每轮扫描清单（按时效性）

**晨间（08:00）**：
- high：隔夜外盘、政策盘前、A股盘前要点、watchlist 个股 + 行业隔夜消息
- medium：检查是否 5 天没扫的行业/政策主题（scan-status get 判断）

**盘中（11:30）**：
- high：市场热点、板块轮动、watchlist 个股盘中异动

**收盘（15:30）**：
- 当日重要消息汇总、更新事件 latest_summary、标记结束事件 resolved

## scan-status 用法

每扫完一个 scope，`newsdb scan-status set <scope_type> <scope_id>`。
下次用 `newsdb scan-status get` + `scan_due` 判断是否到期。
