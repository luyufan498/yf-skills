---
name: paper-trading
description: 模拟盘交易系统，支持 A股、港股和美股的模拟交易，提供独立资金池管理、市场数据查询和新闻获取功能。当用户需要进行股票模拟交易、市场数据查询或获取市场新闻时使用。
---

# 模拟盘交易系统

## ⏰ 交易时段约束（2026-08-25 加入，执行 agent 必读）

**交易时段**：周一至五 **9:30-11:30、13:00-15:00**（A 股连续竞价）。

**硬规则：买卖（allocate+buy / sell）只能在交易时段执行**。执行 agent 操作前先 `date +%H:%M` 判断当前时间，非交易时段**禁止直接买卖**。

**非交易时段（凌晨/午休/收盘后）发现交易信号 → 挂买卖点，开盘后由心跳价格触发**：
- **建仓信号**（l3-scan 复核通过 / 组合审查通道判定通过 / 分析设点）：不直接 buy → `taskbus watchpoint add <股> --price <触发价> --mode buy --amount <预算> --code <代码> --note "非交易时段判定，开盘触发:<原因>"`（**必须 --mode buy**——默认 eval 次日触发只唤醒评估不建仓，链路断裂）→ 开盘后心跳检测现价≤触发价 → 交易时段内执行
- **止损信号**（非交易时段价格触及止损位）：**不建 watchpoint**（CLI 无 sell 模式）——止损位已存在 conditions 表（cost_protection/trailing_stop），`check_price_triggers` 次日交易时段自动检测破位触发执行，无需额外挂点
- **紧急破位**：同样只标记不直接执行（模拟盘无真实滑点，等开盘触发即可）

**例外**：无。交易时段约束是硬性的——凌晨/收盘后成交（如中际旭创 01:05 以隔夜收盘价建仓）视为执行纪律违规。

## ⚡ ptrade2（V2 弹性组合总池，推荐）

> **2026-08-10 起 ptrade2 已完整上线**：命令面与 v1 完全对齐（35 命令），SQLite 深迁移存储 + 弹性组合总池 + 三档策略。旧 ptrade v1 保留但仅用于回退。

**核心区别**：
- **存储**：`ptrade2` 用 SQLite（`master_pool.db`）单一事实源，取代 v1 的每账户 JSON 文件。命令 `ptrade2` 与 v1 命令同名同参，直接替换前缀即可。
- **资金模型**：v1"每只股票独立资金池互不相通" → v2"**一个总池 1000 万 + 按股分配/释放**"。池 ≠ 持仓 ≠ 预算三者解耦。
- **三档策略**：L1 人工锁定（AI 无权 release）/ L2 稳健（agent 自主+三重释放条件）/ L3 投机（agent 完全自由，空仓即剔）。当前 8 股全部 L2。

