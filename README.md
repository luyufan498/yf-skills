# YF Skills - Claude 技能集合

这是一个 Claude Code 技能（Skills）的集合项目，包含多个专业化技能，用于扩展 Claude 的能力。

## 项目概述

本项目将常用的工作流程和工具封装成独立的 Claude 技能，提供专业、高效的 AI 辅助开发体验。

## 包含的技能

| 技能名称 | 描述 | 触发关键词 |
|---------|------|-----------|
| **nbl-ppt--builder** | 专门用于构建 NBL 企业 PPT，包含标准模板、配色方案和内容规范 | 制作 PPT、创建演示文稿、PPT 模板、企业介绍 |
| **searxng-search** | 使用 SearXNG 实例增强的 Web 和软件包仓库搜索 | 搜索、查找 |
| **siyuan-notes** | 思源笔记全功能命令行工具，支持笔记本、文档和块操作 | 思源、笔记 |
| **skill-creator-dev** | 用于在 workspace 创建新技能开发环境的工具 | 创建技能、技能开发 |

## 快速开始

### 环境要求

- Claude Code 终端扩展
- 各技能的特定依赖（详见各技能目录）

### 安装技能

由于本项目中的技能需要单独打包和安装，请参考各技能目录下的 `SKILL.md` 文件了解详细安装和使用说明。

### 使用技巧

在 Claude Code 对话中，可以通过触发关键词来自动加载相应的技能：

```bash
# 示例：
"帮我制作一个季度业务汇报的 PPT"         # 触发 nbl-ppt--builder
"搜索 tokio 这个 crate"                   # 触发 searxng-search
"列出思源笔记中的所有文档"                # 触发 siyuan-notes
"帮我创建一个新的技能开发环境"            # 触发 skill-creator-dev
```

## 技能详情

### nbl-ppt--builder

专门用于构建 NBL 企业 PPT 的 Skill，包含 14 个基于 Tailwind CSS 的 HTML 模板和完整的 6 步构建流程。

**核心特性：**
- 5 步构建流程，确保质量符合要求
- 每个内容页由独立子代理完成，保证专业度
- 强调模板多样性，避免重复单调
- 支持生成 HTML、PPTX 和 PDF 格式

**目录结构：**
```
nbl-ppt--builder/
├── SKILL.md                          # 技能核心定义
├── README.md                         # 详细说明
├── scripts/                          # 辅助脚本
│   ├── merge_ppt_pages.py           # HTML 页面合并
│   ├── validate_with_playwright.py  # 页面验证
│   └── pptx/                        # PPTX 生成工具
├── templates/                        # 14 个 HTML 模板
└── reference/                       # 参考文档
    ├── HTML页面生成说明.md
    └── PPT规划说明.md
```

详细说明请参见 [nbl-ppt--builder/README.md](nbl-ppt--builder/README.md)

### searxng-search

使用 SearXNG 实例增强的 Web 和软件包仓库搜索，支持多个搜索引擎和软件包仓库的聚合搜索。

**支持类别：**
- `general` - 通用网页搜索
- `cargo` - Rust crates (crates.io)
- `packages` - 多仓库 (npm, rubygems, hex, docker hub 等)
- `it` - IT/技术资源
- `repos` - 代码仓库
- `q&a` - 问答社区
- `wikimedia` - 维基媒体项目

**快速开始：**
```bash
# 设置服务器地址
export SEARXNG_URL=http://your-server:38080

# 使用搜索辅助工具
scripts/searx-bash tokio --category cargo
```

详细说明请参见 [searxng-search/README.md](searxng-search/README.md)

### siyuan-notes

思源笔记全功能命令行工具，提供完整的笔记管理功能。

**核心功能：**
- 笔记本管理（列表、创建、删除、重命名）
- 文档操作（创建、读取、更新、移动、删除）
- 块级编辑（更新、追加、移动、删除）
- 内容搜索和 SQL 查询
- 导出为 Markdown/ZIP
- 资源文件上传和下载

**环境配置：**
```bash
export SIYUAN_ENDPOINT=http://127.0.0.1:6806
export SIYUAN_TOKEN=your-api-token
```

**快速使用：**
```bash
cd scripts
python3 siyuan nb list              # 列出所有笔记本
python3 siyuan doc show <文档ID>     # 查看文档
python3 siyuan query search "关键词" # 搜索内容
```

详细文档请参见 [siyuan-notes/SKILL.md](siyuan-notes/SKILL.md)

### skill-creator-dev

用于在 workspace 创建新技能开发环境的工具。

**核心功能：**
- 创建标准化的技能目录结构
- 生成 SKILL.md 模板
- 支持本地调试和测试
- 分离开发和安装阶段

**重要说明：**
这是开发工具，不是安装工具。仅在 workspace 创建 skill 目录结构，不会执行安装操作。安装流程需要用户主动发起请求。

**快速使用：**
```bash
scripts/init_skill.py <skill-name> --path <output-directory>
```

详细文档请参见 [skill-creator-dev/SKILL.md](skill-creator-dev/SKILL.md)

## 开发指南

### 开发新技能

1. 使用 `skill-creator-dev` 技能创建开发环境
2. 在 workspace 中调试和测试
3. 完成后主动发起安装请求

### 技能设计原则

- **简洁是关键**：只添加 Claude 尚未具备的上下文
- **适当自由度**：将特异性级别与任务的脆弱性相匹配
- **渐进式披露**：使用三级加载系统高效管理上下文

## 贡献

欢迎为各种技能提供反馈和改进建议！

## 许可证

本项目各技能的许可证请参阅各自目录下的说明文件。

## 相关链接

- [Claude Code 文档](https://claude.ai/docs/claude-code)
- 技能开发最佳实践：[skill-creator-dev](skill-creator-dev/)