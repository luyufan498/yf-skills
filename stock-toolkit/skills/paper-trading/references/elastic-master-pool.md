# 弹性组合总池（V2 ptrade2）

> 2026-08-10 上线。ptrade2 的核心资金模型，取代 v1"每只股票独立资金池"。

## 一、核心概念：池 ≠ 持仓 ≠ 预算

| 概念 | 定义 | v2 实现 |
|------|------|---------|
| **池（关注名单）** | 动态跟踪的股票列表 | `pool` 表（SQLite），可 30+ 只 |
| **持仓段** | 实际占用资金的仓位 | `position` 表，open 段 ≤20 |
| **预算** | 总资金池的分配 | `pool_ledger` 表，total 固定 1000 万 |

**关键**：预算只被 **open 段**占用。池成员空仓不占预算 → 资金利用率最大化，天然避免"名额闲置"。

## 二、三档策略

| 档位 | 池:持仓 | 入池节奏 | 空仓去留 | 买卖自主度 |
|------|---------|----------|----------|------------|
| **L1 锁定** | 人工，不限额 | 人工 | 永不移除 | 全人工；release/降级/变更一律 `--source manual`，AI 无权 |
| **L2 稳健** | 池可 15~30 只，持仓 ≤20 | 定期 + 事件驱动 | 留池（可降级） | agent 自主，释放需三重条件 |
| **L3 投机** | 池 30+ 只，持仓 ≤20 | 每日扫描候选 | 空仓即移除 | agent 完全自由，空仓即剔 |

> **有持仓= L1（2026-08 语义统一）**：触发买入（allocate 建段）即档位进 L1，L1 不限额持仓数；release 空仓后回落原档位（L2 留池/L3 可剔）。消息仓（小仓位试探）与正式建仓的区别只在仓位大小，不影响档位。

## 三、段生命周期

```
入池(打 strategy 标签) → 信号 → allocate(占段+拨预算+建账户) → buy/sell(引擎)
  → 空仓 → release(现值回 free + 段归档 + 7日冷却) → 池去留按策略(L2留/L3剔/L1永不)
```

- **allocate**：从 free 拨 budget → 建账户（SQLite 原子事务）。校验：free 足够、单股 ≤30%×total、总 open 段 <20、不在冷却期、无已 open 段。**初始段默认总池 5%**（策略矩阵见 trading-discipline 3.0.0），历史 3% 段保持现状不迁移。
- **topup**：段内追加弹药（账户 total/available += amount，free -= amount）。校验累计 ≤30%×total。
- **release**：空仓后现值回 free → 段 closed + 7 日 cooldown。L1 需 manual。
- **会计**：`free + Σ open 段现值 = total + 浮动盈亏`；`master-pool-show` 显示 `realized_pnl`。

## 四、命令

```
ptrade2 master-pool-init --amount 10000000    # 初始化总池（一次性）
ptrade2 master-pool-show                       # 总池状态 + 对账
ptrade2 master-pool-allocate 股 --amount N --reason 依据 [--source agent/manual] [--code]
ptrade2 master-pool-topup 股 --amount N --reason 依据
ptrade2 master-pool-release 股 --reason 依据 [--source agent/manual]
ptrade2 master-pool-records [--days N]         # 资金流水审计
ptrade2 watchlist-list | add | remove          # 池名单（add 带 --strategy/--source/--reason）
ptrade2 migrate-existing                       # 迁移 v1 JSON 账户 → SQLite 归档段
```

## 五、V2 资金纪律（详见 trading-discipline.md 八、）

1. 总预算 1000 万固定；超出部分是盈亏，不是新弹药。
2. 单股分配 ≤ 30%×total（含 topup 累计）。
3. 总持仓段 ≤ 20（有持仓= L1，全部计入；释放需按档位纪律）。
4. 现金保留 free ≥ 20%×total 作为弱市子弹底线；低于则审查以释放为主。
5. 释放冷却 7 日（防 whipsaw）；L1 人工不受。
6. 每次 allocate/topup/release 必须带 --reason 审计。
7. 候选须过完整分析（周线/短期/动量/连亏过滤），不得因"有闲钱"放松纪律。

## 六、数据存储（SQLite）

- `master_pool.db`：`pool`（名单）/ `position`（段）/ `pool_ledger`（账本）/ `audit`（流水）/ `watchlog`（变更审计）/ `operations_archive`（重入归档）。
- 账户/操作/条件：深迁移为 `accounts`/`operations`/`conditions`/`condition_history`/`exright_applied` 规范化表。
- v1 历史 JSON 迁移后移入 `tradings_archive/`（只读归档）。