**快速开始（ptrade2）**：
```bash
# 总池状态（每次交易/审查先看）
ptrade2 master-pool-show              # total/free/占用率/活跃段/已实现盈亏

# 池名单
ptrade2 watchlist-list                # 三档名单
ptrade2 watchlist-add 股票 --strategy L2 --source agent --reason 依据
ptrade2 watchlist-add 股票 --strategy L1 --source manual --reason 人工锁定  # L1 必须 manual
ptrade2 watchlist remove 股票 --reason 依据   # 出池（僵尸剔除/降级后移除）

## 三档语义（2026-08 重构：L1=持仓段 / pin=名单保护）

- **L1 持仓段**：被 allocate 分配预算的股票（`master-pool-allocate` **自动升 L1**）。**初始建段统一 = 总池 5%**（1000 万池 → 50 万；段预算 = 资金承诺上限 ≠ 买入额）。段预算内**按策略分批建仓**（首笔比例查交易纪律 3.0.0 矩阵，禁止一次买满；补仓优先段内弹药，弹药用尽才 topup ≤30%），自由买卖（建仓/加仓/减仓/清仓）。`release` 释放空仓段后**自动降回 L2**
- **L2 正式候选**：准备建仓的股票。**可设建仓点**：`taskbus watchpoint add <股> --price <价> --mode buy --amount 500000 --code <代码>`（**--amount = 段预算 = 总池 5% = ¥500,000，不是首笔买入金额**——首笔比例是 buy 阶段按策略矩阵算，如消息仓 5-20%×50万；填小会把段建小，见沃森生物 8/24 教训；可选 `--min <价>` 设区间 [min, price] 触发，跌破下限不接刀）→ 到价触发 `WATCH_ALERT(mode=buy)` → agent 核验（预算/防重/分析时效/资金）→ `master-pool-allocate`（自动升 L1）→ `ptrade2 buy` 建仓；也可主动建仓：`master-pool-allocate <股> <预算> --reason ...`（分配预算 → 自动升 L1）→ `ptrade2 buy`。无持仓 + 短期不打算买入 → agent 降级 L2→L3（watchlist-add --strategy L3）
- **L3 观察窗**：只观察不买卖——新闻跟踪（newsdb track）+ 价格点等待（taskbus watchpoint）+ 异动监测。进入即触发一次分析（CANDIDATE），评估后：值得入场 → 升级 L2；有价值但时机未到 → 留 L3 设价格点；无价值 → 移除
- **pin 名单保护（独立字段，与档位正交）**：`watchlist-add --pin` 设置。pin=1 的股票：**允许自由升降级（L1↔L2↔L3）但禁止删除**（`watchlist remove` 被拒绝，可降级到 L3 观察）。取消 pin 需人工确认（source=manual）
- 入池默认 L3（观察），`--strategy` 可指定 L1/L2/L3

## L3 观察窗管理

```bash
# 进观察窗（组合审查入池扫描 / 新闻驱动）
ptrade2 watchlist-add 股票 --strategy L3 --source agent --reason "依据"
taskbus add CANDIDATE 股票 --source portfolio-review --priority 2 --payload '{"evidence":"..."}'

# 观察中：设价格事件点（心跳 watch_scan 检测 现价≤价 → WATCH_ALERT(mode=eval) → 唤醒分析评估升级）
taskbus watchpoint add 股票 --price 24.5 --note "买点下沿-重新评估"
taskbus watchpoint list
taskbus watchpoint remove 股票

# 评估结论：值得入场 → 升级 L2（进入正式运作范围）
ptrade2 watchlist-add 股票 --strategy L2 --source agent --reason "分析通过+右侧信号"
# 或：无价值 → 移出观察窗
ptrade2 watchlist remove 股票 --reason "观察窗评估无价值"
```

## 名单维护规范（2026-08 组合审查起）

### 🔀 组合审查通道判定（2026-08-22 加入，每日消息门整体触发）

**WATCH_ALERT mode 语义表**（心跳消费方按 mode 分流，2026-08-24 加入）：
```
mode=trade   价格条件触发（buy/sell 条件）→ 直接按交易流程执行
mode=eval    L3 价格点触发 → 评估是否升级 L2
mode=l3-scan L3 通道预筛（qwen 12:30 扫描建事件）→ 复核通道条件后按试探仓执行
```

**l3-scan 消费流程**（心跳唤醒后识别 payload.mode=="l3-scan"）：
1. **不信任 payload 数据**——重新拉最新价格/动量/新闻/大盘验证通道条件（消息仓/价值反转/粘滞flag 之一）
2. 成立 → **先判断交易时段**（`date +%H:%M`，交易时段=9:30-11:30/13:00-15:00）：交易时段内直接按"消息试探仓建仓执行"框架执行；**非交易时段 → 挂 watchpoint（`--mode buy --amount <预算>`，触发价=当前价或分析价，note 注明"非交易时段判定，开盘触发"），开盘后由心跳价格触发执行**
   - **建段必须带 code**：`master-pool-allocate <股> <预算> --code <代码>`（从事件 payload 取 code，缺失则查 pool/accounts 兜底；严禁建段不带 code——否则网站持仓段代码列空白，且 fetch_prices 无法取价）
3. 不成立 → taskbus 事件 resolve + 记录原因
4. 防重复：单日消息仓限 1 只；"已开过不重复开"；qwen 预筛事件不写 newsdb（19:00 审查不会重复判定）

**背景**：L3 观察窗不做每日深度分析（4:00 批量只覆盖 L1/L2，9:40 轻量开盘复核也只查 L1/L2 的调整需求），消息试探仓的判定（stock-daily-analysis 步骤 7）对 L3 是断的——L3 个股强消息入库后无人触发消息门。修复：**组合审查（19:00）每日基于当天全部事件统一做通道判定**，优于每条消息单独触发（效率高 + 能看消息组合效应）。

**执行位置**：组合审查第三步（L3 管理）之前，对 L3 观察窗 + 当日强消息标的逐一过四条通道：

```
① 消息仓（strong-signal）：newsdb 重要度≥4 + bullish + confidence≥3 个股消息
   + 10日动量 ≤25% + 大盘非极端崩跌（m20<5%）+ 非连亏
   → 执行消息试探仓（建段 5% + 段内 5-20%）
