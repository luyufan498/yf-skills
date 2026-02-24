---
name: siyuan-notes
description: 思源笔记全功能命令行工具，支持笔记本、文档和块操作。适用于操作本地思源笔记（http://127.0.0.1:6806）的需求：(1) 笔记本管理（列表、创建、删除、重命名、打开/关闭），(2) 文档操作（创建、读取、更新、移动、删除），支持自动上传本地 assets 资源文件，(3) 块级编辑（更新、追加、前置、移动、删除块），(4) 内容搜索和 SQL 查询，(5) 导出为 Markdown/ZIP（自动移除 front matter），(6) 资源文件上传和下载。需要设置 SIYUAN_TOKEN 环境变量。
---

# 思源笔记命令行工具

## 概述

完整的思源笔记操作命令行接口。所有脚本位于 `scripts/` 目录下，运行需要：
- 脚本会读取 `SIYUAN_ENDPOINT` 环境变量配置的端点来进行访问
- 脚本会读取 `SIYUAN_TOKEN` 环境变量配置的 API token
- 在使用前，请检查环境变量已经正确设置

获取 API token：思源设置 → 关于 → API token

## 快速开始

```bash
# 进入脚本目录
cd scripts

# 列出所有笔记本
python3 siyuan nb list

# 查看文档
python3 siyuan doc show <文档ID>

# 搜索内容
python3 siyuan query search "关键词"
```

## 笔记本操作 (nb)

```bash
python3 siyuan nb list                     # 列出所有笔记本
python3 siyuan nb list --tree              # 树状视图
python3 siyuan nb create "名称"            # 创建笔记本
python3 siyuan nb remove "名称"            # 删除笔记本
python3 siyuan nb rename "旧名" "新名"      # 重命名
python3 siyuan nb open "名称"              # 打开笔记本
python3 siyuan nb close "名称"             # 关闭笔记本
python3 siyuan nb conf "名称" get          # 获取配置
python3 siyuan nb conf "名称" set --key icon --value "📚"
```

## 文档操作 (doc)

```bash
python3 siyuan doc list "笔记本"          # 列出文档
python3 siyuan doc list "笔记本" --tree   # 树状视图
python3 siyuan doc show <文档ID>          # 查看文档
python3 siyuan doc cat <文档ID>           # 仅显示文档内容
python3 siyuan doc info <文档ID>          # 文档元信息

# 创建新文档
# ⚠️ 重要：第2个参数是"父文档路径"（字符串路径），不是文档 ID！

# 📋 父路径选择原则：
# - 使用 "/"（根目录）作为父路径：
#   • 笔记本已经是细分领域，直接在笔记本下平铺文档
#   • 例如："Go语言笔记"笔记本下直接创建"并发编程"、"反射机制"等独立文档
# - 使用 "/父文档路径" 作为父路径：
#   • 笔记本本身内容分散，描述很多不同的东西，需要创建二层树结构来组织
#   • 创建的文档是某个父文档的子章节或补充说明
#   • 例如：在"/项目文档"下创建"需求分析"、"技术方案"、"测试报告"等子文档
#   • 注意：笔记本本身已经是文档树的一层，通常直接在根目录创建即可

python3 siyuan doc create "笔记本" "/" --title "标题" --content "正文内容"  # 直接在笔记本根目录创建
python3 siyuan doc create "笔记本" "/父文档路径" --title "子标题" --content "正文内容"  # 在父文档下创建子文档

# 💡 通过 ID 在父文档下创建子文档（推荐）：
# 步骤 1: 先在根目录创建文档
python3 siyuan doc create "笔记本" "/" --title "子标题" --content "正文内容"
# 步骤 2: 获取新创建的文档 ID（从命令输出中复制）
# 步骤 3: 使用 move 命令移动到父文档下
python3 siyuan doc move <新文档ID> --to <父文档ID>

# 🛡️ 路径验证：
# 脚本会自动验证父路径是否存在，如果不存在会显示详细错误提示
# 如果确实需要在不存在路径下创建（创建新的文档结构），请使用 --force 参数
python3 siyuan doc create "笔记本" "/不存在的路径" --title "标题" --force

# 💡 自动资源上传：
# 如果文档内容包含 assets/ 引用（如 ![alt](assets/image.png)），
# 且本地 assets 目录下存在对应文件，会自动上传到思源并更新引用路径

python3 siyuan doc create "笔记本" "/" --title "文档标题" --content "## 二级标题\n正文内容..."
# 注意：如果 content 第一行是 "# 标题" 且与 --title 相同，脚本会自动移除该行，避免重复

python3 siyuan doc rename <文档ID> "新标题"
python3 siyuan doc move <文档ID> --to <父文档ID>
python3 siyuan doc remove <文档ID>

# 🛡️ 安全删除原则（重要）：
# 如果需要删除文档并重新创建，请遵循以下步骤避免数据丢失：
# 步骤 1: 重命名原始文档作为备份
python3 siyuan doc rename <文档ID> "旧文档标题（备份）"
# 步骤 2: 创建新文档
python3 siyuan doc create "笔记本" "/" --title "新标题" --content "新内容"
# 步骤 3: 确认新文档正常后，再删除旧文档
python3 siyuan doc remove <旧文档ID>
```

