---
name: news-database
description: 独立新闻库（newsdb CLI + SQLite）。新闻采集 agent 负责按 时效性×层级 维护，分析 agent 通过查询端命令读取。包含事件聚合（语义去重）、重要度打标、异动刷新请求队列。
---

# 📰 独立新闻库

Agent 整理后的消息源数据库。核心单元是**事件**（一个持续进展的主题），每条消息强制归属一个事件。入库前由 agent 通过 `newsdb lookup` 做语义去重判断：新进展→归属 / 全新→新建 / 无新信息→跳过。

## 安装（已装则跳过）
```bash
cd <yf-skills>/stock-toolkit/skills/news-database/scripts
uv tool install --editable .
```

## 数据库位置
`STOCK_NEWS_DB` 环境变量指定，默认 `<cwd>/data/news/news.db`。

## 采集端命令（新闻 agent）
| 命令 | 作用 |
|------|------|
| `newsdb init` | 初始化库 |
| `newsdb lookup "关键词" [--entity-type X]` | 入库前查重/查事件归属 |
| `newsdb event <id>` | 查看事件详情/时间线 |
| `newsdb save --new-event/--event <id> ...` | 结构化写入消息 |
| `newsdb update-event <id> --latest-summary "..."` | 刷新事件最新摘要 |
| `newsdb resolve-event <id>` | 标记事件结束 |
| `newsdb track <code> --name ... [--industry] [--watchlist]` | 添加实体跟踪 |
| `newsdb signal-backfill` | 对存量消息回填预期信号（关键词规则，仅补未标注的） |
| `newsdb signal-set <msg_id> --direction bullish/bearish/event/none [--type X]` | 手动修正单条消息的信号标注（agent 复核用） |
| `newsdb ack-refresh <id>` | 处理完异动请求后确认 |
| `newsdb industry-aliases add <行业名> --alias <别名>` | 登记行业别名 |
| `newsdb industry-aliases list [<行业名>]` | 查看行业别名 |
| `newsdb industry-hierarchy set-parent <子行业> --parent <父行业>` | 设行业层级 |
| `newsdb industry-relate <行业A> --to <行业B> --strength 60` | 登记行业关联 |
| `newsdb industry-sync` | 回填现有库（别名/关联/层级） |
| `newsdb scan-status set/get <scope_type> <scope_id>` | 记录/查看扫描时间 |
| `newsdb scan-list` | 列出应扫描的 scope（采集 agent 用） |

> 注：`scan-status` / `scan-list` 与 news-collector skill 共享，采集 agent 按时效性调度扫描时使用。

## 查询端命令（分析 agent）
| 命令 | 作用 |
|------|------|
| `newsdb query-stock <code> [--days N] [--include-low-confidence]` | 该股相关事件 |
| `newsdb query-industry <name> [--days N]` | 该行业相关事件 |
| `newsdb query-market [--days N]` | 宏观/政策/大盘事件 |
| `newsdb important [--min-importance 4] [--days N]` | 高重要度事件 |
| `newsdb research [--entity-type X] [--tag X] [--days N]` | **深度研究列表**（info_type='analysis'，可按对象/标签过滤） |
| `newsdb search "关键词"` | FTS 全文检索 |
| `newsdb refresh-requests [--status pending]` | 读异动刷新请求 |

## 信息性质与标签（2026-08-19 加入）

**`--info-type`（信息性质，正交于 entity_type）**：analysis / news / fact / rumor
- `analysis`=深度研究/分析（逻辑链+判断，需置信度，事后可验证 verdict）
- `news`=新闻快讯（报道发生了什么）｜`fact`=事实公告（财报/中标/监管）｜`rumor`=流言舆情（未经证实，不推动交易）
- 默认 `news`；旧库自动迁移补列

**`--tags`（弹性标签，N 个任意组合）**：写入 event_tags 表，随用随加
- 常用：market-shock / panic-selloff / trend-reversal / rate-shock / semiconductor / high-confidence / oversold-bounce ...
- 查询：`newsdb research --tag rate-shock`；`newsdb event <id>` 显示标签

## 行业成分股与产业链（2026-08-19 加入）