② 价值反转通道：超跌（回撤>15%/周线破位 + 近2根日K止跌）+ 行业或公司级
   已证实利好（行业需成分核心 rel≥70）+ 动量∈[-15%,+15%] + 大盘非崩跌 + 非连亏
   → 执行价值反转试探仓（建段 5% + 段内 5-10%）
③ 粘滞flag确认豁免：动量 10日∈[15%,25%] 甜点区 + 仅粘滞 flag（非破位）
   + 大盘 m20≤3%（急跌放行/阴磨不适用）+ 非连亏 + 日线站上5日线且收阳
   → 执行消息试探仓（建段 5% + 段内 5-20%）
④ 动量仓（趋势门通过 + 甜点区）→ 常规升级 L2 路径（第三步原有逻辑）

都不命中 → 维持 L3 / 设价格点（原逻辑；**移除类操作不在此执行**，见下方职责分离）
```

**执行注意**：
- 判定通过 → **先判断交易时段**（见顶部"交易时段约束"）：交易时段内**直接执行**（allocate 建段 + buy + 成本保护 + 移动止损 + CALENDAR 评估）；**非交易时段（审查 19:00 属非交易时段）→ 挂 watchpoint（`--mode buy --amount <预算>`，触发价=判定价/现价，note "审查判定通过，开盘触发:<通道/理由>"），开盘后由心跳触发执行**。复用下方"消息试探仓建仓执行"框架
- **防循环核验**（规则 14-16）必须执行：近 2 日止损清仓禁开、成本抬高>3% 禁开、已开过不重复开
- 单日最多 1 只消息仓（规则 3.4.4）；多只命中按优先级取 1 只（消息重要度最高者）
- 通道判定不改变 L3 档位（试探仓买入即升 L1，见档位语义）；未命中通道的 L3 继续观察
- 判定依据写审查摘要（哪条通道/为什么命中/为什么未命中），投递群时可审计

### 📏 L3 数量上限（2026-08-24 加入，2026-08-25 改目标区间主动控制）

**目标**：L3 观察窗**非锁定（pin=0）控制在 20-23 只**（硬顶 25）。**锁定（pin=1）不计入上限**——它是保护名额，锁定的票是用户/系统明确保护的（曾过正式分析/长期价值标的），不应因数量上限被挤出，也不占用普通观察名额。

**为什么是目标区间而非压线**（2026-08-25 修正）：原规则"超限才挤、不超限不主动挤"导致审查每次都报"刚好 25 压线"，**上限从未真正执行过**——实际变成"涨到 25 才不让进新票"，而不是"稳定控制在 25 左右"。修正为：**非锁定 >23 即主动识别挤出候选**（留 2-5 只缓冲给新票），硬顶 25 是最后防线（=25 时不再入池新票，除非先挤）。2026-08-25 实测执行：25 → 20（挤出中信出版/药明康德/康希诺/华大智造/网宿科技，均为无 CALENDAR 挂载+消息陈旧/性价比低）。

**为什么需要**：新行业探索 + 三层候选机制跑起来后入池速度变快（8/24 已达 28 只），L3 过多会稀释关注度——每只分到的评估/新闻资源变少。

**挤出优先级（从最该挤的开始，仅对非锁定票）**：
```
1. 老而僵：入池 >30 天 且 期间无任何升级/降级/重评动作
2. 消息陈旧：近 10 天无任何 newsdb 新事件
3. 基本面差：连亏/转亏股 或 最新财报恶化（-30%+ 营收/净利下滑）
4. 性价比低：动量持续走弱（近10日<0）+ 无 watchpoint/CALENDAR 挂载
5. 无报告支持：近 14 天无分析报告
```

**保护（不可挤）**：
- pin=1（名单保护，**不计入上限名额**，锁定票永不因超限被挤出）
- 有 active watchpoint 或 pending CALENDAR（还在等触发，未到评估期）
- 7 天内刚入池的新票（给足观察期）
- L1/L2 降级下来的票（曾经过正式分析，价值已知）

**超限执行（2026-08-25 职责分离：只识别候选、不执行挤出）**：统计 L3 总数 → 减去 pin=1 锁定数 → 仅非锁定部分 >25 时，按优先级 1→5 找出可挤的（只挤非锁定）→ **标记为"L3 超限候选"**（写入审查摘要，**不执行 `watchlist remove`**）——删除由人工确认后执行，或用户明确指令。理由写清（哪条优先级/什么原因）。

**⚠️ 截断保护（2026-08-25 加入）**：若本轮组合审查发生**响应截断/输出不完整**（monitor 报 truncated、continuation 失败、或摘要缺失步骤），**连"识别出池候选"也跳过**（识别也需要完整评估）——只做**非破坏性操作**（通道判定记录、设价格点、挂 CALENDAR、升级 L2），并在摘要注明"⚠️ 截断保护：出池候选识别延后"。

**入池（主动发现）**：组合审查每交易日扫描：
- 新闻源：`newsdb important --min-importance 4 --days 3` + `newsdb query-market --days 3` 的高重要度 bullish 事件标的
- 技术初筛：动量 15-25% 甜点区 / 周线收复 10 周均线 / 超跌企稳（`ptrade2 fetch-kline`）
- 未在名单且双源有依据 → `watchlist-add --strategy L3 --source agent --reason "<事件摘要>; 动量+XX%"`
- **入池后必须触发分析**：`taskbus add CANDIDATE <股> --source portfolio-review --priority 2 --payload '{"evidence":"<依据>"}'` → 心跳路由 agent 30 分钟内消费 → delegate 分析 subagent 完整分析并设定触发价。
- **入池 ≠ allocate**：新入池只进名单，等分析结果验证后才谈资金

**出池候选识别（2026-08-25 职责分离：组合审查只识别、不执行删除）**：对**无持仓**股票评估（L2/L3），命中以下条件 → **标记为"出池候选"**（写入审查摘要，**不执行 `watchlist remove`**）：
- a. 连续 ≥5 交易日无新事件（`newsdb query-stock <code> --days 5` 无新增/open 事件）
- b. 动量持续走弱（近 10 日涨幅 < 0 且周线未修复）
- c. 无近 7 天分析报告支持
- a+b 或 a+c → 标记"出池候选：<股>（僵尸）"，理由写清；**删除操作由人工确认后执行**（或用户明确指令）
- L1 永不自动动（人工锁定）

> **为何组合审查不做删除（2026-08-25 拍板）**：组合审查是**正向流程**（通道判定/挂载触发/升级 L2/入池），删除是**负向操作**且不可逆——混在例行审查里，一旦审查信息不完整（截断/超时/单只分析过粗）就可能误删有价值标的（尤其有 watchpoint/CALENDAR 保护的票）。职责分离：**审查识别 → 人工/用户确认 → 执行删除**。出池候选在摘要里列出，等用户确认或专门指令再删。

**与其他机制分工**：入池的"事件驱动"路径（taskbus CANDIDATE → 分析）管盘中新题材；组合审查主动扫描管"没上新闻雷达但技术面转好"的兜底 + 出池候选识别（不删除）。

# 开持仓段（从 free 拨预算建账户）——替代 v1 的 ptrade init
ptrade2 master-pool-allocate 股票 --amount 200000 --reason 右侧建仓

# 段内注资（买入缺口时）
ptrade2 master-pool-topup 股票 --amount 50000 --reason 补弹药

# 关段回池（空仓后，7 日冷却）
ptrade2 master-pool-release 股票 --reason 空仓释放

# 交易命令与 v1 同名（buy/sell/conditions/atr-sync/check-triggers/fetch-* 全部可用）
ptrade2 buy 股票 --qty 100
ptrade2 atr-sync 股票
ptrade2 check-triggers 股票

# 历史迁移（一次性，把 v1 JSON 账户归档为 closed 段）
ptrade2 migrate-existing
```

