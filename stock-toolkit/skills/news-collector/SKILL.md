---
name: news-collector
description: 新闻采集 agent——按时效性×层级扫描市场/政策/行业/个股，把新消息增量灌入 newsdb 新闻库。处理分析 agent 的异动刷新请求。用多行业 + 别名归一化。当需要维护新闻库时使用。
---

# 📡 新闻采集 Agent

独立定时运行的新闻采集器，与股票分析任务解耦。它把搜索到的新闻整理成"事件 + 消息"，用 newsdb CLI 增量写入新闻库。分析 agent 从库里读，不再自己深搜。

## 核心循环

```
读 refresh-requests（分析 agent 的异动请求，优先处理）
→ 按时效性×层级扫描（见 scan_rules.md）
→ 每条新闻：newsdb lookup 查重 → 归属已有事件 / 新建事件 / 跳过重复
→ newsdb save 写入（多行业 + 别名归一化）
→ 更新事件 latest_summary / 标记 resolved
→ newsdb scan-status set 记录本次扫描
→ newsdb ack-refresh 确认处理完的请求
```

## 环境

```bash
export STOCK_NEWS_DB=/home/catmouse/Github_Project/daily-stock-workspace/data/news/news.db
```

## 扫描前

1. `newsdb refresh-requests --status pending` 读异动请求——**优先处理这些**，用 signal 做搜索词。
2. `newsdb scan-list` 列出**所有应扫描的 scope**（从库拉取：stocks 表 watchlist 优先 + industries 表 + market/global + policy/global）。**新加股票/行业自动出现在清单里。**
   > L3 观察窗实体由组合审查负责 `newsdb track --watchlist 1` 标记，标记后自动进入本扫描清单——观察窗股票与正式池（L1/L2）一样每日采集新闻。
3. 对每个 scope 判断是否到期（见 scan_rules.md）：
   - `newsdb scan-status get <scope_type> <scope_id>` 查上次扫描
   - high（market/stock 异动）→ 上次扫描距今 > 8 小时就扫
   - medium（industry/policy）→ 上次扫描距今 > 5 天就扫
   - low（一次性事件）→ 仅在相关事件发生时扫
   - **从未扫描的 scope（scan-status 返回"未扫描"）→ 立即扫**（新加的股票/行业）
   > 注：每次 scan-status get 查询 + 判断是否到期。实际到期判断由你按上述时间间隔估算（scan_due 逻辑的简化版）。

## 入库规则（关键）

**搜索工具链（快讯层 + searxng 优先 + brave 备用）：**

0. **快讯层（每轮扫描先跑，零成本实时电报）**：`ptrade2 fetch-news`（本地命令，不耗搜索配额）：
   - `ptrade2 fetch-news -s tv -n 20`（TradingView 外媒快讯）
   - `ptrade2 fetch-news -s sina -n 20`（新浪财经公告/快讯，实时电报级）
   - 每条：`newsdb lookup` 查重 → 重要消息（个股/行业相关，重要度≥3）→ `newsdb save` 入库（归属已有事件或新建）
   - 财联社 cls 源：接口需 sign/cookie（50101 风控），**暂不可用跳过**（路径已修复 /v1/nodeapi/telegraphList）
   - 快讯层价值：搜索 API 收录有延迟，快讯立即可拿（A股公告/电报级消息）

1. **searxng（优先，免费）**：`searx-bash "<查询>" --time-range day/week/month/year`
   - 按时效性传 `--time-range`：high（大盘/个股异动）→ `day`；medium（行业/政策）→ `week`；low（财报）→ `month`
2. **brave-search（备用，遇验证码/限流/无结果时切换）**：已配 API key（`BRAVE_SEARCH_API_KEY`），月 1000 次调用，用 `brave-search-skills:news-search`
   - 新闻搜索 curl 示例：
     ```bash
     curl -s "https://api.search.brave.com/res/v1/news/search" \
       -H "Accept: application/json" -H "X-Subscription-Token: ${BRAVE_SEARCH_API_KEY}" \
       -G --data-urlencode "q=赛力斯" --data-urlencode "freshness=pd" --data-urlencode "count=20"
     ```
   - `freshness` 按时效性映射：high → `pd`（过去24h）；medium → `pw`（本周）；low → `pm`（本月）
   - 也可用 skill 方式调用：`Skill brave-search-skills:news-search -q "<查询>"`
