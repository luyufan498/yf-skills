---
name: task-bus
description: 股票任务总线（taskbus CLI）——事件驱动 agent 任务队列。当需要"发现候选/异动告警/深挖请求"等任务事件的入队、认领、消费、状态查询时使用。
version: 1.0.0
author: catmouse
license: MIT
metadata:
  hermes:
    tags: [task-bus, event-driven, 任务队列, 事件总线, agent编排]
    related_skills: [news-database, news-collector, news-deep-browser, paper-trading, stock-daily-analysis]
---

# 🚌 股票任务总线（task-bus）

事件驱动的 agent 任务队列。**信息域**（发生了什么）归 newsdb `events` 表；**任务域**（要做什么）归本总线 `task_events` 表。生产者发现需要处理的事项 → 写任务事件 → 心跳路由 agent 消费 → 标 done。两者分离，职责清晰。

## When to Use

- **生产者**（news-collector / x-scan / 分析 agent / 扫描脚本 / 用户）：发现值得分析的候选、需要补搜新闻、需要深挖、关注股异动、日历到期 → `taskbus add`
- **消费者**（心跳路由 agent）：醒来后 `taskbus list --status pending` → 逐个 `taskbus claim` → 处理 → `taskbus done/fail`
- 任何需要查询任务队列状态、最新事件 ID、剩余 pending 数的场景

## 环境

```bash
export STOCK_TASKS_DB=/home/catmouse/Github_Project/daily-stock-workspace/data/tasks/tasks.db
# 未设置时默认 <cwd>/data/tasks/tasks.db
```

安装：`cd <yf-skills>/stock-toolkit/skills/task-bus/scripts && uv tool install --editable .`

## 事件类型（6 种核心）

| 类型 | 含义 | 典型生产者 | 消费者 agent |
|------|------|-----------|-------------|
| `CANDIDATE` | 发现新候选标的/行业（新闻或扫描） | news-collector、市场扫描 | 分析（stock-daily-analysis） |
| `REFRESH` | 新闻库缺信息需补搜 | 分析 agent | 新闻（news-collector） |
| `DEEP_DIVE` | 需论坛/社交/外网深挖 | x-scan、分析 agent | 深挖（news-deep-browser） |
| `WATCH_ALERT` | 关注/池内标的异动或条件触发 | 心跳异动检测、watch_scan 价格触发 | 交易（paper-trading） |
| `REVIEW` | 组合审查触发 | 定时、事件 | 组合审查 |
| `CALENDAR` | 财报/解禁/除权日历 | 分析 agent（档位降级时挂回查）、日历检查 | 分析/交易 |
| `MARKET_SHOCK` | 大盘指数异动（单日跌幅超阈值） | watch_scan 心跳检测 | 深度研究（news-collector + news-deep-browser） |
| `L3_SNAPSHOT` | 午间次优候选快照（≤5 只打包，TTL=次日晨审前有效，**不碰钱**，2026-08-31 加入） | stock-l3-scan（13:35） | **仅次日 6:05 组合审查（晨审）消费**（收盘数据验证午间信号）；心跳/主 agent 不消费（watch_scan check_tasks 已排除，同 CALENDAR 语义）；积压非前一交易日的 pending 由晨审 done 注明"过期作废" |

### CALENDAR 日历回查（2026-08 起，档位管理配套）

分析 agent 对"暂时不买/等财报/等催化"的股票降级 L3 时写日历事件，到期自动回查升级：

```bash
taskbus add CALENDAR <股> --source analysis --priority 2 \
  --payload '{"due":"2026-08-20","event":"中报披露","check":"分析后评估是否升级"}'
```

