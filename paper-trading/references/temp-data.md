# Paper Trading 临时数据存储功能使用指南

## 概述

Paper Trading CLI 提供了临时数据存储功能，用于保存和读取各种分析中间结果数据。这个功能特别适合保存深度搜索结果、历史分析连续性、广发证券数据等专业分析过程中生成的中间数据。

## 目录结构

```
workspace/
└── temp-data/
    └── <股票名称>/
        ├── <类别1>/
        │   ├── 2026-04-09-103015.md
        │   ├── 2026-04-09-110020.md
        │   └── 最新.md (软链接)
        ├── <类别2>/
        └── ...
```

**目录结构说明：**
- `temp-data/`: 临时数据根目录
- `<股票名称>/`: 每只股票独立的目录
- `<类别>/`: 按数据类别分类的子目录
- `最新.md`: 总是指向最新保存数据的软链接

## 支持的数据类别

虽然类别名称是自定义的，但建议使用以下约定：

| 类别名称 | 用途 | 示例 |
|---------|------|------|
| `deep-search` | 深度搜索的新闻和分析结果 | 多轮搜索收集的财经新闻、行业动态 |
| `history-continuity` | 历史分析连续性报告 | 预测准确性、观点演进、建议更新 |
| `gf-summary` | 广发证券专业数据 | 龙虎榜、财务基本面、ETF 联动数据 |
| `custom-analysis` | 自定义分析 | 任何其他类型的分析中间数据 |

## 命令使用

### 保存数据

支持三种方式保存数据：

**方式 1: 直接传入内容（`--content`）**

```bash
ptrade temp-data 赛力斯 --action save --category deep-search --content "# 分析内容"
```

适用于简短的分析内容或测试数据。

**方式 2: 从文件读取（`--file`）**

```bash
ptrade temp-data 赛力斯 --action save --category deep-search --file search_result.md
```

适用于已经保存为文件的分析结果。

**方式 3: 从 stdin 读取（`--stdin`）**

```bash
ptrade temp-data 赛力斯 --action save --category gf-summary --stdin << 'EOF'
# 广发证券数据分析
## 龙虎榜数据
...
EOF
```

适用于从其他程序输出的数据，支持多行格式化内容。

**注意事项：**
- 三种方式只能选择一种，不能同时使用
- `--content`、`--file`、`--stdin` 互斥
- 必须指定 `--category` 参数

### 读取数据

```bash
# 读取最新数据
ptrade temp-data 赛力斯 --action read --category deep-search
```

读取指定类别下最新保存的数据。如果该类别下没有数据，会返回错误。

### 列出数据

```bash
# 列出某股票所有类别
ptrade temp-data 赛力斯 --action list

# 列出某股票某类别的数据
ptrade temp-data 赛力斯 --action list --category deep-search

# 列出所有股票的数据
ptrade temp-data all --action list
```

用于查看已保存的数据记录。

## 文件命名规则

所有文件使用时间戳命名：`YYYY-MM-DD-HHMMSS.md`

例如：
- `2026-04-09-103015.md` - 2026年4月9日 10:30:15 保存的数据
- `2026-04-09-110020.md` - 2026年4月9日 11:00:20 保存的数据

**时间戳格式的优势：**
- 精确到秒，避免保存冲突
- 按时间自然排序
- 便于追踪数据的时效性

## 最新数据访问

每个类别目录下都有一个 `最新.md` 软链接，指向该类别下最新保存的数据文件。

**工作原理：**
```bash
# 每次保存数据时，自动更新软链接
# 假设目录内容：
total 12
-rw-r--r-- 1 user user 2048 Apr  9 10:30 2026-04-09-103015.md  # 旧数据
lrwxrwxrwx 1 user user   21 Apr  9 11:02 最新.md -> 2026-04-09-110020.md  # 指向最新
-rw-r--r-- 1 user user 3072 Apr  9 11:02 2026-04-09-110020.md  # 新数据
```

**读取时的行为：**
- 使用 `read` 操作时，如果未指定文件名，默认读取 `最新.md`
- 如果软链接不存在，返回错误
- 支持直接读取指定文件（通过文件名）

## 配置

### 工作空间配置

临时数据目录默认为 `workspace/temp-data/`，可以通过环境变量修改：

```bash
export STOCK_ANALYSIS_WORKSPACE=/custom/path
ptrade temp-data 赛力斯 --action save --category deep-search --content "测试"
# 数据保存到 /custom/path/temp-data/赛力斯/deep-search/
```

