# 数据管理详解

本指南详细说明 paper-trading 的数据管理功能：数据导出、数据删除、备份与恢复。

## 目录

- [export 导出数据](#export-导出数据)
- [delete 删除账户](#delete-删除账户)
- [数据备份与恢复](#数据备份与恢复)
- [数据格式说明](#数据格式说明)

---

## export 导出数据

导出交易数据为 JSON 或 CSV 格式，方便保存、分析或迁移。

### 命令语法

```bash
ptrade export [--stock 股票名称] [--format 格式] [--output 输出文件]
```

### 参数说明

| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `--stock` / `-s` | 否 | 全部股票 | 指定导出的股票名称 |
| `--format` / `-f` | 否 | json | 导出格式：json 或 csv |
| `--output` / `-o` | 否 | stdout | 输出文件路径 |

### 使用示例

```bash
# 导出所有数据为 JSON，输出到控制台
ptrade export

# 导出指定股票为 JSON 文件
ptrade export --stock "赛力斯" --format json --output sels_data.json

# 导出指定股票为 CSV 文件
ptrade export --stock "赛力斯" --format csv --output sels_operations.csv

# 导出所有股票为 JSON 文件
ptrade export --format json --output all_stocks.json
```

### 导出内容

#### JSON 格式

JSON 格式导出所有账户信息，包括：

```json
{
  "version": "1.2.0",
  "export_time": "2026-04-07T10:30:00",
  "accounts": [
    {
      "stock_name": "股票名称",
      "stock_code": "sh600000",
      "capital_pool": {
        "total_capital": 100000,
        "available_capital": 50000,
        "used_capital": 50000
      },
      "positions": [...],
      "operations": [...]
    }
  ]
}
```

#### CSV 格式

CSV 格式导出操作记录，包括：

| 字段 | 说明 |
|------|------|
| 股票名称 | 股票中文名称 |
| 股票代码 | 股票代码 |
| 操作类型 | INIT / BUY / SELL |
| 价格 | 交易价格 |
| 数量 | 交易数量 |
| 金额 | 交易金额 |
| 成本 | 卖出成本（仅 SELL）|
| 盈亏 | 交易盈亏（仅 SELL）|
| 时间 | 操作时间 |
| 备注 | 用户备注 |

### 格式选择指南

| 格式 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| JSON | 完整结构、层级清晰、易于解析 | 需要专门工具查看 | 数据备份、程序处理 |
| CSV | 简洁、Excel 可直接打开 | 缺少部分复杂信息 | 人工查看、简单分析 |

### 最佳实践

```bash
# 定期备份所有数据
ptrade export --format json --output backup_$(date +%Y%m%d).json

# 每周导出操作记录
ptrade export --stock "赛力斯" --format csv --output operations_weekly_$(date +%W).csv

# 快速导出到控制台用于查看
ptrade export --stock "赛力斯" --format json | jq .
```

---

## delete 删除账户

删除指定股票的账户和所有相关数据。

### 命令语法

```bash
ptrade delete "股票名称" [--force]
```

### 参数说明

| 参数 | 必需 | 说明 |
|------|------|------|
| `股票名称` | 是 | 要删除的股票名称 |
| `--force` / `-f` | 否 | 强制删除（即使有持仓） |

### 使用示例

```bash
# 删除空账户（无持仓）
ptrade delete "已清仓股票"

# 强制删除有持仓的账户
ptrade delete "赛力斯" --force
```

### 删除条件

| 条件 | 需要 --force | 说明 |
|------|-------------|------|
| 无持仓 | ❌ 可直接删除 | 空账户可直接删除 |
| 有持仓 | ✅ 需要 --force | 有持仓的数据需强制删除 |
| 资金池空 | ✅ 需要 --force | 有资金但无持仓 |

### 安全检查

delete 命令会执行以下安全检查：

1. **验证账户存在**：确认账户已初始化
2. **检查持仓状态**：如有持仓，需使用 `--force`
3. **检查可用资金**：如有余额，需使用 `--force`
4. **确认删除操作**（`--force` 时无确认）

### 风险提示

⚠️ **删除操作不可逆！**

删除账户会永久删除以下数据：
- 资金池状态（account.json）
- 所有操作记录（operations.json）
- 收益和盈亏统计
- 历史交易数据

在执行删除前，建议：

```bash
# 1. 先导出数据备份
ptrade export --stock "股票名称" --output backup.json

# 2. 确认真的不再需要该账户
ptrade info "股票名称"

# 3. 确认无误后再删除
ptrade delete "股票名称" --force
```

### 何时使用 delete 命令

- 股票已清仓且确认不再关注
- 清理测试数据
- 重新开始（删除后重新初始化）
- 清理误创建的账户

---

## 数据备份与恢复

### 自动备份

建议定期自动备份数据：

```bash
# 每日备份脚本
#!/bin/bash
DATE=$(date +%Y%m%d)
BACKUP_DIR="/path/to/backups"
mkdir -p $BACKUP_DIR

# 导出所有数据
ptrade export --format json --output "$BACKUP_DIR/paper_trading_$DATE.json"

# 保留最近 30 天的备份
find $BACKUP_DIR -name "paper_trading_*.json" -mtime +30 -delete
```

### 手动备份

```bash
# 备份单个股票
ptrade export --stock "赛力斯" --output backup_sels_$(date +%Y%m%d).json

# 备份所有股票
ptrade export --output backup_all_$(date +%Y%m%d).json

# 直接备份 JSON 文件
cd intermediate
tar -czf backup_$(date +%Y%m%d).tar.gz */
```

### 恢复数据

paper-trading 本身不提供"恢复"命令，因为恢复数据需要谨慎操作。以下为恢复建议：

#### 方法 1：重新初始化

```bash
# 如果备份了操作记录，可以手动重新执行
ptrade init "股票名称" --capital 100000
ptrade buy "股票名称" --qty 100 --note "从备份恢复"
# ... 重复之前的操作
```

**优点**：操作透明，易于验证
**缺点**：耗时，需要手动执行

#### 方法 2：直接恢复 JSON 文件

```bash
# 解压备份
tar -xzf backup_20260407.tar.gz

# 复制回原位置
cp -r intermediate/股票名称/ /path/to/current/intermediate/
```

**优点**：快速，完全恢复
**缺点**：可能覆盖现有数据，需谨慎

#### 方法 3：使用脚本解析备份导入

可以编写脚本解析 JSON 备份文件，然后调用 ptrade 命令重建账户：

```python
import json

# 读取备份
with open('backup.json', 'r') as f:
    data = json.load(f)

# 遍历账户重建
for account in data['accounts']:
    stock_name = account['stock_name']
    capital = account['capital_pool']['total_capital']

    # 重建账户
    print(f'ptrade init "{stock_name}" --capital {capital}')

    # 重建操作
    for op in account['operations']:
        if op['type'] == 'BUY':
            print(f'ptrade buy "{stock_name}" --qty {op["qty"]} --note "恢复"')
        elif op['type'] == 'SELL':
            print(f'ptrade sell "{stock_name}" --qty {op["qty"]} --note "恢复"')
```

---

## 数据格式说明

### account.json 结构

```json
{
  "stock_name": "股票名称",
  "stock_code": "sh600000",
  "capital_pool": {
    "total_capital": 100000,
    "available_capital": 50000,
    "used_capital": 50000
  },
  "positions": [
    {
      "qty": 200,
      "cost": 10.5,
      "cost_amount": 2100
    }
  ],
  "updated_at": "2026-04-07T10:30:00"
}
```

### operations.json 结构

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
    "qty": 200,
    "amount": 2000,
    "note": "首次建仓"
  },
  {
    "time": "2026-04-07T11:00:00",
    "type": "SELL",
    "price": 12.0,
    "qty": 100,
    "amount": 1200,
    "cost": 1000,
    "profit": 200,
    "note": "部分止盈"
  }
]
```

---

## 相关命令

- `ptrade list` - 列出所有账户
- `ptrade info` - 查看账户信息
- `ptrade export` - 导出数据
- `ptrade delete` - 删除账户

---

## 进阶话题

参考以下文档了解更多：

- **[数据存储结构](data-storage.md)** - 数据文件详细结构
- **[查询命令说明](query-commands.md)** - 如何查询数据
- **[常见问题与故障排除](troubleshooting.md)** - 常见问题的解决方案