3. **甄别**：返回结果仍可能混旧闻——数字与库中矛盾 / 日期不在窗口 / 同公告重复 → 跳过或归属已有事件

**每条新闻，用 newsdb 命令按以下步骤处理：**
1. `newsdb lookup "<关键词>"` 查重 → 返回已有事件候选
   - 有新进展 → `newsdb save --event <id> ...` 追加消息
   - 全新主题 → `newsdb save --new-event ...` 新建事件
   - 无新信息 → 跳过（不新增）
2. **多行业关联（重要）**：`--industry "行业A,行业B"` 逗号分隔，关联涉及的所有行业。
3. **用别名/层级归一化**：行业名用常见的叫法即可（`upsert_industry` 会自动归一化）；遇到"液冷"这种别名会自动映射到规范行业。发现已知行业的新叫法，用 `newsdb industry-aliases add <行业> --alias <新叫法>` 登记。
4. **importance 分级**：重大利空/利好 5，重要经营变化 4，一般信息 3，行业背景 2，无关 1。
4.5. **message_type 内容类型**：每条消息标内容类型（说的是什么），与 source_type（谁说的）正交：
   - financial_report 财报业绩（预亏/预增/中报/营收净利）
   - announcement 公告（回购/减持/定增/中标/解禁）
   - news 新闻资讯（行业新闻/公司动态/媒体报道）
   - research 研报（机构评级/目标价/深度报告）
   - community 社区舆情（论坛讨论/股吧/雪球评论）
   - industry_change 行业变化（政策/技术迭代/竞争格局/供需）
   - capital_flow 资金异动（龙虎榜/主力资金/北向）
   - price_action 股价走势（涨跌/异动/K线）
   - policy 政策（监管/产业政策）
   - other 其他
   CLI: `newsdb save --message-type <type> ...`
5. **summary 精炼**：保留关键数字（销量、金额、百分比），一句话说清影响。
5.5. **预期信号标注（自动 + 可选覆盖）**：`save` 缺省会按标题/摘要关键词**自动识别** `--signal-direction`（bullish/bearish/event/none）与 `--signal-type`（buyback/reduction/earnings_preview/win_bid/...），无需手动传。以下情况**显式传参覆盖**：
   - 关键词识别方向明显错误时（如"增资扩股"其实是 H1 预亏背景下的一次性增利）；
   - 舆情/流言给出明确多空倾向时（`--signal-direction bullish/bearish` 让社区情绪进入预期层）；
   - `--signal-direction none` 可禁用自动识别（纯事后描述）。
   > 目的：把"预期/先导类信号"结构化，配合技术指标双层确认。股价走势（price_action）与 market 类事件天然不标预期，勿手动补标。
6. **时效性标签**：`--sensitivity high/medium/low`——市场/大盘/个股异动 high，行业趋势/政策 medium，季度财报/一次性事件 low。

## 时效性×层级扫描规则

见 `references/scan_rules.md`。简要：**high 每轮扫，medium 5 天扫一次，low 事件触发扫**。

## 完成后

- `newsdb scan-status set <scope_type> <scope_id>` 记录本次扫描。
- `newsdb ack-refresh <id>` 确认处理完的请求。
- 总结：新建 X 事件、追加 Y 消息、跳过 Z、处理请求 N 个。

## 候选事件（task-bus 接线）

扫描中发现**值得关注但不在跟踪范围**的新标的/行业（重要度≥4 且 bullish 信号，或出现新题材主线）时，写入任务总线让心跳 agent 安排完整分析：

```bash
export STOCK_TASKS_DB=/home/catmouse/Github_Project/daily-stock-workspace/data/tasks/tasks.db
taskbus add CANDIDATE <代码或行业名> --source news-collector --priority 2 \
  --payload '{"evidence":"<一句话依据>","importance":4}'
```

心跳 agent 消费 CANDIDATE → 完整分析 → 评估关注/买入。已在 stocks 跟踪或 watchlist 的标的无需重复入队。