详细文档：[弹性组合总池（V2）](references/elastic-master-pool.md)、[V2 资金纪律](references/trading-principles.md)

---

## 概览（v1 兼容说明）

> 以下为 v1 `ptrade` 的说明。功能命令与 ptrade2 一致，但存储为每账户独立 JSON、资金互不相通。ptrade2 已上线后，**新操作请用 `ptrade2`**。

模拟盘交易系统是一个功能完善的虚拟交易平台，支持 A股、港股和美股交易，每个股票有独立的资金池管理，提供完整的交易流程追踪、市场数据查询和市场新闻获取功能。

**安装方式**：
```bash
cd scripts
uv tool install --editable .
```

**要求**：需要先安装 uv 工具：`pip install uv`

## 核心功能

### 交易管理
- **独立资金池**：每只股票享有独立的资金池系统
- **买入/卖出**：支持按股数、按金额交易，自动获取实时价格
- **持仓追踪**：实时跟踪持仓成本、市值和浮动盈亏
- **操作历史**：查看所有交易记录和资金变动详情
- **收益分析**：统计收益率、胜率、盈亏比等指标

### 市场数据
- **实时价格**：查询 A股、港股、美股实时股价
- **K线数据**：获取日K、周K、月K、分钟K线
- **股票搜索**：搜索 A股、港股、美股股票代码
- **市场新闻**：获取财联社、新浪财经、TradingView 市场新闻

