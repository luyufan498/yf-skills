# 时效性×层级扫描规则

按 **时效性**（多久会变）× **层级**（属于哪类）双维度决定扫描频率。

## 时效性

| 时效性 | 特征 | 扫描频率 | 触发方式 | 搜索 time-range |
|--------|------|----------|----------|----------------|
| high | 每日变 | 每天 1-3 次 | 定时轮询 | `--time-range day` |
| medium | 数周变 | 5 天一次 | 事件触发 + 兜底 | `--time-range week` |
| low | 固定/季度 | 事件触发 | 仅在相关事件发生时 | `--time-range month` |

## 搜索时间范围（重要）

**searxng（优先，免费）**：用 searx-bash 搜索时**必须**按时效性传 `--time-range`，避免历史旧闻混入：

```bash
# high 时效性（大盘/个股异动）：只看今天
searx-bash "赛力斯 新闻" --time-range day

# medium（行业趋势/政策）：看本周
searx-bash "数据中心液冷 政策" --time-range week

# low（财报/一次性事件）：看本月
searx-bash "中科曙光 中报" --time-range month
```

**brave-search（备用，searxng 遇验证码/限流/无结果时切换）**：已配 API key（月 1000 次），freshness 按时效性映射：
```bash
# high → pd（过去24小时）
curl -s "https://api.search.brave.com/res/v1/news/search" \
  -H "Accept: application/json" -H "X-Subscription-Token: ${BRAVE_SEARCH_API_KEY}" \
  -G --data-urlencode "q=赛力斯" --data-urlencode "freshness=pd" --data-urlencode "count=20"
# medium → pw（本周）/ low → pm（本月）
```
也可用 skill 方式：`Skill brave-search-skills:news-search -q "<查询>"`。注意控制用量（月 1000 次），searxng 可用时优先用它。

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

## 扫描清单来源（重要）

**每次采集前，用 `newsdb scan-list` 拉取所有应扫描的 scope**，从库动态生成：

| scope_type | 来源 | 说明 |
|-----------|------|------|
| stock | `stocks` 表 | watchlist 优先，新 track 的自动出现 |
| industry | `industries` 表 | 新 upsert 的行业自动出现 |
| market | 固定 `market/global` | 大盘/成交/资金流 |
| policy | 固定 `policy/global` | 宏观/政策 |

**新加股票/行业 → 自动进扫描清单**（scan-list 从库拉），scan_log 无记录 → "未扫描" → 立即扫。无需手动登记。

## 到期判断

对 scan-list 返回的每个 scope，用 `newsdb scan-status get` 查上次扫描，按时效性判断：

- **high**（market/stock 异动）→ 距上次 > 8 小时到期
- **medium**（industry/policy）→ 距上次 > 5 天到期
- **low**（一次性事件）→ 仅在相关事件发生时扫（不强制定期）
- **未扫描**（新加的 scope）→ 立即扫

## 每轮扫描职责（按时效性重点）

**晨间（08:00）**：
- high：隔夜外盘、政策盘前、A股盘前要点、watchlist 个股 + 行业隔夜消息
- medium：检查是否 5 天没扫的行业/政策主题（scan-status get 判断）
- 额外：行业+政策关键词快检（searxng `--time-range day`），发现新动态就入库

**盘中（11:30）**：
- high：市场热点、板块轮动、watchlist 个股盘中异动

**收盘（15:30）**：
- 当日重要消息汇总、更新事件 latest_summary、标记结束事件 resolved

## 一次性/临时性事件触发

行业新闻/政策/大趋势这类 medium/low 的临时性更新，**不靠定期轮询**，靠主动触发：
1. **分析 agent 推送**：分析时发现行业/政策/大趋势重要变化 → `newsdb request-refresh`（reason 标"行业政策"）→ 采集 agent 优先处理。
2. **每轮快检**：晨间轮额外用 searxng 搜行业+政策关键词（`--time-range day`），发现新动态就入库/更新，不等 5 天。

## scan-status 用法

每扫完一个 scope，`newsdb scan-status set <scope_type> <scope_id>`。
下次用 `newsdb scan-status get` + 上方到期判断决定是否再扫。
