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
| `CALENDAR` | 财报/解禁/除权日历 | 日历检查 | 分析/交易 |

### WATCH_ALERT 价格条件触发（watch_scan 自动写入）

`watch_scan.py` 每 tick 读 `conditions` 表 active 条件 vs 实时价，穿越触发时自动写事件，payload 规范：

```json
{"direction": "buy", "cond_id": 44, "cond_name": "买点下沿-建仓30%",
 "trigger_price": 30.0, "current_price": 25.14}
```

- **buy**：现价 ≤ 买点触发价（action 含 建仓/买入/加仓）——消费 agent 评估纪律后执行买入，或纪律否决时**调整买卖点**（移除/重设条件）
- **sell**：现价 ≤ 止损/止盈触发价（hard 或 action 含 清仓/减仓/止损）——确认破位后执行卖出；hard 条件只升不降
- **去重**：同实体同方向已有 pending/processing 事件时跳过，不重复写入；同 `cond_id` 精确去重（同条件不重复入队）
- **条件清理**：消费 agent 处理完须移除/标记已触发的 conditions，避免下一 tick 重新触发

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
