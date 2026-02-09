# NBL PPT Builder Skill

## 概述

这是一个专门用于构建 NBL 企业 PPT 的 Claude Skill，集成了基于 Tailwind CSS 的 HTML 模板库和完整的使用说明文档。

**核心特性：**
- 5 步构建流程，确保质量符合要求
- 每个内容页由独立子代理完成，保证专业度
- 强调模板多样性，避免重复单调
- 支持用户确认和迭代调整

## 目录结构

```
nbl-ppt--builder/
├── SKILL.md                          # Skill 核心定义文件
├── README.md                         # 本说明文件
├── scripts/                          # 辅助脚本目录
│   └── merge_ppt_pages.py            # HTML 页面合并脚本
├── templates/                        # PPT 模板目录
│   ├── template_home.html            # 封面页模板
│   ├── template_toc.html             # 目录页模板
│   ├── template_end.html             # 结束页模板
│   ├── template_content_single.html  # 单栏正文模板
│   ├── template_content_two_column.html      # 左右分栏模板
│   ├── template_content_three_cards.html     # 三列卡片模板
│   ├── template_content_list.html            # 列表模板
│   ├── template_content_flowchart_4steps.html   # 4步骤流程图模板
│   ├── template_content_flowchart_5steps.html   # 5步骤流程图模板
│   ├── template_content_chart.html            # 数据图表模板
│   ├── template_content_image_1.html          # 单图模板
│   ├── template_content_image_2.html          # 双图模板
│   ├── template_content_image_3.html          # 三图模板
│   ├── template_content_image_4.html          # 四图模板
│   └── images/                       # 模板图片资源
│       ├── bg_home.jpeg             # 首页背景
│       ├── bg_end.jpeg              # 结束页背景
│       ├── decoration.png           # 装饰元素
│       └── logo.png                 # Logo
└── reference/                       # 参考文档目录
    └── 模板说明.md                   # 完整的模板使用说明文档（529行），包含每个模板的详细说明、选择原则、占位符使用指南等
```

## 文件说明

### SKILL.md
Skill 的核心定义文件，包含：
- 基本信息（名称、描述、触发关键词）
- **5 步 PPT 构建流程**（含信息收集、材料分析、内容确认、子代理生成、模板检查）
- 模板选择指南
- 占位符替换说明

### templates/*.html
基于 Tailwind CSS 的 HTML PPT 模板文件（共 14 个模板）：

**基础页面模板：**
- `template_home.html` - 封面页（标题、汇报人、Logo）
- `template_toc.html` - 目录页（4个目录项，支持高亮）
- `template_end.html` - 结束页（感谢观看）

**内容模板（11 个）：**
- `template_content_single.html` - 大段文字 + 要点总结
- `template_content_two_column.html` - 左右分栏对比展示
- `template_content_three_cards.html` - 三列卡片展示
- `template_content_list.html` - 双列列表展示
- `template_content_flowchart_4steps.html` - 4步骤流程图
- `template_content_flowchart_5steps.html` - 5步骤流程图
- `template_content_chart.html` - 数据图表（ECharts）
- `template_content_image_1.html` - 单图展示
- `template_content_image_2.html` - 双图对比
- `template_content_image_3.html` - 三图展示
- `template_content_image_4.html` - 四图展示（2x2网格）

### reference/模板说明.md
完整的模板使用说明文档（529行），包含：
- 模板概述和特点
- 每个模板的详细说明（用途、内容、使用场景、需要修改的内容）
- 模板选择原则（根据内容类型、信息密度、视觉重点、页面位置）
- 占位符使用指南（文本、图片、图表数据、颜色）
- 模板使用流程
- 注意事项

### templates/images/
模板使用的图片资源：
- `bg_home.jpeg` - 首页背景图
- `bg_end.jpeg` - 结束页背景图
- `decoration.png` - 装饰元素
- `logo.png` - 企业 Logo

## 6 步构建流程

### 步骤 0：准备工作
创建工作目录并准备图片资源：
- 根据主题创建工作目录：`ppt_{主题}/`（简洁明确，无需包含日期）
- 复制 `templates/images/` 中的所有图片到工作目录的 `images/` 文件夹
- 确保 HTML 模板中的相对图片路径可以正确引用

### 步骤 1：信息收集
向用户确认以下必填信息：
- **汇报人姓名**：显示在封面页
- **PPT 主题/标题**：封面页和目录页使用
- **汇报目的**：内部汇报、对外介绍、培训展示等
- **目标受众**：内部团队、外部客户、领导层等
- **提供材料**：文字、数据、图片等资料

### 步骤 2：材料分析与页面规划
分析用户提供的材料，整理成多个章节，每个章节包含多个内容页。为每个内容页规划内容，标记可用的文字、数据、图片等资料，但**无需指定具体模板**（模板选择由子代理完成）。

页数控制在 **8-20 页**之间，根据内容丰富程度决定：
- 简介/概览：8-10 页
- 标准汇报：12-15 页
- 详细分析：16-20 页

### 步骤 3：内容确认与迭代
向用户展示章节规划大纲（包含每页内容的简要说明），确认是否符合要求。如果用户要求调整，根据反馈重新规划，直到满意为止。

### 步骤 4：子代理内容生成
**每个内容页**由一个独立的子代理负责：
- 读取 `reference/模板说明.md` 了解所有模板的特点和适用场景
- 分析该内容页的具体内容类型、信息密度、视觉重点和可用资源
- 根据这些因素**自主选择最合适的模板**
- 生成完整的 HTML 内容，替换所有占位符
- 将生成的 HTML 保存为独立文件，文件名使用页码标记（如 `03_背景介绍.html`）