### 数据管理
- **导出功能**：支持 JSON、CSV 格式导出
- **投资组合**：统一管理多股票账户和收益统计

## 快速开始

> 以下列出常用命令，完整命令列表请使用 `ptrade --help` 查看。

### 基础交易流程

```bash
# 初始化资金池
ptrade init "股票名称" --capital 100000

# 买入股票
ptrade buy "股票名称" --qty 100

# 卖出股票
ptrade sell "股票名称" --qty 50

# 查看完整信息
ptrade info "股票名称"
```

### 市场数据查询

```bash
# 查询实时价格 (⚠️ 使用股票代码，如 sh600000、sz300731)
ptrade fetch-price sh600000

# 查询K线数据
ptrade fetch-kline sh600000 --type day --count 30

# 多周期市场趋势汇总 (月K/周K/日K/分时)
ptrade market-summary sh600000

# 搜索股票代码
ptrade search 茅台 --limit 5

# 获取市场新闻
ptrade fetch-news --source all --limit 10
```

### 查看操作历史

```bash
# 查看单只股票的操作历史
ptrade operations "股票名称"

# 查看所有股票的操作历史
ptrade operations
```

### 投资组合管理

```bash
# 查看所有账户
ptrade list

# 查看投资组合
ptrade portfolio

# 性能分析
ptrade analyze

# 导出数据
ptrade export --format json
```

### 条件管理（交易纪律）

```bash
# 查看当前条件
ptrade conditions "股票名称" --format markdown --template all

# 设定标准条件（5种固定类型，会覆盖同类型旧条件）
ptrade conditions "股票名称" --action set --type trailing_stop --price 65.0 --action-str "减仓50%" --category hard

# 设定事件条件（支持同类型多实例，不覆盖）
ptrade conditions "股票名称" --action event-set --event-type loss_protect --price 68.6 --action-str "减仓20%" --category hard

# 查看事件条件列表
ptrade conditions "股票名称" --action event-list

# 移除事件条件
ptrade conditions "股票名称" --action event-remove --event-id XXX
```

