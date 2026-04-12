# 分析报告管理

## 功能概述

分析报告管理功能提供股票分析报告的保存、读取和查询功能。

## 目录结构

分析报告存储在 `<workspace_root>/stocks_analysis` 目录下：

```
<workspace_root>/
└── stocks_analysis/
    ├── 股票名称A/
    │   ├── 股票名称A-2026-04-08-1430.md  # 分析报告（时间戳格式）
    │   ├── 股票名称A-2026-04-08-1500.md
    │   └── 最新分析.md -> 股票名称A-2026-04-08-1500.md  # 软链接，指向最新报告
    └── 股票名称B/
        ├── 股票名称B-2026-04-08-1600.md
        └── 最新分析.md -> 股票名称B-2026-04-08-1600.md
```

文件命名格式：`{股票名称}-{YYYY-MM-DD}-{HHMM}.md`

## 文件命名规则

固定使用 timestamp 模式：`{股票名称}-YYYY-MM-DD-HHMM.md`

例如：
- `赛力斯-2026-04-08-1430.md` - 2026年4月8日14:30的分析报告
- `特斯拉-2026-04-09-0915.md` - 2026年4月9日09:15的分析报告

## CLI 命令

### 保存分析报告

```bash
# 直接传入内容
ptrade analysis 赛力斯 --action save --content "# 分析报告内容"

# 从文件读取
ptrade analysis 赛力斯 --action save --file analysis.md

# 从标准输入读取
echo "# 分析" | ptrade analysis 赛力斯 --action save --content -
```

### 读取分析报告

```bash
# 读取最新的分析报告
ptrade analysis 赛力斯 --action read
```

### 列出分析记录

```bash
# 列出某股票的分析记录（默认最近10条）
ptrade analysis 赛力斯 --action list

# 列出更多记录
ptrade analysis 赛力斯 --action list --limit 20

# 列出所有已分析的股票
ptrade analysis all --action list
```

## Python API

### 创建管理器

```python
from paper_trading.analysis import AnalysisManager

# 使用默认配置
manager = AnalysisManager()

# 指定基础目录
manager = AnalysisManager(base_dir="/path/to/workspace")
```

### 保存分析

```python
from datetime import datetime

record = manager.save_analysis(
    stock_name="赛力斯",
    content="# 分析报告\n\n这是分析内容",
    stock_code="sh603527",
    timestamp=datetime.now()
)

print(f"保存至: {record.file_path}")
```

### 读取分析

```python
# 读取最新分析
record = manager.read_analysis("赛力斯")
if record:
    print(record.content)

# 读取指定文件
record = manager.read_analysis("赛力斯", "赛力斯-2026-04-08-1430.md")
```

### 列出分析

```python
# 列出某股票的分析记录
files = manager.list_analyses("赛力斯", limit=10)
for f in files:
    print(f)

# 列出所有已分析的股票
stocks = manager.list_stocks()
print(stocks)
```

## 注意事项

1. **股票名称验证**：默认启用股票名称验证，通过 paper-trading 的 StockCodeSearcher 验证股票名称的合法性
   - 保存时会自动查询股票代码
   - 如果股票名称无法识别会抛出 ValueError
   - 可通过 `validate_stock=False` 禁用验证

2. **文件名唯一性**：文件名包含时间戳（精确到分钟），避免重复

3. **软链接**：每次保存会自动更新 `最新分析.md` 软链接

5. **工作空间**：默认使用 `<workspace_root>/stocks_analysis/` 目录（workspace_root 默认为脚本所在目录）

6. **环境变量**：可通过 `STOCK_ANALYSIS_WORKSPACE` 环境变量自定义工作空间根目录