## 块操作 (blk)

```bash
python3 siyuan blk show <块ID>            # 查看块
python3 siyuan blk info <块ID>            # 块元信息

# ⚠️ 重要警告：blk update 不能用于文档块（type='d'）！
# 文档块是容器，直接更新会破坏文档结构，导致内容丢失
# 仅适用于普通内容块（type='c', 'h', 'l', 'i', 'b' 等）
python3 siyuan blk update <块ID> --content "新内容"

python3 siyuan blk append <父块ID> --content "子块内容"   # 在父块末尾添加子块
python3 siyuan blk prepend <父块ID> --content "首个子块"  # 在父块开头添加子块
python3 siyuan blk move <块ID> --to <父块ID> --after <前一块ID>
python3 siyuan blk delete <块ID>

# 块属性
python3 siyuan blk attr <块ID> get
python3 siyuan blk attr <块ID> get --key "自定义名称"
python3 siyuan blk attr <块ID> set --key "自定义名称" --value "值"
python3 siyuan blk attr <块ID> unset --key "自定义名称"
```

## 查询 (query)

```bash
python3 siyuan query search "关键词"                          # 全局搜索
python3 siyuan query search "关键词" --notebook "笔记本"      # 在笔记本内搜索
python3 siyuan query search "关键词" --type d                # 仅搜索文档
python3 siyuan query search "关键词" --limit 20

# SQL 查询（blocks 表）
python3 siyuan query sql "SELECT * FROM blocks WHERE type='d' LIMIT 10"

# 属性搜索
python3 siyuan query attr "自定义属性" "值"
python3 siyuan query attr "状态" "待办" --notebook "笔记本"

# 最近更新
python3 siyuan query recent --limit 50
```

## 导出 (export)

```bash
python3 siyuan export md <文档ID>                            # 导出 Markdown
python3 siyuan export md <文档ID> --output ./文档.md
python3 siyuan export zip "/路径1" "/路径2" --name archive.zip
```

## 资源文件 (asset)

```bash
# 上传文件（所有文件必须上传到 /assets 目录下）
python3 siyuan asset upload /path/to/file.png                       # 上传到 /assets/ 目录
python3 siyuan asset upload /path/to/file.pdf --to "/assets/docs/"  # 上传到 /assets/docs/ 子目录

# 上传成功后会显示思源文档引用路径，可在文档中直接使用
# 例如：![图片描述](assets/docs/file.pdf)

# 下载文件（只允许下载 /assets 目录下的文件）
python3 siyuan asset download image.png                            # 下载到当前目录的 assets/
python3 siyuan asset download /assets/photo.jpg                    # 下载 assets 中的文件
python3 siyuan asset download /assets/docs/file.pdf -o ~/Downloads/  # 指定输出目录
```