详细文档：[条件管理](references/conditions.md)

### ATR 止损同步与触发检测

```bash
# 同步 ATR 动态止损（trailing_stop = peak−2.5×ATR，cost_protection = 成本−2×ATR）。持仓时每日跑
ptrade atr-sync "股票名称"              # 算 ATR(14)+更新 peak+同步止损位（只升不降）
ptrade atr-sync "股票名称" --dry-run    # 只算不写，预览止损位变化
ptrade atr-sync "股票名称" --reset-peak # 重新建仓后重置 peak 为当前价（不继承上一轮）

# 检测现价是否已跌破硬条件触发价（只读，不自动卖出）
ptrade check-triggers "股票名称"        # 对比实时价与所有硬条件，返回已破位清单；有破位退出码 1
ptrade check-triggers                   # 省略股票名则遍历所有持仓账户
```

> ⚠️ `conditions --template trigger-table` 的"未触发/已触发"只反映**手动标记**，不反映实时破位——止损位设了必须跑 `check-triggers` 才知道有没有被跌破。详见 [交易纪律](references/trading-principles.md) 规则 2.2/2.5。

### ⚠️ 消费 WATCH_ALERT 事件时的交易校验（防重复触发/重复建仓）

当作为交易 agent 消费 taskbus 的 WATCH_ALERT 事件（`taskbus claim <id>` 后、执行 buy/sell 前），**必须核验**：

1. **条件仍 active？** 查 `ptrade conditions "股票" --action event-list`，确认事件 payload 中的 `cond_id` 对应条件状态为 `active`。若已 `triggered`/`removed` → 说明此前已触发并处理过，**不执行交易**，直接 `taskbus done <id> --note "条件已触发/失效，重复事件，不执行"`。
2. **近7日已执行过同向操作？** `ptrade operations "股票" --days 7` 检查是否已对该条件执行过买入/卖出（如"买点上沿-建仓10%"昨天已建仓）。已有 → 不重复执行，标 done。
3. **仓位合理？** `ptrade info "股票"` 核对：buy 事件但仓位已达目标（区间捕捉建满）→ 不追加；sell 事件但已空仓 → 不重复卖出。
4. **手动补录事件（cond_id=0 或无凭证）**：直接按"疑似重复"处理，核验后标 done，不执行交易（详见 task-bus skill「WATCH_ALERT 消费前置校验」）。

> 爱司凯事故复盘（2026-08-13）：旧条件"买点上沿-建仓10%"（¥25.0）8/12 已触发并建仓 1100 股，8/13 现价 24.96 再次穿越被手动补录成新事件——若不校验条件状态会**重复建仓**。已修复：脚本层加 active 校验+对账，协议层加消费前置校验。

### 💡 消息试探仓建仓执行（strong-signal / 超跌反弹 / 价值反转，2026-08 定稿）

**三种触发源，同一执行框架**：
1. **strong-signal（强消息）**：newsdb 重要度≥4 + bullish + confidence≥3 的已证实强消息（原规则）
2. **粘滞flag确认豁免**（2026-08-18 加入，2026-08-22 改名，原超跌反弹豁免）：周线仅"见顶组合/高位回撤"粘滞 flag（非破位下行）+ 动量 10日∈[15%,25%] 甜点区 + 大盘 m20≤3%（急跌放行/阴磨不适用）+ 非连亏 + 日线站上5日线且收阳 → 分析 agent 判定后按消息试探仓执行（详见 stock-daily-analysis 纪律文档 3.0.1 粘滞flag确认豁免 + 3.4）
3. **价值反转通道**（2026-08-21 加入）：趋势门禁止 + 个股超跌（回撤>15%/周线破位 + 近2根日K止跌）+ **行业或公司级**已证实利好（行业事件需成分核心 rel≥70）+ 动量 ∈[-15%,+15%]（**负动量允许**，底部动量必负是常态；0~+15% 为反转确认窗口，>+15% 移交甜点区通道）+ 大盘非极端崩跌 + 非连亏 → 分析 agent 判定后按**价值反转试探仓**执行（建段 5% + 段内试探 **5-10%**，比消息仓更低档——左侧抄底胜率天然低于右侧；详见纪律文档 3.5）

