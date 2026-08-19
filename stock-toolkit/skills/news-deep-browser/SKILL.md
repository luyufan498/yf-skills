---
name: news-deep-browser
description: 深度浏览补充采集 agent——用裸 CDP 驱动真实 Chrome(9222+登录态) 访问雪球/知乎/X，补充主搜索路线够不到的信息(提前量/情绪流言/全局异动/国际AI科技情报)，带置信度标签写入 newsdb。与 news-collector 完全解耦，挂了不影响主路线。当需要深挖论坛/社交/登录墙/外网内容时使用。
---

# 🌐 深度浏览补充采集 Agent

独立运行的补充采集器，与主新闻采集(news-collector)完全解耦。用裸 CDP 驱动真实 Chrome(9222) 访问雪球/知乎/X，把搜索引擎够不到的信息带置信度写入 newsdb。无 agent-browser、无 daemon，session 状态 = 真实 Chrome 的 tab 本身。

## 核心循环

```
读 deepdive-requests(分析 agent 的深挖请求，优先处理)
→ 从库读深挖目标(高关注+open事件股票/异动股)
→ 每个目标：查库已有内容 → 决定去哪找(雪球讨论/资讯/7×24、知乎) → 裸 CDP 脚本逛 → 甄别
→ newsdb save 写回(source_type + confidence)
→ ack-deepdive 确认处理完的请求
```

## 环境(关键，别踩坑)

```bash
export STOCK_NEWS_DB=/home/catmouse/Github_Project/daily-stock-workspace/data/news/news.db
export DIVE_SCRIPTS=/home/catmouse/Github_Project/yf-skills/stock-toolkit/skills/news-deep-browser/scripts
export DIVE_SESSION_FILE=/tmp/news_deep_browser_tabs.json   # 记录本轮 dive 开的 tab，收尾 dedupe 用

# 裸 CDP 驱动真实 Chrome(9222 登录态最完整)。若连不上，先确认 Chrome 以 --remote-debugging-port=9222 启动
python3 "$DIVE_SCRIPTS/cdp_drive.py" list
# 知乎开工前先做"保登录清指纹"避免问题页 40362
python3 "$DIVE_SCRIPTS/zh_cookie_clean.py"
```

## 操作(优先用封装脚本)

| 目标 | 命令 | 输出 |
|---|---|---|
| 雪球单股深挖(讨论+翻页+资讯) | `python3 "$DIVE_SCRIPTS/xq_dig.py" <代码> --pages 2` | JSON：`{captcha, pages[], news}` |
| 雪球 7×24 快讯 | `cdp_drive.py new "https://xueqiu.com/today#/livenews"` → `read <tid> --site xueqiu-livenews` | — |
| 雪球今日话题 | `cdp_drive.py new "https://xueqiu.com/today"` → `read <tid>` | — |
| 知乎搜索(中期前瞻) | `python3 "$DIVE_SCRIPTS/zh_search.py" "预计XX"` | JSON：`{count, results[]}` |
| 知乎问题页 | 先 `zh_cookie_clean.py`，再 `cdp_drive.py navigate <tid> <url>` + `read <tid>` | — |
| X 推荐流(For you+趋势) | `python3 "$DIVE_SCRIPTS/x_scan.py" --both` | JSON：home.tweets + explore.trends |
| X 定向搜索 | `python3 "$DIVE_SCRIPTS/x_search.py" "AI chip" --f live --since 2026-08-09 --min-faves 50` | JSON：tweets[{author,text,url}] |
| 手动底层命令 | `python3 "$DIVE_SCRIPTS/cdp_drive.py" <cmd>`：list/new/close/dedupe/clean/navigate/eval/read/click/scroll/wait/paginate/captcha/title | — |

`xq_dig.py` 一条命令完成雪球单股：开页 → 过滑块(如有) → 读讨论 → 翻页到 N → 点资讯 tab。读出的 JSON 直接甄别入库。

## 深挖目标来源(两路合并)

1. `newsdb deepdive-requests --status pending`——分析 agent 插队，**优先处理**
2. 从库拉：`newsdb scan-list` 的 stock scope 中**高关注 + 有 open 事件**的 + 异动股(非机械扫全部 watchlist)

如何判断高关注：看该股是否有 open 事件且 importance 高（`newsdb query-stock <code>` 查事件状态）；异动股看近期涨跌（可用行情数据）。

每轮限量：最多深挖 5 个目标，按 deepdive 请求 > MARKET_SHOCK 大盘异动 > 异动事件 > 高关注股票顺序。

## 📉 MARKET_SHOCK 大盘异动社区声音收集（2026-08-19 加入）

当 taskbus 有 `MARKET_SHOCK` 事件（大盘单日大跌，心跳触发深度研究）时，**本 agent 优先级最高**——收集社区声音补充深度研究的情绪面：

1. **雪球 7×24 + 今日话题**：`cdp_drive.py new "https://xueqiu.com/today#/livenews"` 看实时舆情；今日话题看多空焦点
2. **雪球个股讨论**：对池内**重挫股**（跌幅榜前 5）跑 `xq_dig.py <代码> --pages 2`——投资者是"恐慌割肉"还是"错杀抄底"？
3. **知乎搜索**：`zh_search.py "A股 大跌 原因"` / `"半导体 暴跌"`——找逻辑分析/前瞻帖
4. **X/外网**：美股期货、亚太市场关联帖（`x_scan` 词表命中）
5. **甄别标注**：舆情 confidence <3 仅背景参考，不推动交易结论；区分"恐慌情绪"与"基本面利空"——社区是否在找"错杀标的"往往比找"利空原因"更能指示后续走向