**路径限制**：
- 上传和下载都只允许操作 `/assets` 目录（对应思源服务器的 `workspace/data/assets` 目录）
- 所有附件和图片必须存放在此目录下，便于统一管理

**下载路径格式**：
- `image.png` - 相对路径，自动转换为 `/data/assets/image.png`
- `/assets/photo.jpg` - assets 路径，自动转换为 `/data/assets/photo.jpg`

**安全限制**：
- 不允许在 skill 安装目录中下载文件（避免污染项目代码）
- 如需在 skill 目录下载，必须使用 `-o/--output` 参数指定绝对路径

## 全局列表 (list)

```bash
python3 siyuan list                              # 列出所有笔记本及文档数量
python3 siyuan list "笔记本"                      # 列出笔记本文档
python3 siyuan list "笔记本" --tree               # 树状视图
python3 siyuan list --filter "关键词"             # 过滤结果
python3 siyuan list --format json                # JSON 输出
```

## 常用输出格式

| 格式 | 用途 |
|------|------|
| text | 默认表格/文本输出 |
| json | 机器可读的 JSON 输出 |
| --tree | 分层树状结构 |

## 错误处理

所有命令失败时会显示错误信息并以代码 1 退出。常见错误：
- `API Token is required` - 设置 `SIYUAN_TOKEN` 环境变量
- `Failed to call API` - 检查思源是否正在运行，`SIYUAN_ENDPOINT` 是否正确
- `Authentication failed` - API token 无效

## 常见错误与陷阱

### 1. 使用文档 ID 作为 `doc create` 的路径参数

**错误操作**：
```bash
python3 siyuan doc create "GitHub工程" "20260127151833-5z7coxw" --title "文档标题"
# 结果（旧版本）：创建了一个标题为 "20260127151833-5z7coxw" 的空文档
# 结果（新版本）：✗ 错误: 父文档路径不存在，并显示详细提示信息
```

**问题**：
- `doc create` 的第2个参数是**父文档路径**（字符串路径），不是文档 ID
- 路径格式：`/文档名` 或 `/父文档/子文档`
- ID 格式：`20260127151833-5z7coxw`（时间戳-随机字符）

**🛡️ 自动防护（新版本）**：
- 脚本会自动验证父路径是否存在
- 如果路径不存在，会显示详细的错误提示和正确用法
- 防止误用 ID 作为路径参数

**正确做法**：

**方法 1（已知路径）**：使用父文档的完整路径
```bash
# 查询父文档路径（如果不知道）
python3 siyuan query sql "SELECT id, hpath FROM blocks WHERE id = '20260127151833-5z7coxw'"
# 使用路径创建子文档
python3 siyuan doc create "GitHub工程" "/闲鱼购物助手 (Goofish Shopping Assistant)" --title "文档标题"
```

**方法 2（已知 ID，推荐）**：先创建再移动
```bash
# 步骤 1: 在根目录创建文档
python3 siyuan doc create "GitHub工程" "/" --title "文档标题" --content "内容..."
# 输出：✓ 已创建文档
#       ID: 20260127152720-abc123
#       路径: /文档标题

# 步骤 2: 移动到父文档下（使用父文档 ID）
python3 siyuan doc move 20260127152720-abc123 --to 20260127151833-5z7coxw
# 输出：✓ 已移动文档
```

**关键区别**：
| 命令 | 参数类型 | 示例 |
|------|---------|------|
| `doc create` | 路径（字符串） | `"/父文档/子文档"` |
| `doc move` | ID（字符串标识符） | `20260127151833-5z7coxw` |
| `doc rename` | ID | `20260127151833-5z7coxw` |
| `doc remove` | ID | `20260127151833-5z7coxw` |