- `due`：回查日期/时刻（ISO；**纯日期=当天 15:30 收盘后触发**，带时间=精确时刻触发，见下方到期时刻语义）
- `event`：回查原因（财报/解禁/催化日）
- `check`：回查动作（评估升级 L2 / 继续观察 / 移除）
- 消费：心跳 agent delegate 分析 subagent → 重新评估 → 升级 L2 / 延期重挂 / 移除
- **⚠️ 未到期不唤醒（2026-08-14 修复）**：CALENDAR 长期 pending 是常态（挂起等 due），**不是待消费任务**——`check_tasks` 已排除 CALENDAR，未到期**不**出现在 [EVENT] 列表、不触发心跳；只有 `check_calendar` 对到期事件输出 📅 提醒唤醒。改 watch_scan 时勿把 CALENDAR 加回 check_tasks（否则 9 个未到期回查点会每 tick 白唤醒）
- **⏰ 到期时刻语义（2026-08-18 修复，防凌晨空触发）**：`check_calendar` 原按天比较（due ≤ 今天），心跳全天 30 分钟跑 → **due 当天凌晨 00:00 就触发**，但"等中报披露"的事件那时财报还没出（空转）。现改为时刻级：**纯日期 `2026-08-20` → 当天 15:30（收盘后）到期**（等财报/公告/当日行情齐了再唤醒）；**带时间 `2026-08-20T10:00` → 精确到该时刻**（紧急事项显式写时间）。due 格式非法 → 跳过不触发不崩溃。分析 agent 挂 CALENDAR 时按此语义写 due：默认纯日期（收盘后触发），需要盘中/盘前触发才写时间。

### WATCH_ALERT 价格条件触发（watch_scan 自动写入）

`watch_scan.py` 每 tick 读 `conditions` 表 active 条件 vs 实时价，穿越触发时自动写事件，payload 规范：

```json
{"mode": "trade", "direction": "buy", "cond_id": 44, "cond_name": "买点下沿-建仓30%",
 "trigger_price": 30.0, "current_price": 25.14}
```

- **mode=trade（L1 条件触发）**：buy（现价 ≤ 买点）→ 消费 agent 评估纪律后执行买入，或纪律否决时调整买卖点；sell（现价 ≤ 止损/保护）→ 确认破位后执行卖出，hard 条件只升不降；**止盈阶梯（cond_name 含"分批止盈"，现价 ≥ 触发价，2026-08-30）**→ 确认当日为收盘确认后**次日**卖出持仓 1/3（T+1），`conditions --action trigger` 标记 + `sell --note "止盈阶梯①/②（成本¥X→现价¥Y 浮盈+Z%）"` 必填；**只标记该 TP 条件，禁止动保护线/移动止损**（余仓 1/3 继续 2.5×ATR 跟随）
- **mode=eval（L3 观察窗价格点触发）**：`taskbus watchpoint add` 设置的价格点穿越（现价 ≤ 价，配 `--min` 则区间 [min, price]）→ 唤醒**分析 agent 重新评估**（判定升级 L2 / 重设价格点 / 移除），**不直接交易**。触发后价格点自动移除（触发即失效）
- **mode=buy（L2 建仓点触发）**：`taskbus watchpoint add --mode buy --amount <预算>` 设置的建仓点穿越 → 唤醒 agent **核验 → 建仓**（见下方消费约定）。L2 已过分析确认买入意愿，到价即执行，但执行前必须过闸门；触发后价格点自动移除（触发即失效）
- **mode=risk（盘中新闻利空旁路，2026-08-31 加入）**：news-intraday（12:05 收闻）发现 **L1/L2 持仓标的** imp≥4 利空（立案/退市/停牌/暴雷/减持，payload 带 news_event=newsdb事件ID）时写入——补心跳纯价格触发的非价格信号盲区。消费：交易 subagent 查 newsdb 核真实性 → 对照持仓 → 防御评估。**盘中卖出仅限硬利空实锤（停牌/立案类）**；价格类止损仍走收盘确认纪律，普通坏消息不恐慌割肉
- **⚠️ `--amount` 语义 = 段预算（建段金额），不是首笔买入金额**：初始建段统一 = 总池 5%（1000 万池 → **¥500,000**）；首笔比例是 buy 阶段按策略矩阵（3.0.0）计算（如消息仓 5-20% × 50 万 = 2.5-10 万），**绝不填进 --amount**——填错会把段建小（如沃森生物 8/24 只建了 10 万=1% 池，8/25 修正案例）
- **去重**：同实体同方向已有 pending/processing 事件时跳过，不重复写入；同 `cond_id` 精确去重（同条件不重复入队）
- **条件清理**：消费 agent 处理完须移除/标记已触发的 conditions，避免下一 tick 重新触发
- **单轮消费上限（2026-08-22 加入，防响应截断）**：一次心跳唤醒**单轮最多 claim 3 个事件**（按优先级：MARKET_SHOCK/WATCH_ALERT > CANDIDATE > CALENDAR 到期 > 其他）。超过 3 个的事件留在队列，**下一轮（30 分钟后）继续消费**——不要一次性全处理（8/22 事故：7 个事件同轮处理导致响应截断失败）。watch_scan 输出也做了对应截断（只列前 3 个 + 汇总提示）。