子代理在选择模板时必须参考模板说明，确保内容与模板匹配，并考虑整体多样性。

### 步骤 5：最终检查与整合
在所有子代理生成完成内容页后，进行最终检查：
- 确保至少使用了 5 种不同类型的模板
- 检查是否避免了连续 2 页使用相同模板
- 验证是否合理利用了 chart、image、flowchart 等丰富模板

如果发现模板多样性不足，重新调用子代理重新生成相关页面。最后一使用合并脚本将所有页面整合成完整的 HTML PPT 文件。

**合并命令：**
```bash
python scripts/merge_ppt_pages.py -d ppt_主题/
```

详细流程说明请参见 `SKILL.md` 文件。

## 合并脚本说明

`scripts/merge_ppt_pages.py` 是用于合并多个 HTML 页面的 Python 脚本。

### 功能特性
- 自动查找按页码命名的 HTML 文件（01_*.html, 02_*.html 等）
- 按页码顺序排序并合并
- 智能去重：避免重复加载相同的样式表和脚本
- 生成完整的 HTML 文件，可在浏览器中直接查看

### 使用方法

**基本用法：**
```bash
python scripts/merge_ppt_pages.py -d ppt_季度总结_20240131/
```

**指定输出文件名：**
```bash
python scripts/merge_ppt_pages.py -d ppt_产品介绍 -o my_presentation.html
```

**显示详细信息：**
```bash
python scripts/merge_ppt_pages.py -d ppt_季度总结 -v
```

### 参数说明
- `-d, --dir`：必填，工作目录路径（包含 HTML 文件的目录）
- `-o, --output`：可选，输出文件名（默认: merged_presentation.html）
- `-v, --verbose`：可选，显示详细处理信息

### 输入文件要求
HTML 文件必须按以下格式命名：
- `01_home.html` - 封面页
- `02_toc.html` - 目录页
- `03_背景介绍.html` - 内容页
- ...
- `16_end.html` - 结束页

页码必须是 2 或 3 位数字，不足前面补 0。

### 输出文件
脚本会在工作目录中生成 `merged_presentation.html`（或指定的文件名），包含所有页面的合并内容。

## 使用方法

### 触发方式
在对话中使用以下任一关键词即可触发本 Skill：
- "制作 PPT"
- "创建 PPT"
- "演示文稿"
- "幻灯片"
- "PPT 模板"
- "企业介绍"
- "NBL PPT"

### 使用示例

**示例 1：创建完整演示文稿**
```
用户：帮我制作一个季度业务汇报的 PPT，相关材料在 data.txt 中
AI：[步骤 1] 请告诉我汇报人姓名、PPT 标题等信息...
用户：汇报人是张三，标题是2024 Q1业务总结...
AI：[步骤 2] 我已经分析了您的材料，规划了3个章节，共12个内容页：
第一章：背景概述（2页）
- 页1：项目背景介绍
- 页2：核心问题分析

第二章：核心内容（6页）
- 页3：关键要点一
- 页4：关键要点二
...
用户：看起来不错，可以开始生成了
AI：[步骤 4-5] 正在为每个内容页调用子代理，子代理会自主选择最合适的模板并生成内容。生成完成后检查模板多样性...
[最终输出: 完整的 HTML PPT 文件，使用了 7 种不同模板]
```

**示例 2：创建产品介绍 PPT**
```
用户：需要制作一个新产品介绍PPT给客户看，有产品图和3张使用场景图
AI：[步骤 1] 请确认汇报信息...
用户：[提供信息]
AI：[步骤 2] 建议规划为9页：封面、目录、产品介绍、特性展示、使用场景（3页）、技术参数、总结、结束页
用户：我想对比一下旧产品，能加一页对比吗？
AI：好的，调整为10页：封面、目录、产品介绍、新旧对比、特性展示、使用场景（3页）、技术参数、总结、结束页
用户：可以
AI：[步骤 4-5] 开始为各页面调用子代理生成内容。子代理会根据每页的内容和可用资源（如对比页面会用 two_column，使用场景会用 image_3）选择最合适的模板。
[最终输出: 完整的 HTML PPT 文件]
```

## 模板特性

1. **统一配色方案**：蓝色系（#0B3BD3、#1D4FED、#202665）
2. **现代化设计**：基于 Tailwind CSS，卡片式布局
3. **灵活占位符**：使用 `{{变量名}}` 格式易于替换
4. **响应式设计**：适配不同屏幕尺寸
5. **图表支持**：内置 ECharts 数据可视化

## 占位符说明

- **文本占位符**：`{{标题}}`、`{{汇报人姓名}}` 等，子代理会自动替换为实际内容
- **图片占位符**：`{{图片路径}}`，子代理会替换为 `<img>` 标签
- **图表数据**：子代理会修改 script 标签中的 data 数组
- **页码**：子代理会自动设置正确的页码

## 模板多样性要求

为确保 PPT 的视觉效果和专业性，**最终检查阶段**必须确保：
- 至少使用 5 种不同类型的模板
- 避免连续 2 页使用相同模板
- 合理利用 chart、image、flowchart 等丰富模板
- 模板类型分布合理（文字、图表、图片等）

**注意：** 模板选择完全由子代理在生成时完成，SKILL 只负责在所有页面生成后进行检查和必要的调整。

## 技术说明

- 依赖 Claude Code 的 Skill 加载机制
- 使用子代理（sub-agent）为每个内容页生成具体内容
- 输出 HTML 格式的 PPT 可直接在浏览器中打开
- 支持将 HTML 导出为 PDF 或截图为图片