### 2. 使用 `blk update` 更新文档块导致内容丢失

**错误操作**：
```bash
python3 siyuan blk update <文档ID> --content "新内容"
```

**问题**：
- 文档块（type='d'）是容器，包含多个子块
- 直接更新会破坏文档结构，导致内容全部丢失

**正确做法**：
- 🛡️ **安全原则**：如果要更新文档内容，请遵循"先备份再删除"原则：
  ```bash
  # 步骤 1: 重命名旧文档作为备份
  python3 siyuan doc rename <文档ID> "旧标题（备份）"

  # 步骤 2: 创建新文档
  python3 siyuan doc create "笔记本" "/" --title "标题" --content "新内容"

  # 步骤 3: 确认新文档正常后，删除旧文档
  python3 siyuan doc remove <旧文档ID>
  ```
- `blk update` 仅适用于普通块（type='c', 'h', 'l', 'i', 'b' 等）

### 3. 使用 `doc create` 创建了重复子目录

**错误操作**：
```bash
python3 siyuan doc create "GitHub工程" "/UART 通信说明" --title "UART 通信说明"
# 结果：创建了 /UART 通信说明/UART 通信说明（重复）
```

**问题**：
- 第2个参数是"父文档路径"，不是文档路径
- 结合 `--title` 参数，易产生重复目录

**正确做法**：
- 要在根目录创建文档：
  ```bash
  python3 siyuan doc create "GitHub工程" "/" --title "UART 通信说明"
  ```
- 要在某个父文档下创建子文档：
  ```bash
  python3 siyuan doc create "GitHub工程" "/父文档" --title "子文档"
  ```

## 资源

### scripts/
完整的思源 CLI 实现：

- `siyuan` - 主 CLI 入口
- `core/` - API 客户端、配置、模型、异常处理、路由
- `modules/` - 笔记本、文档、块、查询、资源、导出的业务逻辑
- `utils/` - 格式化、树结构、验证工具

从 `scripts/` 目录执行。脚本自包含，无需加载到上下文即可运行。

### references/
参考文档：

- [mermaid-usage.md](references/mermaid-usage.md) - 如果需要在思源笔记中绘制 mermaid 图表，请参考此文档获取详细的语法说明和示例
- [wavedrom-usage.md](references/wavedrom-usage.md) - **用于绘制时序图/波形图（Timing Diagram）**

**重要提示** - 图表库选择指南：
- **Mermaid**：流程图、时序图、类图、状态图、甘特图、思维导图等
- **WaveDrom**：时序图/波形图（数字电路信号、时序分析、总线波形等）

### 重要提示

#### Mermaid 使用规范

在适当场景下使用 mermaid 图表可以显著提升文档的可读性和信息传达效率：
- **流程逻辑**：用 flowchart 展示业务流程、算法步骤、操作流程
- **状态转换**：用 state diagram 表达系统状态变化、生命周期
- **时序关系**：用 sequence diagram 描述多组件交互、API 调用时序
- **系统架构**：用 mindmap 或 flowchart 展示系统结构、模块关系
- **数据分析**：用 pie chart 呈现数据比例、统计信息

**注意事项**：
1. 参考 [references/mermaid-usage.md](references/mermaid-usage.md) 了解 mermaid 语法
2. 在思源笔记中使用 `~~~mermaid` 代码块格式（不要使用 ` ```mermaid`）
3. 确保内容中使用正确的 mermaid 语法，以确保图表能正确渲染
4. 保持图表简洁，避免过度复杂影响理解

#### WaveDrom 使用规范
**仅**在需要绘制时序图/波形图时使用 WaveDrom：
1. 参考 [references/wavedrom-usage.md](references/wavedrom-usage.md) 了解 WaveDrom JSON 语法
2. 在思源笔记中使用 `~~~wavedrom` 代码块格式，内容为 JSON 格式的波形描述