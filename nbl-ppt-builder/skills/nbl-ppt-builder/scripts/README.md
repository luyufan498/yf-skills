# NBL PPT Builder - Scripts 使用指南

## 概述

本目录包含用于构建和验证 NBL PPT 的 Python 脚本工具。

### 可用脚本

- `merge_ppt_pages.py` - 合并多个 HTML 页面为完整演示文稿
- `validate_with_playwright.py` - 使用 Playwright 验证 PPT 页面内容是否溢出

---

## 验证脚本 (validate_with_playwright.py)

### 功能

检测 PPT 页面中的主要内容（卡片、分栏等）是否溢出幻灯片底部边界。

### 安装依赖

使用 `uv` 管理依赖：

```bash
cd .claude/skills/nbl-ppt--builder/scripts
uv sync
uv run playwright install chromium
```

### 使用方法

#### 验证单个页面

```bash
cd .claude/skills/nbl-ppt--builder/scripts
uv run python validate_with_playwright.py /path/to/page.html
```

#### 验证所有页面（批量）

```bash
cd .claude/skills/nbl-ppt--builder/scripts
for file in /path/to/ppt/pages/*.html; do
    uv run python validate_with_playwright.py "$file"
done
```

### 检测逻辑

- ✅ 只检测主要内容区域（卡片、分栏等），忽略背景装饰元素
- ✅ 检测元素底部是否超出幻灯片边界（540px）
- ✅ 报告溢出量和元素详细信息

### 输出示例

```bash
检测文件: /path/to/page.html

⚠️  发现 4 个滚动条问题

  1. content_overflow: 内容溢出幻灯片底部 99.9375px

✅ 报告已保存: validation_report.json
```

### 返回值

- `0` - 无问题
- `1` - 有警告
- `2` - 有错误（严重）

---

## 合并脚本 (merge_ppt_pages.py)

### 功能特性

- ✅ 自动识别按页码命名的 HTML 文件
- ✅ 按页码顺序自动排序
- ✅ 智能去重样式表（CSS）和脚本（JavaScript）
- ✅ 生成完整的 HTML 文件，可直接在浏览器中查看
- ✅ 支持打印分页（@media print）

### 使用方法

#### 基本用法

```bash
cd .claude/skills/nbl-ppt--builder/scripts
python merge_ppt_pages.py -d /path/to/ppt/pages/
```

#### 指定输出文件名

```bash
python merge_ppt_pages.py -d /path/to/ppt/pages/ -o complete_presentation.html
```

#### 显示详细信息

```bash
python merge_ppt_pages.py -d /path/to/ppt/pages/ -v
```

## 参数说明

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `-d, --dir` | 是 | 工作目录路径（包含 HTML 文件的目录） | `ppt_季度总结_20240131` |
| `-o, --output` | 否 | 输出文件名 | `my_presentation.html` |
| `-v, --verbose` | 否 | 显示详细处理信息 | 无 |

## 输入文件要求

### 文件命名格式

HTML 文件必须按以下格式命名：

```
{页码:02d}_{描述}.html
```

或简化格式：

```
{页码:02d}.html
```

### 示例文件列表

```
ppt_季度总结_20240131/
├── 01_home.html           # 封面页
├── 02_toc.html            # 目录页
├── 03_背景介绍.html
├── 04_问题分析.html
├── 05_关键要点.html
├── 06_实施流程.html
├── 07_数据分析.html
├── 08_成果展示.html
├── 09_未来规划.html
└── 10_end.html            # 结束页
```

---

## SKILL 集成

这些脚本由 NBL PPT Builder SKILL 自动调用：

1. **页面生成后自动验证**：每生成一个 HTML 页面后，自动调用 `validate_with_playwright.py` 检测溢出
2. **批量生成后合并**：所有页面生成完成且验证通过后，调用 `merge_ppt_pages.py` 合并
3. **报告保存**：验证结果保存在 `validation_report.json`，供 SKILL 读取和分析

### 工作流程

```
1. SKILL 生成单个页面 HTML
   ↓
2. 自动调用验证脚本检测溢出
   ↓
3. 如有问题，SKILL 调整页面布局
   ↓
4. 所有页面验证通过
   ↓
5. 调用合并脚本生成完整演示文稿
```

### 命名规则

- 页码必须是 2 或 3 位数字（01, 02, ..., 10, 11, ..., 99, 100）
- 不足前面补 0（例如：09 而不是 9）
- 描述部分（如果有）可以是中文或英文
- 必须是 `.html` 扩展名

## 输出说明

### 验证报告

`validate_with_playwright.py` 生成 `validation_report.json`：

```json
{
  "file": "/path/to/page.html",
  "status": "error",
  "issues": [
    {
      "type": "A",
      "category": "content_overflow",
      "severity": "high",
      "description": "内容溢出幻灯片底部 99.9375px",
      "details": {
        "element_top": 172,
        "element_height": 467.9375,
        "element_bottom": 639.9375,
        "slide_height": 540,
        "overflow": 99.9375
      }
    }
  ]
}
```

### 合并输出

`merge_ppt_pages.py` 生成完整的 HTML 演示文稿，可直接在浏览器中查看或导出 PDF。

## 常见问题

### Q: 脚本找不到 HTML 文件

确保文件命名格式正确：`01_description.html` 或 `01.html`

### Q: 验证报告显示溢出但内容看起来正常

检测的是元素底部相对于幻灯片边界的位置。检查 `bottom-[XXpx]` 的设置是否合理。

### Q: uv 命令不存在

需要先安装 uv：`pip install uv`

## 环境要求

- Python 3.13+
- uv（用于依赖管理）
- Playwright（通过 `uv run playwright install chromium` 安装）

## 许可证

内部使用