> **与 news-collector 的分工**：news-collector 负责**事实层**（官方/媒体说了什么），本 agent 负责**情绪层**（投资者在想什么）。两者合并 = 深度研究的完整逻辑链条。产出写 newsdb（source_type=community）+ 供消费 agent 汇总。

## 渠道策略(实测确认)

| 目标 | 去哪找 | 做法 |
|---|---|---|
| 个股舆情/流言 | 雪球讨论区 | `xq_dig.py <代码>` 新帖+热帖排序 |
| 个股媒体新闻 | 雪球资讯 tab | `xq_dig.py` 自动切"资讯"，一次读全，来源+时间戳 |
| 全局实时异动 | 雪球 7×24 | `https://xueqiu.com/today#/livenews`，发现非 watchlist 新题材 → request-refresh |
| 市场焦点 | 雪球今日话题 | `https://xueqiu.com/today` |
| 中期前瞻 | 知乎 | `zh_search.py "预计XX/评价XX"` 前瞻型问题 |
| 国际AI/科技情报 | X | `x_scan.py` 推荐流 + `x_search.py` 定向搜(芯片涨价/HBM/大模型/新用法) |
| 排除 | 小红书 | IP 风控 + 消费向信息密度低 |

**各网站操作流程/选择器/错误解决已按站独立成文，动手前先读对应 ref**：
- 雪球 → `references/xueqiu.md`（四层信息源/抓取/翻页/滑块验证/WAF 放行/API 拦截）
- 知乎 → `references/zhihu.md`（搜索/问题页/**40362 cookie 指纹限流修复**）
- X → `references/x.md`（推荐流+搜索/选择器/订阅号坑/已关注账号）
- 小红书 → `references/xiaohongshu.md`（排除记录）
跨站策略与裸 CDP 环境坑见 `references/deepdive_rules.md`。

## 置信度写入(关键)

每条消息必须带 source_type + confidence：

| source_type | 含义 | confidence |
|---|---|---|
| official | 公告/监管/公司渠道 | 5 |
| media | 主流媒体多方报道 | 4 |
| community | 论坛/社交单方、未核实 | 2 |
| rumor | 小道消息、无法核实 | 1 |

**message_type 内容类型**：每条消息再标内容类型（说的是什么），与 source_type 正交。深度浏览尤其要区分 **community（论坛讨论/流言，未证实）** 与 **financial_report/announcement（财报/公告，已证实）**——逛雪球/知乎常同时遇到两者，别混标：
- 论坛转述的财报/公告 → 内容已核实的标 financial_report/announcement（source_type 相应 official/media）
- 论坛原创讨论/猜测/流言 → 标 community（source_type 保持 community/rumor）
- 其余 10 类与 news-collector 一致：financial_report/announcement/news/research/community/industry_change/capital_flow/price_action/policy/other
CLI: `newsdb save --message-type <type> ...`

```bash
# 官方公告(高置信)
newsdb save --event <id> --title "..." --summary "..." --source-type official --confidence 5 --message-type announcement
# 论坛流言(低置信)
newsdb save --event <id> --title "..." --summary "论坛舆情: ... 未经证实" --source-type rumor --confidence 1 --message-type community
```

**甄别原则**：
- 多方印证(≥2 独立来源)或官方证实才上调 confidence
- 无凭据的"听说"标 rumor
- 情绪宣泄类跳过(避免噪音)
- 找不到新东西不入库

## 三个局限(务必记住)

1. **V 底反转点论坛滞后**——底部当天论坛最看空，不可做反转信号源
2. **论坛情绪是趋势跟随者**——只在趋势方向变化上提前，反转点上滞后
3. **置信度刚需**——粉黑互撕噪音巨大，必须过滤

## 深挖请求接线（task-bus）

发现需要**其他平台补充印证**的消息（X 消息需雪球/知乎确认、单一来源流言需多方核实）时，写入任务总线让心跳 agent 安排后续深挖：

```bash
export STOCK_TASKS_DB=/home/catmouse/Github_Project/daily-stock-workspace/data/tasks/tasks.db
taskbus add DEEP_DIVE <代码或行业> --source x-scan --priority 3 \
  --payload '{"reason":"X消息需雪球/知乎印证"}'
```

心跳 agent 消费 DEEP_DIVE → 驱动下一轮深挖（每轮限量 5 个目标内）。

## 完成后

- `newsdb ack-deepdive <id>` 确认处理完的请求
- **任务结束清理 tab**（必须做，别留一堆 tab）：
  - 默认：去重本轮 dive 开的 tab（每站最多留一个，只动 `$DIVE_SESSION_FILE` 记录的、不碰用户手动开的 tab）：
    `python3 "$DIVE_SCRIPTS/cdp_drive.py" dedupe`
  - 需要连用户手动开的重复 tab 一起清（用户要求/浏览器 tab 太多时）：`dedupe --all`
    对**全部** tab 按主域名去重，**相同网站保留 1 个**（保持登录 session），优先保留活跃 tab > 手动开的 tab > dive 开的 tab：
    `python3 "$DIVE_SCRIPTS/cdp_drive.py" dedupe --all`
  - 注意：`dedupe --all` 会关掉用户手动开的多余 tab，动手前先 `cdp_drive.py list` 确认保留哪个；用户原来开着的重要页面（watchlist 股票页等）优先保留
- 总结：新建 X 事件、追加 Y 消息、跳过 Z、处理请求 N 个
