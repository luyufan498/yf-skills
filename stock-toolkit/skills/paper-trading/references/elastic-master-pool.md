# 弹性组合总池（V2 ptrade2）

> 2026-08-10 上线。ptrade2 的核心资金模型，取代 v1"每只股票独立资金池"。
> 2026-09-01 sleeve-m1 起双资金池 + 两组两层（L3 Sleeve 架构，方案 2.1/2.5/3.1）。

## 一、核心概念：池 ≠ 持仓 ≠ 预算

| 概念 | 定义 | v2 实现 |
|------|------|---------|
| **池（关注名单）** | 动态跟踪的股票列表 | `pool` 表（SQLite），可 30+ 只 |
| **持仓段** | 实际占用资金的仓位 | `position` 表，open 段 ≤20（技术组；sleeve 成员段 strategy='NEWS' 不占主池段位） |
| **预算** | 总资金池的分配 | `pool_ledger`（趋势池）+ `sleeve_ledger`（消息池 ≤总资金 20%）双账本，互不透支 |

**关键**：预算只被 **open 段**占用。池成员空仓不占预算 → 资金利用率最大化，天然避免"名额闲置"。

## 二、两组两层制（2026-09-01，原三档作废）

| 组 / 层 | 池:持仓 | 入层节奏 | 空仓/退出 | 交易自主度 |
|---------|---------|----------|-----------|------------|
| **技术组 L2 待命**（原 L2/L3 合并） | 池可 15~30 只 | 定期 + 事件驱动 | 留池（archived 需新证据回池） | 零交易权限；可挂建仓点/复检点（价格语言） |
| **技术组 L1 持仓段** | 持仓 ≤20 段 | allocate（L2 挂点触发或主动） | release 回资金；SLEEVE_ARCHIVE_ON_CLEAR=1 → archived 终态 / flag 关 → 降回 L2 | agent 自主（三件套+ATR+甜点加仓），L1 人工段 AI 无权 |
| **消息组 L2 信号缓冲**（strategy='NEWS'） | 池表留置 | 收编扫描/晨审（G1-G4 清单闸） | TTL 2 交易日作废 | **零交易权限**：禁价格点/禁 conditions |
| **消息组 L1 事件槽**（sleeve） | 20 事件坑 | sleeve-open（晨审判定，T+1 开盘等权成交） | 保护链/论点失效[灰度=影子]/sleeve-migrate；全成员清零槽 closed | 仅保护链机械执行；加仓锁死（灰度） |

> **有持仓= L1（语义统一）**：技术组触发 allocate 即升 L1；消息组建槽即 L1（实体是事件槽，
> 池行 strategy 保持 'NEWS'，成员身份以 `event_slot_members` 表为权威）。
> **同票双组**：同票两个 open 段并存（L1+NEWS），组由段 strategy 推导（v9 无 grp 列）；冲突进晨审人工裁决。

## 三、段生命周期

```
技术组：入池(L2 待命) → 建仓点触发 → allocate(占段+拨预算+段直建,升L1) → buy/sell
        → 空仓 → release(现值回 free + 段归档 + 7日冷却) → 池去向(flag 开=archived 终态 / 关=降 L2)
消息组：收编(G1-G4/TTL) → sleeve-open(sleeve_ledger 扣款+成员 NEWS 段+pending 单+开槽)
        → sleeve-fill(开盘价成交+挂三件套) → 保护链退出 → 槽 partial/closed（sleeve-close-slot 对账）
移交桥：sleeve-migrate（单向一次，原成本结转+双 ledger 对转+加仓锁，禁回迁）
```

- **allocate**：从 free 拨 budget → 段直建（v9 段即账户，段吸收 cash/FIFO；SQLite 原子事务）。校验：free 足够、单股 ≤30%×total、总 open 段 <20、不在冷却期、无已 open 段。**初始段默认总池 5%**（策略矩阵见 trading-discipline 3.0.0）。
- **topup**：段内追加弹药（段 budget/cash += amount，free -= amount）。校验累计 ≤30%×total；消息组（NEWS 段）禁 topup（闸门）。
- **release**：空仓后现值回 free → 段 closed + 7 日 cooldown。L1 需 manual。回落语义见 flag。
- **sleeve-open**：sleeve_ledger 扣款 → 成员 NEWS 段（等权段直建）→ pending 待成交单 → event_slots 开槽；G3 活跃槽并入不加坑、关闭槽二波=新键；坑上限 20（与主池 20 段位并存互不侵占）。
- **sleeve-fill**：pending → 按当日开盘价成交 + 挂三件套（atr.py 常量同源：cost 2.0×ATR / trail 2.5×ATR）。
- **会计**：`free + Σ open 段现值 = total + 浮动盈亏`（双池各自成立）；`master-pool-show --pool sleeve` 显示槽占用/活跃槽/已实现。

