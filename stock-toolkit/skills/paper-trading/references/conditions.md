# 条件管理命令详解

本指南详细说明 `ptrade conditions` 条件管理命令，包括标准条件和事件条件的设定、查看、修改和触发。

## 目录

- [概述](#概述)
- [标准条件命令](#标准条件命令)
- [事件条件命令](#事件条件命令)
- [条件类型说明](#条件类型说明)
- [格式化输出](#格式化输出)
- [完整示例](#完整示例)
- [常见问题](#常见问题)

---

## 概述

条件系统用于管理持仓的纪律规则，分为两类：

| 类型 | 说明 | 数量限制 | 存储位置 |
|------|------|---------|---------|
| **标准条件** | 5种固定类型，每种只能存一个 | 最多5个 | `conditions` 字典 |
| **事件条件** | 6种事件类型，支持同类型多实例 | 无限制 | `events` 列表 |

**核心区别**：
- 标准条件：`--action set/update/remove` 会**覆盖**同类型的旧条件
- 事件条件：`--action event-set` 每次都**新增**一个条件，有独立ID

---

## 标准条件命令

### 查看条件

```bash
# 默认格式（终端友好）
ptrade conditions "股票名称"

# Markdown 格式
ptrade conditions "股票名称" --format markdown --template all

# JSON 格式
ptrade conditions "股票名称" --format json
```

### 设定条件

```bash
# 设定移动止损（hard，持仓周期有效）
ptrade conditions "股票名称" --action set --type trailing_stop --price 65.0 --action-str "减仓50%" --category hard

# 设定止盈条件（soft，7天有效）
ptrade conditions "股票名称" --action set --type take_profit_1 --price 90.0 --action-str "减仓20%" --category soft --expiry-days 7

# 设定成本保护（hard，自动跟随持仓成本，含1.5%缓冲）
ptrade conditions "股票名称" --action set --type cost_protection --price 77.51 --action-str "亏1.5%清仓" --category hard
```

### 修改条件

```bash
# 更新价格（受规则引擎约束）
ptrade conditions "股票名称" --action update --type trailing_stop --price 75.0 --reason "支撑位上调"
```

### 移除条件

```bash
ptrade conditions "股票名称" --action remove --type take_profit_1
```

### 触发/过期/检查

```bash
# 手动标记条件已触发
ptrade conditions "股票名称" --action trigger --type trailing_stop --trigger-price 64.5

# 手动标记条件过期
ptrade conditions "股票名称" --action expire --type take_profit_1

# 检查过期条件
ptrade conditions "股票名称" --action check
```

---

## 事件条件命令

### 设定事件条件

```bash
# 亏损预警（hard）
ptrade conditions "股票名称" --action event-set --event-type loss_protect --price 68.6 --action-str "减仓20%" --category hard

# 技术破位（hard）
ptrade conditions "股票名称" --action event-set --event-type tech_break --price 75.0 --action-str "减仓50%" --category hard

# 目标价止盈（soft，7天有效）
ptrade conditions "股票名称" --action event-set --event-type target_profit --price 90.0 --action-str "减仓20%" --category soft --expiry-days 7

# 加仓条件（soft，支持同类型多实例）
ptrade conditions "股票名称" --action event-set --event-type add_position --price 45.36 --action-str "成本区补仓-加仓30%" --category soft --expiry-days 7

# 分批建仓条件（soft，空仓状态下首次建仓，通过多个独立事件覆盖买点区间）
ptrade conditions "股票名称" --action event-set --event-type add_position --price 80.00 --action-str "买点下沿-建仓30%" --category soft --expiry-days 7
ptrade conditions "股票名称" --action event-set --event-type add_position --price 81.00 --action-str "买点中沿-建仓30%" --category soft --expiry-days 7
ptrade conditions "股票名称" --action event-set --event-type add_position --price 82.00 --action-str "买点上沿-建仓40%" --category soft --expiry-days 7
```

### 查看事件条件

```bash
# 列出所有事件条件
ptrade conditions "股票名称" --action event-list
```

输出示例：
```
📋 股票名称 事件条件列表 (3个):
   ✅ [eca38fef] 亏损保护 ¥68.60 — 减仓20% (hard)
   ✅ [68791967] 亏损保护 ¥66.40 — 减仓50% (hard)
   ✅ [9df2d57c] 技术破位 ¥75.00 — 减仓50% (hard)
```

### 移除事件条件

```bash
# 通过ID移除
ptrade conditions "股票名称" --action event-remove --event-id eca38fef
```

### 触发事件条件

```bash
# 手动标记事件已触发
ptrade conditions "股票名称" --action event-trigger --event-id eca38fef --trigger-price 68.2
```

---

## 条件类型说明

### 标准条件类型（5种）

| 类型 | 用途 | 建议类别 |
|------|------|---------|
| `trailing_stop` | 移动止损/技术破位 | hard |
| `cost_protection` | 成本保护（亏1.5%清仓） | hard |
| `take_profit_1` | 第一止盈位 | soft |
| `take_profit_2` | 第二止盈位 | soft |
| `add_position` | 加仓条件 | soft |

### 事件条件类型（6种）

| 事件类型 | 用途 | 典型场景 |
|---------|------|---------|
| `profit_protect` | 利润保护梯度 | 浮盈>20%后设定四档回撤保护 |
| `loss_protect` | 亏损保护梯度 | 浮亏状态下设定亏损止损线 |
| `tech_break` | 技术破位 | 跌破关键支撑位减仓 |
| `target_profit` | 目标价到达 | 分析报告目标价减仓20% |
| `add_position` | 加仓条件 / 分批建仓条件 | 成本区/支撑位/急跌/突破加仓；空仓状态下的首批/第二批/第三批建仓 |
| `fundamental` | 基本面事件 | 业绩暴雷/重大利好 |
| `market_risk` | 市场风险 | 大盘系统性风险 |

### 类别说明

| 类别 | 有效期 | 修改约束 | 用途 |
|------|--------|---------|------|
| `hard` | 持仓周期内 | 修改需理由，只升不降 | 止损、成本保护、技术破位 |
| `soft` | 7天（默认） | 相对灵活 | 止盈、加仓、利润保护 |

---

## 格式化输出

### 模板选项

| 模板 | 说明 |
|------|------|
| `trigger-table` | 当前有效条件表 |
| `audit-table` | 硬条件修改审计 |
| `expired-table` | 已失效条件 |
| `execution-check` | 上期条件执行检查 |
| `all` | 以上全部（默认） |

```bash
# 只看触发条件表
ptrade conditions "股票名称" --format markdown --template trigger-table

# 只看执行检查
ptrade conditions "股票名称" --format markdown --template execution-check
```

---

## 完整示例

### 场景：为新持仓设定完整条件体系

```bash
# 1. 初始化（建仓时自动创建）
ptrade init "英维克" --capital 500000

# 2. 设定标准条件
ptrade conditions "英维克" --action set --type cost_protection --price 72.21 --action-str "亏1.5%清仓" --category hard
ptrade conditions "英维克" --action set --type trailing_stop --price 65.0 --action-str "技术破位-减仓50%" --category hard

# 3. 设定事件条件（亏损梯度）
ptrade conditions "英维克" --action event-set --event-type loss_protect --price 68.6 --action-str "减仓20%" --category hard
ptrade conditions "英维克" --action event-set --event-type loss_protect --price 66.4 --action-str "减仓50%" --category hard
ptrade conditions "英维克" --action event-set --event-type loss_protect --price 61.4 --action-str "清仓" --category hard

# 4. 查看完整条件
ptrade conditions "英维克" --format markdown --template all

# 5. 查看事件条件列表
ptrade conditions "英维克" --action event-list
```

---

## 常见问题

### Q: 标准条件和事件条件有什么区别？

A: 标准条件是5种固定类型（如 `trailing_stop`），每种只能存一个，新设会覆盖旧的。事件条件是6种类型，每种可以存多个（如多个不同价格的 `loss_protect`），通过独立ID管理。

### Q: 为什么利润保护和亏损保护要用事件条件？

A: 因为梯度体系需要同类型多实例（如亏损5%/8%/15%三档），标准条件的字典结构只能存一个值。事件条件的列表结构支持无限多实例。

### Q: `event-list` 和默认 `show` 有什么区别？

A: `event-list` 只显示事件条件。默认 `show`（或 `--format markdown`）会同时显示标准条件和事件条件。

### Q: 事件条件过期后怎么办？

A: 事件条件和标准条件一样支持 `expiry_days`。过期后会显示在 `--template expired-table` 中，需要复审后重新设定或移除。

### Q: 除权后条件需要重置吗？

A: 除权前设定的绝对价格条件（止盈、止损）需要基于除权后价格重新设定。成本保护（`cost_protection`）会自动跟随持仓成本同步（保护价 = 成本价 × 0.985），无需手动调整。

### Q: `add_position` 事件类型可以用于空仓建仓吗？

A: 可以。`add_position` 事件类型有两种语义：
- **加仓**（已有持仓）：`--action-str` 使用 `"<触发类型>-加仓<比例>%"` 格式，如 `"成本区补仓-加仓30%"`、`"支撑位补仓-加仓25%"`
- **分批建仓**（空仓 → 目标仓位）：通过多个独立的 `add_position` 事件覆盖买点区间，每个事件用 `--action-str` 描述批次和比例，如 `"买点下沿-建仓30%"`、`"买点中沿-建仓30%"`、`"买点上沿-建仓40%"`。事件之间是 OR 关系——任一触发即完成部分建仓，触发后 agent 手动 `event-remove` 其他未触发的事件。

> **命名规范**：`--action-str` 推荐使用 `"<触发位置>-建仓<比例>%"` 格式，便于 agent 在步骤 7 审查时识别批次。详见 [stock-daily-analysis/references/trading-discipline.md](../../stock-daily-analysis/references/trading-discipline.md) 第 3.0 节"分配建仓策略"。

### Q: 分批建仓的多个事件之间有依赖关系吗？

A: 没有。每个 `add_position` 事件条件独立存储和触发，CLI 不维护批次之间的依赖。多个事件是 OR 关系——任一价位触及即触发对应建仓。agent 在生成报告时需要在步骤 7 审查所有同类型事件的触发状态，并在步骤 8 根据投资决策决定是否移除未触发的事件（如区间捕捉完成后移除其他未触发的买点条件）。

### Q: 分批建仓期间成本保护价为什么低于"成本×0.985"？

A: 分批建仓期间，CLI 会自动调整成本保护价，避免"计划内浮亏"被误判为"判断错误"强制清仓。

**调整逻辑**：`sync_cost_protection`（每次 `buy` 后自动调用）会检查是否存在未触发的 `add_position` 事件：

| 阶段 | 保护价计算 | 语义 |
|------|----------|------|
| 建仓期间（有未触发的 `add_position`） | `min(加权成本 × 0.985, 最低买点 × 0.98)` | 容忍计划内浮亏，但跌破买点下沿 2% 仍止损 |
| 建仓完成（无未触发的 `add_position`） | `加权成本 × 0.985` | 正常成本保护，亏损 1.5% 清仓 |

**示例**：买点区间 ¥80-82，¥82 首批建仓后加权成本 ¥82，正常成本保护应为 ¥80.77。但存在未触发的 ¥80/81 买点事件，保护价取 `min(80.77, 80×0.98=78.40) = ¥78.40`。这样股价跌到 ¥80 时会按计划触发第二批建仓，而不是触发成本保护清仓。建仓完成后（所有买点事件触发或移除），下次 `buy` 操作触发 `sync_cost_protection` 时会自动切换回正常的 `成本×0.985`。

> **agent 无需手动操作**：此调整完全由 CLI 自动完成。agent 在步骤 7 审查时如看到成本保护价低于"成本×0.985"，应理解为建仓期间的正常行为，并在报告"利润保护追踪"章节说明。详见 [stock-daily-analysis/references/trading-discipline.md](../../stock-daily-analysis/references/trading-discipline.md) 第 3.0.6 节。

---

## 相关命令

- `ptrade conditions` - 条件管理主命令
- `ptrade info` - 查看持仓和盈亏
- `ptrade operations` - 查看交易历史

## 进阶话题

参考以下文档了解更多：

- **[交易原则与策略](trading-principles.md)** - 止损止盈策略详解
- **[数据存储结构](data-storage.md)** - conditions.json 文件格式说明