### 📉 MARKET_SHOCK 大盘异动深度研究（2026-08-19 加入）

`watch_scan.py` 每 tick 检查四大指数（上证 -2% / 深成 -3.5% / 创业板 -4% / 科创50 -5% 任一跌破即触发），写 `MARKET_SHOCK` 事件（payload 含触发的指数/跌幅/日期，**同交易日只触发 1 次**）。消费 agent 做**逻辑链条深度研究**：

1. **导火索溯源**（news-collector 快讯层 + 搜索）：外盘（美/韩/日半导体、美股期货）→ 亚太传导 → A 股映射板块；用 `ptrade2 fetch-news` + newsdb `query-market --days 2` + searxng/brave 补漏
2. **资金面确认**（2026-08-19 加入，用 gf-finance token API 无风控）：谁在被抛/谁在被买——`cd <gf-finance skill 目录> && python3 scripts/lhb_analysis.py outline lhb`（今日龙虎榜概括）+ `python3 scripts/lhb_analysis.py daily <YYYYMMDD> sh/sz`（当日上榜个股）+ `python3 scripts/etf_rank.py fund 1 20`（ETF 主力资金榜，板块级资金流向）——替代同花顺数据中心板块资金流（浏览器登录态/CDP 不稳定）
3. **社区声音收集**（news-deep-browser，CDP 雪球/知乎/X）：投资者情绪、多空观点、是否恐慌错杀 vs 趋势反转；标注置信度（舆情 <3 仅背景参考）
4. **逻辑链条整理**：导火索 → 传导路径（板块逐级验证：半导体→算力→液冷→存储→高位股）→ 确认信号（量能/北向/跌停家数）→ 结论（恐慌错杀 / 趋势反转 / 高位退潮）
5. **组合影响评估**（paper-trading）：
   - 持仓止损位是否濒临触发 → 预警（`ptrade2 check-triggers` 遍历持仓）
   - L2 建仓点/价格点是否失效 → 重估（`taskbus watchpoint list` + conditions）
   - L3 候选是否要重设价格点