## 四、命令

```
ptrade2 master-pool-init --amount 10000000    # 初始化趋势池（一次性）
ptrade2 master-pool-show [--pool main|sleeve]  # 池状态 + 对账
ptrade2 master-pool-allocate 股 --amount N --reason 依据 [--source agent/manual] [--code]
ptrade2 master-pool-topup 股 --amount N --reason 依据
ptrade2 master-pool-release 股 --reason 依据 [--source agent/manual]
ptrade2 master-pool-records [--days N]         # 资金流水审计
ptrade2 watchlist-list | add | remove [--archive]   # 池名单（NEWS 收编带 --event-key/--news-kind）
ptrade2 sleeve-pool-init --amount <主池20%>     # 初始化消息池（一次性；资金从主池配对划拨，主池同事务扣减，M1.8）
ptrade2 sleeve-show                            # 消息池状态 + 事件槽清单
ptrade2 sleeve-open 股A 股B --budget N --event-key ND#293 --news-kind policy  # 开槽（等权）
ptrade2 sleeve-fill [--event-key K] [--price 股=价 ...] [--atr ...]  # 开盘成交+挂三件套
ptrade2 sleeve-cancel <event_key> --reason     # 弃单（影子账#1，坑释放）
ptrade2 sleeve-migrate 股 --reason "V11 依据"   # 移交桥（单向一次）
ptrade2 sleeve-close-slot <event_key> --reason # 槽对账归档
ptrade2 migrate-existing                       # v1 JSON 导入（一次性，v9 起显式拒绝——账户层已退役）
ptrade2 reconcile                              # 资金恒等式对账（U7.5，只报不拦；心跳尾步/晨审接）+ 总量守恒门（双池 Σtotal vs 注入基准 10M，M1.8）
```

## 五、V2 资金纪律（详见 trading-discipline.md 八、）

1. 趋势池 1000 万固定；消息池 = 总资金 20%（sleeve_ledger），互不透支——两个独立资金操作入口。
2. 单股分配 ≤ 30%×total（含 topup 累计，技术组）。
3. 总持仓段 ≤ 20（有持仓= L1，全部计入；sleeve 成员段不占主池段位）；消息组 20 事件坑独立计数。
4. 现金保留 free ≥ 20%×total 作为弱市子弹底线；低于则审查以释放为主。
5. 释放冷却 7 日（防 whipsaw）；L1 人工不受；消息组槽释放无冷却（closed 即复用，无 FIFO）。
6. 每次 allocate/topup/release/sleeve-open 必须带 --reason 审计。
7. 技术组候选须过完整分析；消息组候选须过 G1-G4 清单闸（判决权给清单，agent 当会计不当法官）。

## 六、数据存储（SQLite v7）

- `master_pool.db`：`pool`（名单，v7+event_key/archived_at）/ `position`（段）/ `pool_ledger`（趋势池账本）+ `sleeve_ledger`（消息池账本）/ `audit`（流水）/ `watchlog`（变更审计，v7+event_key/news_kind）/ `operations_archive`（重入归档）。
- v7 新表：`event_slots`（事件槽状态机）/ `event_slot_members`（事件↔成员关联权威）/ `shadow_log`（影子账 9 类 + gate_violation）。
- 段/流水/条件（v9 账户层退役后）：`position` 段表（+cash/fifo_index/fifo_offset——**段即账户**，资金标签=budget、段现金=cash）/ `trades`（原 positions 更名，FIFO 流水，account_id 语义=段 id）/ `operations`/`conditions`/`condition_history`/`exright_applied` 规范化表 / `accounts_old`（退役账户只读历史，禁 DROP）/ `positions` 兼容视图（INSTEAD OF 触发器垫片，v10 删）。
- v1 历史 JSON 迁移后移入 `tradings_archive/`（只读归档）。