**`newsdb industry-stocks`（行业成分股，行业事件→个股候选的映射层）**：
| 命令 | 作用 |
|------|------|
| `newsdb industry-stocks add --industry X --stock 600879,300034 --relevance 80 --note "火箭测控"` | 添加成分股（relevance：80核心/60受益/40边缘） |
| `newsdb industry-stocks list --industry 商业航天` | 列行业成分股（按相关性排序） |
| `newsdb industry-stocks query --code 600879` | 查某股票属于哪些行业 |

- **维护节奏**：初始灌入（脚本/手工）→ 事件驱动补充（行业事件入库时查无/查少 → agent 搜索补录，发现即补）→ 季度审计（组合审查复查）
- **缺失兜底**：查不到成分股时 agent 现场搜索受益标的，先补录再入队 CANDIDATE，不断链

**`relations` 表（上下游产业链传导）**：`industry→industry`，rel_type=upstream/downstream/related，strength=0-100
- 行业事件传导时沿 relations 扩散到上下游行业 → 查其成分股 → 核心入队 CANDIDATE（strength>=60 才传导）
- 已灌：商业航天→卫星互联网/钢铁/化工、半导体→设备/消费电子、算力→GPU/服务器/液冷/存储、AI→算力/大模型 等 16 条

## 协作端命令
| 命令 | 作用 |
|------|------|
| `newsdb request-refresh <code> --signal "..." [--reason] [--priority]` | 分析 agent 写语义化异动请求 |
| `newsdb ack-refresh <id>` | 新闻 agent 确认处理完成 |

## 退出码契约
| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 目标不存在（event <id> / update-event / resolve-event / ack-refresh 目标不存在） |
| 2 | save 必须且只能指定 --event <id> 或 --new-event 之一 |
| 3 | save --event <id> 指向不存在的事件 |

## 注意
- `newsdb track` 对 `is_watchlist`/`priority` 总是**覆盖**为传入值：重复 track 不带 `--watchlist` 会把已 watchlist 的标的重置为 0。跟踪脚本每次应显式带 `--watchlist`。
- 重要性 `importance` 范围 1-5；相关度 `relevance`/`strength`/`priority` 建议 0-100，超出不校验，由 agent 自律。
- FTS trigram 需 ≥3 字符，2 字符中文查询（如"涨价"）自动回退 LIKE。
- 输出中 `[msg#N]` 是消息 id，可配合 `newsdb event <事件id>` 追溯完整时间线。
- 行业支持别名归一化：`upsert_industry` 先查别名再新建，入库用任意别名不分裂行业。`save --industry` 支持逗号分隔多行业。
- `save --message-type <type>` 标内容类型（10 类：financial_report 财报业绩 / announcement 公告 / news 新闻资讯 / research 研报 / community 社区舆情 / industry_change 行业变化 / capital_flow 资金异动 / price_action 股价走势 / policy 政策 / other 其他），与 `--source-type`（谁说的）正交。采集 agent 入库时都应带。
- **预期信号标注（2026-08 新增）**：`save --signal-direction <bullish/bearish/event>` 标消息的**预期方向**（知情方对未来走势的预期），`--signal-type <buyback/reduction/earnings_preview/win_bid/...>` 标信号类型（与 direction 正交）。**缺省时按标题/摘要关键词自动识别**；只有需要修正时才显式传。语义：
  - **strong-signal 消费方（2026-08）**：`importance≥4 + signal-direction bullish + confidence≥3`（官方/媒体）的事件会被分析 agent 识别为"强消息"，可能触发**消息试探仓提前入场**（建段 5% + 段内试探 5-20%，豁免趋势门的小仓位试探，详见 stock-daily-analysis 纪律文档 3.4 节）。采集时对这类事件**务必**标对 importance/signal-direction/confidence，并保留 source_type 可追溯。
  - bullish 偏多（回购/增持/中标/定增/调研…）；bearish 偏空（减持/质押/业绩暴雷/评级下调/解禁…）；event 事件驱动（预约披露日/分红除权，中性到点复核）；none 无预期。
  - **股价走势（price_action）与 market 类事件不标预期**——它们是已发生的市场描述（"美股重挫"里出现"签署MOU"不应误标 bullish）。`signal-backfill` 同样跳过这两类。
  - 目的：把"预期/先导类信号"结构化，配合技术指标做双层确认（新闻定区域、指标定时点），支撑后续"预期信号→未来5-10日收益"的统计验证。现在只积累，不下结论。
- `query-industry` 支持别名匹配 + 父带子展开（查父行业含子行业事件）；未命中时给出候选提示。