6. **产出**：
   - **深度研究结论单独建 research 事件**（2026-08-19 起，与日常记录分离）：
     ```bash
     newsdb save --new-event --title "<日期>大盘深度研究：<定性结论>" \
       --summary "<逻辑链+定性+置信度+组合影响>" \
       --entity-type market --info-type analysis \
       --tags "market-shock,panic-selloff,rate-shock" \
       --importance 5 --message-type research --sensitivity high
     ```
     - `--info-type`：analysis/news/fact/rumor（**analysis=深度研究**，需置信度+verdict）
     - `--tags`：弹性标签 N 个任意组合（如 panic-selloff / trend-reversal / rate-shock / semiconductor / high-confidence）
     - 日常收盘综述/快讯保持 `--info-type news`，不混淆
     - **⚠️ 深度研究内容只写入 research 事件，不得改写日常事件**（2026-08-19 事故：先改 #187 收盘综述的 latest_summary 写入完整逻辑链、后又建 #191，导致两条内容重复——已修复：深度研究产物（导火索溯源/社区舆情）全部归 research 事件，日常事件只保留行情记录；导火索溯源等研究产物应 `--event <research_id>` 追加，不写进日常综述事件）
   - 完整报告存 temp-data `--category deep-search` + reports/*.md
   - 飞书摘要（含影响清单）
   - 事后验证：挂 CALENDAR 事件（due=3-5 交易日后）对比实际走势与定性判断 → 更新事件 latest_summary

> **消费优先级**：MARKET_SHOCK 与 WATCH_ALERT 同级（priority=1），先研究后动仓——大盘异动期间不因单股条件触发而盲目操作，先判清市场环境。

### 🛒 L2 建仓点（watchpoint mode=buy）消费约定

**设置**（分析 agent 对 L2 正式候选确认买点后）：
```bash
taskbus watchpoint add <股> --price 24.5 --mode buy --amount 200000 --code <代码> --note "建仓10%"
# --amount = 建仓预算（触发后 master-pool-allocate 的金额），必须声明
```

**触发后消费流程**（mode=buy 的 WATCH_ALERT，claim 后按序执行）：
1. **预算核验**：payload 无 `budget`（设置时没传 --amount）→ **拒绝执行**，`taskbus done <id> --note "预算缺失，拒绝建仓"`，要求重设带预算的建仓点
2. **防重核验**（复用 WATCH_ALERT 通用前置校验）：`ptrade2 operations <股> --days 7` 近 7 日无同向建仓；无 pending/processing 同向事件（watch_scan 已去重，双保险）
3. **分析时效**：分析报告仍有效（无重大利空/财报变脸）→ 继续；失效 → done 注明，降 L3 重评
4. **资金核验**：`ptrade2 master-pool-show` 确认 free ≥ budget；不足 → done 注明"资金不足"，降 L3 或留观
5. **执行**：`ptrade2 master-pool-allocate <股> --amount <budget> --reason "建仓点触发-<note>"`（自动升 L1）→ `ptrade2 buy <股> --amount <budget> --note "<通道>-<依据>（<关键数据>）"` 建仓 → `taskbus done <id> --note "已建仓..."`
   ⚠️ **buy 的 --note 必填**（2026-08-28 审计规则）：operations.note 是交易质量唯一审计凭证，禁止留空。内容 = watchpoint 的 note + 判定关键数据（通道名/消息事件ID/imp/动量值/档位），如"通道判定-消息仓 newsdb#231（imp4 bullish，动量+8%，段内8%档）"。8/25-27 有五笔建仓 note 为空的教训（光通信链/存储/北斗等），复盘时无法还原判定依据。
6. 完成后若原档位是 L2，allocate 已自动升 L1，无需手动调档

### ⚠️ WATCH_ALERT 消费前置校验（防重复触发/重复建仓）

**任何 WATCH_ALERT 事件（含自动与手动补录）在 claim 后、执行买入/卖出前，必须先核验：**

1. **查条件状态**：`ptrade2 conditions "<股票>" --action event-list`（或直接查库），确认 payload 中 `cond_id` 对应条件仍为 **active**。若条件已 `triggered`/`removed`（说明此前已触发并消费过），**不得执行交易**，直接 `taskbus done <id> --note "条件已触发/失效，重复事件，不执行"`。
2. **查同向历史事件**：`taskbus list` 无 pending/processing 同向事件 + 检查最近 done 的 WATCH_ALERT 是否已对该条件执行过同向操作（`ptrade2 operations <股票> --days 7` 查近7日是否已有对应买入/卖出）。已有 → 不重复执行，标 done。
3. **查仓位合理性**：`ptrade2 info <股票>` 核对当前持仓。若事件方向为 buy 且仓位已到目标（如区间捕捉已建满），不追加；若 sell 且已清仓，不重复卖出。
4. **对账告警优先**：`watch_scan.py` 心跳会对账输出 `⚠️ 对账：事件#X ... 已 triggered（非 active）`——这类事件 **默认不执行交易**，核验后标 done 并清理。

**手动补录 WATCH_ALERT 的硬规则**（agent 手工 `taskbus add` 场景）：
- **必须先确认条件 active** 才允许补录；已 triggered/removed 的条件禁止补录
- payload 必须带真实 `cond_id`（禁止 0 或伪造）；带真实触发价与现价
- 补录前查 `_has_pending_event` 等价逻辑（同实体同方向已有 pending/processing → 不补）
- 补录后立即将该条件标 triggered（或由消费 agent 处理后标记），防止下一 tick 重复触发
- 怀疑重复时，优先**不补录**，而是人工核验后直接处置（如本次爱司凯事件：旧条件#81 昨已触发建仓，今现价再穿越系重复，直接 done 不执行）

## 状态机

```
pending ──claim──▶ processing ──done──▶ done
   ▲                  │
   │                  └──fail──▶ failed ──requeue──▶ pending
   └────────────── recover(超时重置) ──────────────┘
```

- `claim` 是**原子认领**（`UPDATE ... WHERE status='pending'`）：串行消费 + 认领失败跳过，双保险防抢事件/重复消费
- `recover` 把卡死（agent 崩溃）的 processing 重置回 pending，默认超时 2 小时

## CLI 命令

```bash
taskbus init                                # 初始化库
taskbus add CANDIDATE 光智科技 --source news-collector --priority 2 --payload '{"evidence":"磷化铟供需趋紧"}'
taskbus list --status pending [--type X]    # 查待消费（按 priority 排序）
taskbus claim 42                            # 原子认领 #42（pending→processing）
taskbus done 42 --note "已入关注列表"          # 完成
taskbus fail 42 --note "分析失败：数据不可用"    # 失败
taskbus requeue 42                          # failed→pending 重试
taskbus recover --stale-hours 2             # 卡死恢复
taskbus stats                               # 各状态计数 + 最新事件 ID
taskbus kv watch_scan_state                  # 读 KV 状态（watch_scan 异动状态/atr 日期）
taskbus kv watch_scan_state '{"a":1}'        # 写 KV 状态
taskbus ack 42 43 44 --note "串行消费完成"     # 批量完成
taskbus watchpoint add 光智科技 --price 240 --note "买点下沿-重新评估"          # L3 观察价点
taskbus watchpoint add 赛力斯 --price 24.5 --mode buy --amount 200000 --code sh601127 --note "建仓10%"  # L2 建仓点
taskbus watchpoint add 中芯国际 --price 135 --min 130 --mode buy --amount 200000 --code sh688981 --note "区间建仓(130-135)"  # 区间触发（2026-08-25 支持）
taskbus watchpoint list                        # 查看全部价格点（👀观=eval / 🛒买=buy）
taskbus watchpoint remove 赛力斯                # 移除价格点
```

## KV 状态存储

`kv_store` 表（tasks.db 内）提供持久化 KV，供 watch_scan 等脚本存状态（**数据库持久化，重启不丢**，不依赖 /tmp）：

- `watch_scan_state`：异动检测状态机（每只股票 last_state）+ atr-sync 每日日期
- 脚本通过 `kv_set`/`kv_get` 读写（taskbus CLI 的 `kv` 命令可调试查看）

## 生产者/消费者协议

### 生产者（写事件，立即返回，不阻塞）

```bash
# news-collector 发现新候选
taskbus add CANDIDATE <代码或行业> --source news-collector --priority 2 \
  --payload '{"evidence":"...","importance":4}'

# x-scan 发现需多平台补充的消息
taskbus add DEEP_DIVE <代码> --source x-scan --priority 3 \
  --payload '{"reason":"X消息需雪球/知乎印证"}'

# 分析 agent 发现新闻库缺解释
taskbus add REFRESH <代码> --source analysis --priority 2 \
  --payload '{"signal":"异动无解释"}'
```

### 消费者（心跳路由 agent，串行消费）

```bash
# 1. 看待消费列表（按 priority 排序，高优先先处理）
taskbus list --status pending

# 2. 逐个串行：认领 → delegate 对应 skill 的 subagent 处理 → 完成
taskbus claim 42        # 认领失败（返回非0）说明已被抢/状态变化，跳过
# ... subagent 处理 ...
taskbus done 42 --note "已入关注列表"

# 3. 汇报时带统计
taskbus stats           # 剩余 pending 数 + 最新事件 ID
```

### 心跳集成（watch_scan.py 参考）

monitor 脚本每 tick：`taskbus list --status pending` + 异动检测 → 无事件输出 `IDLE`（稳定，睡眠）；有事件输出摘要（变化，唤醒 agent）。唤醒后 agent 按上面消费者协议串行消费。

## 注意事项

- **认领是唯一防重**：处理前必须先 `claim`，claim 失败（exit 1）就跳过该事件，不要强行处理
- **done/fail 只接受 processing 状态**：直接对 pending 事件 done 会失败（防止未认领就结束）
- **串行消费**：一次只 claim 一个、处理一个、done 一个；不要并行 claim 一堆（多 agent 抢事件）
- 事件卡死：processing 超过 2 小时自动被 `recover` 重置（心跳脚本会顺带执行）
- 事件不丢：生产者写入后即使无人消费也一直在队列，心跳恢复后继续处理