当分析 agent 判定某标的满足上述任一触发源且建议"消息试探仓/价值反转试探仓"时，交易 agent 执行：

```bash
# 1. 建段：默认段 = 总池 5%（1000 万池 → 50 万）
ptrade2 master-pool-allocate "股票" --amount <池总资金×5%> --reason "消息试探仓-strong-signal"

# 2. 试探买入：段内 5-20%，agent 按消息正面强度定（一次性试探，不设区间）
#    普通强消息（重要度4）→ 低档 5-10%（50 万段 → 2.5-5 万）
#    极强消息（重要度5/业绩爆发/政策级）→ 高档 15-20%（50 万段 → 7.5-10 万）
ptrade2 buy "股票" --amount <段预算×试探比例>

# 3. 入场即设保护：成本保护 + 移动止损（peak−2.5×ATR）
#    常规仓：成本保护 -5% 或 2×ATR 取严者
#    价值反转仓：-12% 宽保护——--name 必须带"宽保护-12%（价值反转）"标记
#    （sync_cost_protection 识别该标记豁免 ATR 收紧；反转确认后 set 重设即去标记恢复 ATR，见纪律文档 3.5）
ptrade2 conditions "股票" --action set --type cost_protection --price <成本×0.95 或 0.88> --action-str "消息仓成本保护" --category hard --name "宽保护-12%（价值反转）"
ptrade2 atr-sync "股票"

# 4. 挂 CALENDAR 跟踪升级/到期（10 个交易日后评估；due 纯日期=当天 15:30 收盘后触发）
taskbus add CALENDAR "股票" --source message-position --priority 2 \
  --payload '{"due":"<10个交易日后>","event":"消息试探仓评估","check":"趋势门通过+动量15-25%则段内加仓/升级正式仓,否则减仓或降观察"}'
```

**纪律要点**：
- **默认建段 5%（总池）**；**试探买入 = 段内 5-20%**（普通强消息 5-10% / 极强消息 15-20%，agent 判断；**价值反转通道 = 段内 5-10% 更低档**），一次性试探不设区间；**绝不追高**（10日动量 >25% 禁止）
- **段内剩余弹药留作确认后加仓**（不用重新 allocate）；段内买满还需扩大 → `ptrade2 master-pool-topup` 注资扩展（**累计 ≤30%×总池 = 300 万**）
- 同标的消息试探仓**只开一次**；单日新开**最多 1 只**
- **⚠️ 执行前防循环核验（2026-08-21 补强，规则 14-16）**：建仓前必须查——① `ptrade2 operations "股票" --days 3` 近 2 个交易日内有止损清仓（成本保护/亏损止损/技术破位）→ **拒绝开仓**（止损隔离期，除非明确右侧信号）；② 止损清仓后建仓成本 > 止损卖出价 ×1.03 → 拒绝（成本抬高上限）；③ 已开过消息仓/价值反转仓的标的（查 operations 历史）→ 不重复开。防"止损→消息/反弹→立刻抄回→再止损"循环。
- 消息被证伪/业绩不及预期 → **无条件退出**，不扛单
- 持有上限 10 个交易日，到期未升级 → 平仓或降回观察
- 升级正式仓需趋势门通过 + 动量 15~25%（段内加仓/topup 路径，见 3.4.3）
- **持有期统一状态机（2026-08-22 加入，见纪律文档 3.0.0.1）**：入场后通道使命结束——**动量维度只决定加不加仓**（甜点区→确认档15-20%；甜点区+趋势门通过→强化档至30%；>25%不加仓；降回0~15%暂停加仓；转负不加仓），**价格维度只决定卖不卖**（成本保护/移动止损/利润梯度/目标价/证伪——见纪律文档 2.1-2.4）。**无动量止盈**（动量冲高≠卖出，移动止损锁盈）；**动量回落≠卖出**（保护位不破就持有观察）。

