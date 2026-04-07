# 数据存储结构详解

本指南详细说明 paper-trading 的数据存储机制、文件结构和存储路径。

## 目录

- [存储路径](#存储路径)
- [文件结构](#文件结构)
- [account.json 详细说明](#accountjson-详细说明)
- [operations.json 详细说明](#operationsjson-详细说明)
- [数据格式演变](#数据格式演变)
- [数据迁移](#数据迁移)
- [旧数据兼容](#旧数据兼容)

---

## 存储路径

### 路径优先级

paper-trading 数据存储遵循以下优先级：

1. **最高优先级**：环境变量 `STOCK_INTERMEDIATE_DIR`
2. **次优先级**：环境变量 `STOCK_ANALYSIS_WORKSPACE/intermediate`
3. **默认路径**：项目默认的 `intermediate/` 目录

### 环境变量配置

```bash
# 方式 1：直接指定 intermediate 目录
export STOCK_INTERMEDIATE_DIR=/path/to/intermediate

# 方式 2：指定工作空间，自动加上 /intermediate
export STOCK_ANALYSIS_WORKSPACE=/path/to/workspace

# 方式 3：不设置，使用默认路径
# 默认路径：./intermediate/
```

### 推荐配置

```bash
# 在 ~/.bashrc 或 ~/.zshrc 中配置
export STOCK_ANALYSIS_WORKSPACE=/path/to/workspace
```

这样数据会存储在：`/path/to/workspace/intermediate/`

### 查看当前路径

可以通过 ptrade 命令确认数据存储位置：

```bash
ptrade info  # 在输出中可以看到相关路径信息
```

---

## 文件结构

### 目录结构

```
intermediate/
└── <股票名称>/
    └── 模拟买卖/
        ├── account.json      # 当前持仓、资金池状态
        └── operations.json   # 历史操作记录
```

### 示例

```
intermediate/
├── 赛力斯/
│   └── 模拟买卖/
│       ├── account.json
│       └── operations.json
├── 浦发银行/
│   └── 模拟买卖/
│       ├── account.json
│       └── operations.json
└── 中科曙光/
    └── 模拟买卖/
        ├── account.json
        └── operations.json
```

---

## account.json 详细说明

account.json 存储当前持仓和资金池状态，是交易系统的核心数据文件。

### 完整结构

```json
{
  "stock_name": "股票名称",
  "stock_code": "sh600000",
  "updated_at": "2026-04-07T10:30:00",
  "capital_pool": {
    "total_capital": 100000,
    "available_capital": 50000,
    "used_capital": 50000
  },
  "positions": [
    {
      "qty": 100,
      "cost": 10.0,
      "cost_amount": 1000,
      "updated_at": "2026-04-07T10:20:00"
    },
    {
      "qty": 100,
      "cost": 12.0,
      "cost_amount": 1200,
      "updated_at": "2026-04-07T10:25:00"
    }
  ]
}
```

### 字段说明

#### 根级字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `stock_name` | string | 股票名称 | "赛力斯" |
| `stock_code` | string | 股票代码 | "sh603527" |
| `updated_at` | string | 最后更新时间（ISO 8601）| "2026-04-07T10:30:00" |

#### capital_pool 对象

| 字段 | 类型 | 说明 | 计算 |
|------|------|------|------|
| `total_capital` | float | 初始化资金金额 | init 时设定的 capital |
| `available_capital` | float | 可用资金（剩余）| total - used |
| `used_capital` | float | 占用资金（持仓成本）| Σ(positions.cost_amount) |

#### positions 数组

| 字段 | 类型 | 说明 | 计算方式 |
|------|------|------|---------|
| `qty` | integer | 该批次买入数量 | 买入操作的数量 |
| `cost` | float | 该批次成本价 | 买入操作的价格 |
| `cost_amount` | float | 该批次成本金额 | qty × cost |
| `updated_at` | string | 该批次更新时间 | 买入操作的时间 |

### 数据更新逻辑

#### 初始化时

```json
{
  "total_capital": 100000,
  "available_capital": 100000,
  "used_capital": 0
}
```

#### 买入后

```json
// 假设买入 100 股 @ 10 元
{
  "total_capital": 100000,
  "available_capital": 90000,     // 100000 - 1000
  "used_capital": 1000,          // 0 + 1000
  "positions": [
    {
      "qty": 100,
      "cost": 10.0,
      "cost_amount": 1000
    }
  ]
}
```

#### 再买入后

```json
// 再买入 100 股 @ 12 元
{
  "total_capital": 100000,
  "available_capital": 78000,     // 90000 - 1200
  "used_capital": 2200,          // 1000 + 1200
  "positions": [
    {
      "qty": 100,
      "cost": 10.0,
      "cost_amount": 1000
    },
    {
      "qty": 100,
      "cost": 12.0,
      "cost_amount": 1200
    }
  ]
}
```

#### 卖出后（FIFO）

```json
// 卖出 100 股（从最早的批次开始，即 10 元那批）
{
  "total_capital": 100000,
  "available_capital": 90000,     // 78000 + 12000（扣除成本回收）
  "used_capital": 1000,          // 2200 - 1200（卖出批次成本）
  "positions": [
    // 第一个批次卖出，从数组中移除
    {
      "qty": 100,
      "cost": 12.0,
      "cost_amount": 1200
    }
  ]
}
```

### 数据计算

#### 总持仓数量

```python
total_qty = sum(position.qty for position in positions)
```

#### 平均成本价

```python
total_cost = sum(position.cost_amount for position in positions)
avg_cost = total_cost / total_qty
```

#### 持仓成本

```python
total_cost_amount = sum(position.cost_amount for position in positions)
```

#### 占用资金

```python
used_capital = sum(position.cost_amount for position in positions)
```

#### 可用资金

```python
available_capital = total_capital - used_capital
```

---

## operations.json 详细说明

operations.json 记录所有历史操作，是交易的可追溯记录。

### 完整结构

```json
[
  {
    "time": "2026-04-07T10:00:00",
    "type": "INIT",
    "capital": 100000,
    "note": "初始化资金池"
  },
  {
    "time": "2026-04-07T10:05:00",
    "type": "BUY",
    "price": 10.0,
    "qty": 100,
    "amount": 1000,
    "note": "首次建仓"
  },
  {
    "time": "2026-04-07T10:10:00",
    "type": "BUY",
    "price": 12.0,
    "qty": 100,
    "amount": 1200,
    "note": "加仓"
  },
  {
    "time": "2026-04-07T10:20:00",
    "type": "SELL",
    "price": 15.0,
    "qty": 100,
    "amount": 1500,
    "cost": 1000,
    "profit": 500,
    "note": "部分止盈"
  },
  {
    "time": "2026-04-07T10:30:00",
    "type": "SELL",
    "price": 14.0,
    "qty": 100,
    "amount": 1400,
    "cost": 1200,
    "profit": 200,
    "note": "清仓"
  }
]
```

### 字段说明

#### 通用字段（所有操作）

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `time` | string | 操作时间（ISO 8601）| "2026-04-07T10:00:00" |
| `type` | string | 操作类型 | INIT / BUY / SELL |
| `note` | string | 用户备注 | "首次建仓" |

#### INIT 操作字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `capital` | float | 初始化资金金额 | 100000 |

#### BUY 操作字段

| 字段 | 类型 | 说明 | 计算 |
|------|------|------|------|
| `price` | float | 买入价格 | API 获取的实时价格 |
| `qty` | integer | 买入数量 | 用户指定或从金额计算 |
| `amount` | float | 买入金额 | price × qty |

#### SELL 操作字段

| 字段 | 类型 | 说明 | 计算 |
|------|------|------|------|
| `price` | float | 卖出价格 | API 获取的实时价格 |
| `qty` | integer | 卖出数量 | 用户指定 |
| `amount` | float | 卖出金额 | price × qty |
| `cost` | float | 卖出成本 | FIFO 原则计算 |
| `profit` | float | 卖出盈亏 | amount - cost |

### 操作类型说明

| 操作类型 | 说明 | 触发条件 |
|---------|------|---------|
| INIT | 初始化资金池 | `ptrade init` 命令 |
| BUY | 买入股票 | `ptrade buy` 命令 |
| SELL | 卖出股票 | `ptrade sell` 命令 |

### FIFO 成本计算示例

```json
// account.json 中的 positions
"positions": [
  {"qty": 100, "cost": 10.0, "cost_amount": 1000},  // 最早的批次
  {"qty": 100, "cost": 12.0, "cost_amount": 1200},  // 第二批次
  {"qty": 100, "cost": 13.0, "cost_amount": 1300}   // 第三批次
]

// 卖出 150 股的 operations.json 记录
{
  "type": "SELL",
  "qty": 150,
  "amount": 1800,        // 150 股 × 12 元/股
  "cost": 1600,         // 100×10 + 50×12
  "profit": 200         // 1800 - 1600
}

// 卖出后的 account.json
"positions": [
  // 第一个批次全部卖出，移除
  {"qty": 50, "cost": 12.0, "cost_amount": 600},    // 第二批次剩余 50 股
  {"qty": 100, "cost": 13.0, "cost_amount": 1300}  // 第三批次不变
]
```

---

## 数据格式演变

### 旧格式（holdings.json）

早期版本使用 `holdings.json` 存储数据：

```json
{
  "holdings": [
    {
      "stock_name": "股票名称",
      "qty": 100,
      "avg_cost": 10.0
    }
  ]
}
```

**问题**：
- 决策类操作（如"关注"）干扰计算
- 无法精确追踪多批次买入成本
- 不支持 FIFO 成本计算

### 新格式（account.json + operations.json）

新格式解决上述问题：

```json
// account.json：当前状态
{
  "positions": [{"qty": 100, "cost": 10.0}],
  "capital_pool": {...}
}

// operations.json：历史记录
[
  {"type": "BUY", "qty": 100, "cost": 10.0},
  {"type": "SELL", "qty": 50, "cost": 10.0}
]
```

**优点**：
- 清晰分离状态和历史
- 支持多批次成本追踪
- 支持 FIFO 成本计算
- 决策类操作与交易操作分离

---

## 数据迁移

### 自动迁移机制

系统启动时会自动检测旧格式数据并迁移：

1. **检测 `holdings.json`**：如果存在，说明是旧格式
2. **读取 `holdings.json`**：解析旧数据
3. **计算净持仓**：忽略决策类操作
4. **生成新格式**：创建 `account.json` 和 `operations.json`
5. **保留旧文件**：不删除 `holdings.json`，作为备份

### 手动迁移（不推荐）

如果需要手动控制迁移：

```bash
# 1. 备份旧数据
cd intermediate/股票名称/模拟买卖/
cp holdings.json holdings.json.backup

# 2. 编辑生成新格式
# 手动根据旧数据生成 account.json 和 operations.json

# 3. 验证
ptrade info "股票名称"

# 4. 删除旧文件（确认无误后）
rm holdings.json
```

---

## 旧数据兼容

### 兼容性行为

系统对旧格式数据的处理：

| 操作 | 旧数据行为 | 新数据行为 |
|------|----------|----------|
| init | 覆盖 holdings.json | 覆盖 account.json |
| buy/sell | 修改 holdings.json | 修改 account.json + operations.json |
| info | 读取 holdings.json | 读取 account.json |
| operations | 空（旧格式无历史记录）读取 operations.json |

### 识别旧数据

```bash
# 方法 1：查看文件列表
ls intermediate/股票名称/模拟买卖/

# 如果看到 holdings.json → 旧格式
# 如果看到 account.json → 新格式

# 方法 2：使用 ptrade 命令查看
ptrade operations "股票名称"

# 如果显示"暂无操作记录" → 可能是旧格式
```

### 迁移确认

```bash
# 迁移后验证
ptrade info "股票名称"  # 检查持仓和资金
ptrade operations "股票名称"  # 应该有历史记录
```

---

## 数据完整性

### 数据校验

系统会自动验证以下数据：

1. **资金平衡**：`total_capital == available + used`
2. **持仓非负**：所有持仓数量 >= 0
3. **成本一致性**：`used_capital == sum(positions.cost_amount)`
4. **操作时间顺序**：operations.json 按时间顺序排列

### 数据损坏处理

如果数据文件损坏或格式错误：

```bash
# 1. 查看错误信息
ptrade info "股票名称"  # 系统会报告错误

# 2. 检查 JSON 格式
cat intermediate/股票名称/模拟买卖/account.json | jq .

# 3. 手动修复或删除
cd intermediate/股票名称/模拟买卖/
# 编辑修复 JSON 文件
# 或者
rm account.json operations.json  # 然后重新初始化
ptrade init "股票名称" --capital 100000 --force
```

---

## 数据备份建议

### 定期备份

```bash
# 创建备份目录
mkdir -p ~/backups/paper_trading

# 定期备份脚本
#!/bin/bash
DATE=$(date +%Y%m%d)
cp -r intermediate ~/backups/paper_trading/backup_$DATE

# 压缩备份
tar -czf ~/backups/paper_trading/backup_$DATE.tar.gz intermediate

# 保留最近 7 天
find ~/backups/paper_trading/ -name "backup_*.tar.gz" -mtime +7 -delete
```

### 导出备份

```bash
# JSON 格式完整备份
ptrade export --format json --output data_backup_$(date +%Y%m%d).json
```

---

## 相关命令

- `ptrade export` - 导出数据
- `ptrade info` - 查看数据
- `ptrade operations` - 查看操作历史
- `ptrade delete` - 删除数据

---

## 进阶话题

参考以下文档了解更多：

- **[数据管理](data-management.md)** - 数据导出、删除和备份
- **[基础交易操作](basic-operations.md)** - 交易命令详解
- **[查询命令说明](query-commands.md)** - 查询数据命令