### 目录权限

程序会自动创建不存在的目录：
- `temp-data/` - 根目录
- `<股票名称>/` - 股票目录
- `<类别>/` - 类别目录

确保工作空间目录有写权限。

## 与分析报告的区别

| 特性 | 临时数据存储 | 分析报告 |
|------|-------------|---------|
| 用途 | 保存中间结果、特定类别数据 | 保存完整的股票分析文档 |
| 目录结构 | `temp-data/<股票>/<类别>/` | `stocks_analysis/<股票>/` |
| 文件名 | `YYYY-MM-DD-HHMMSS.md` | `<股票>-YYYY-MM-DD-HHMM.md` |
| 分类 | 支持多个类别（deep-search、gf-summary 等） | 每只股票统一存储 |
| 软链接 | 每个类别一个 `最新.md` | 一个 `最新分析.md` |
| 适用场景 | Agent 协作、数据交换 | 最终分析报告归档 |

## 典型使用场景

### 场景 1: Agent 协作

主 agent 调用多个 subagent 并行分析，每个 subagent 保存自己的结果：

```bash
# subagent 1: 深度搜索
ptrade temp-data 赛力斯 --action save --category deep-search --stdin < search_result.txt

# subagent 2: 历史分析
ptrade temp-data 赛力斯 --action save --category history-continuity --stdin < history_analysis.txt

# subagent 3: 广发数据
ptrade temp-data 赛力斯 --action save --category gf-summary --stdin < gf_data.txt

# 主 agent: 收集所有中间数据
ptrade temp-data 赛力斯 --action read --category deep-search
ptrade temp-data 赛力斯 --action read --category history-continuity
ptrade temp-data 赛力斯 --action read --category gf-summary
```

### 场景 2: 数据持久化

其他程序生成分析结果后保存：

```python
import subprocess

analysis_result = generate_analysis()

# 通过 stdin 传递给 ptrade
process = subprocess.Popen([
    'ptrade', 'temp-data', '赛力斯',
    '--action', 'save',
    '--category', 'custom-analysis',
    '--stdin'
], stdin=subprocess.PIPE)

process.communicate(input=analysis_result.encode('utf-8'))
```

### 场景 3: 数据归档

定期保存中间数据作为历史记录：

```bash
# 保存当前搜索结果
ptrade temp-data 赛力斯 --action save --category deep-search --file search_$(date +%Y%m%d).md

# 查看历史保存情况
ptrade temp-data 赛力斯 --action list --category deep-search
```

## 注意事项

### 1. 软链接支持

某些文件系统不支持软链接（如 FAT32），在这种情况下：
- 程序会静默失败，不报错
- `最新.md` 软链接不会被创建
- 读取操作会失败，因为没有"最新"指向
- 建议在支持的文件系统上使用（如 ext4、NTFS）

### 2. 并发写入

未实现锁机制，避免：
- 多个进程同时写入同一类别
- 同时保存相同股票的相同类别
- 建议按顺序写入或使用不同的类别

### 3. 股票名称验证

临时数据存储默认**不验证股票名称**（与分析报告不同）：
- 使用 `validate_stock=False` 初始化
- 可以保存任何股票名称的数据
- 适合保存临时数据或标准化前的数据

### 4. 类别名称规范

虽然类别名称是自由的，但建议：
- 使用小写字母和连字符 (`-`)
- 使用有意义的名称（如 `custom-rsi`, `custom-macd`）
- 避免特殊字符和空格
- 保持命名一致性

## 常见问题

**Q: 临时数据会占用太多磁盘空间吗？**

A: 使用时间戳命名，每个文件独立，不会覆盖旧数据。建议定期清理不需要的旧数据。

**Q: 可以手动编辑 `最新.md` 吗？**

A: 不建议。`最新.md` 是软链接，手动编辑会变成普通文件。正确做法是编辑实际文件，然后重新保存更新软链接。

**Q: 如何删除某个类别的所有数据？**

A: 直接删除对应目录即可：
```bash
rm -rf workspace/temp-data/赛力斯/deep-search
```

**Q: 可以同时保存不同数据吗？**

A: 不可以。三个选项（`--content`、`--file`、`--stdin`）互斥，只能选择一种方式。

**Q: 读取时指定文件名如何获取？**

A: 使用 `list` 操作查看文件列表，找到所需的文件名（包含时间戳）。