### 分析报告管理

```bash
# 保存分析报告
ptrade analysis 赛力斯 --action save --file report.md

# 查看最新分析
ptrade analysis 赛力斯 --action read

# 列出分析历史
ptrade analysis 赛力斯 --action list

# 查看所有已分析的股票
ptrade analysis all --action list
```

详细文档：[分析报告管理](references/analysis.md)

### 临时数据存储

支持的数据类别：
- `deep-search`: 深度搜索结果
- `history-continuity`: 历史连续性分析
- `gf-summary`: 广发证券摘要

```bash
# 保存临时数据（从文件读取）
ptrade temp-data 赛力斯 --action save --category deep-search --file search_result.md

# 保存临时数据（从 stdin 读取）
ptrade temp-data 赛力斯 --action save --category gf-summary --stdin << 'EOF'
# 广发证券数据分析
...
EOF

# 读取最新数据
ptrade temp-data 赛力斯 --action read --category deep-search
```

详细文档：[临时数据存储](references/temp-data.md)

## 使用指南

对于详细的功能说明、参数详解和最佳实践，请查阅 [references/](references/) 目录下的参考文档。

### 按场景查阅

| 场景 | 参考文档 |
|------|---------|
| 首次使用 | [基础交易操作](references/basic-operations.md) |
| 分析报告管理 | [分析报告管理](references/analysis.md) |
| 查看数据 | [查询命令说明](references/query-commands.md) |
| 管理多个账户 | [投资组合管理](references/portfolio-management.md) |
| 备份数据 | [数据管理](references/data-management.md) |
| 临时数据存储 | [临时数据存储](references/temp-data.md) |
| 市场数据分析 | [市场数据查询](references/market-data.md) |
| 条件管理 | [条件管理](references/conditions.md) |
| 交易策略 | [交易原则与策略](references/trading-principles.md) |
| 理解数据结构 | [数据存储结构](references/data-storage.md) |
| 解决问题 | [常见问题与故障排除](references/troubleshooting.md) |

### 按功能查阅

| 功能类别 | 参考文档 | 说明 |
|---------|---------|------|
| **基础交易** | [basic-operations.md](references/basic-operations.md) | 初始化、买入、卖出操作 |
| **分析报告** | [analysis.md](references/analysis.md) | 保存、读取、查询分析报告 |
| **数据查询** | [query-commands.md](references/query-commands.md) | 资金池、持仓、历史、收益 |
| **多账户管理** | [portfolio-management.md](references/portfolio-management.md) | 投资组合、性能分析 |
| **数据备份** | [data-management.md](references/data-management.md) | 导出、删除、恢复 |
| **临时数据** | [temp-data.md](references/temp-data.md) | 中间数据存储、读取、管理 |
| **市场数据** | [market-data.md](references/market-data.md) | 价格、K线、搜索、新闻 |
| **条件管理** | [conditions.md](references/conditions.md) | 标准条件、事件条件、纪律规则 |
| **交易策略** | [trading-principles.md](references/trading-principles.md) | 纪律、止盈止损 |
| **数据机制** | [data-storage.md](references/data-storage.md) | 文件结构、存储原理 |
| **故障排除** | [troubleshooting.md](references/troubleshooting.md) | 22个常见问题诊断 |

### Agent 使用建议

当需要详细说明时，请：

1. **先查阅对应参考文档** - 每个功能都有专门的详细文档
2. **理解核心机制** - [数据存储结构](references/data-storage.md) 解释了数据如何组织和计算
3. **遵循交易原则** - [交易原则与策略](references/trading-principles.md) 提供最佳实践
4. **遇到问题查阅** - [常见问题与故障排除](references/troubleshooting.md) 包含典型问题解决方案

**不要**在 SKILL.md 中查找详细的参数说明、错误处理或实现细节，这些都在参考文档中。
