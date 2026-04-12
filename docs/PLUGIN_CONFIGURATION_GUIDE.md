# Claude Code 插件和 Marketplace 配置完整指南

## 目录

- [概述](#概述)
- [Marketplace 配置](#marketplace-配置)
- [Plugin 配置](#plugin-配置)
- [SKILL 配置](#skill-配置)
- [目录结构规范](#目录结构规范)
- [完整示例](#完整示例)
- [常见问题](#常见问题)

---

## 概述

Claude Code 的插件系统允许你创建可复用的技能包，通过 Marketplace 进行分发和安装。一个插件可以包含：

- **Skills**: Agent 触发的技能文件
- **Commands**: Slash 命令 (通过 `/` 触发)
- **Agents**: 特定任务的专用代理
- **Hooks**: 事件处理器
- **MCP Servers**: Model Context Protocol 服务器

**关键概念**: 一个插件可以包含**多个 SKILLS**，它们可以共享模板、脚本和其他资源。

---

## Marketplace 配置

### 文件位置

```
your-marketplace/
└── .claude-plugin/
    └── marketplace.json    ✅ Marketplace 索引文件
```

### 配置格式

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "marketplace-name",
  "version": "1.0.0",
  "description": "Marketplace 描述",
  "owner": {
    "name": "Your Name",
    "email": "your.email@example.com"
  },
  "plugins": [
    {
      "name": "plugin-name",
      "source": "./path/to/plugin",
      "description": "插件描述",
      "category": "productivity",
      "tags": ["tag1", "tag2"],
      "keywords": ["keyword1", "keyword2"]
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | 是 | Marketplace 唯一标识符 (kebab-case) |
| `version` | string | 否 | 语义化版本号 |
| `description` | string | 否 | Marketplace 整体描述 |
| `owner` | object | 否 | 维护者信息 `{name, email}` |
| `plugins` | array | 是 | 插件列表 |

### 插件条目字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 插件唯一标识符 (必须与 plugin.json 中的 name 一致) |
| `source` | string | 是 | 插件目录相对路径 (如 `./plugins/my-plugin`) |
| `description` | string | 否 | 插件功能描述 |
| `category` | string | 否 | 分类: `productivity`, `development`, `finance` 等 |
| `tags` | array | 否 | 标签数组 |
| `keywords` | array | 否 | 搜索关键词数组 |

---

## Plugin 配置

### 文件位置

```
your-plugin/
└── .claude-plugin/
    └── plugin.json    ✅ 插件元数据文件
```

### 配置格式

#### 最小配置

```json
{
  "name": "my-plugin"
}
```

#### 标准配置

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "插件功能描述 (50-200 字符)",
  "author": {
    "name": "Your Name",
    "email": "your.email@example.com"
  },
  "homepage": "https://github.com/your-repo/my-plugin",
  "repository": "https://github.com/your-repo/my-plugin.git",
  "license": "MIT",
  "keywords": ["keyword1", "keyword2"],
  "skills": "./skills",
  "commands": "./commands",
  "agents": "./agents"
}
```

#### 完整配置

```json
{
  "name": "enterprise-devops",
  "version": "2.3.1",
  "description": "企业级 DevOps 自动化工具包",
  "author": {
    "name": "DevOps Team",
    "email": "devops@company.com",
    "url": "https://company.com/devops"
  },
  "homepage": "https://docs.company.com/plugins/devops",
  "repository": {
    "type": "git",
    "url": "https://github.com/company/devops-plugin.git"
  },
  "license": "Apache-2.0",
  "keywords": ["devops", "ci-cd", "automation"],
  "category": "productivity",
  "skills": "./skills",
  "commands": "./commands",
  "agents": "./agents",
  "hooks": "./hooks/hooks.json",
  "mcpServers": "./.mcp.json"
}
```

### 字段说明

#### 核心字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | **是** | 插件唯一标识符 (kebab-case 格式) |
| `version` | string | 否 | 语义化版本 (MAJOR.MINOR.PATCH) |
| `description` | string | 否 | 功能描述 (建议 50-200 字符) |

#### 元数据字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `author` | object/string | 否 | `{name, email, url}` 或字符串 |
| `homepage` | string | 否 | 文档 URL |
| `repository` | string/object | 否 | 源代码仓库 |
| `license` | string | 否 | SPDX 许可证标识符 |
| `keywords` | array | 否 | 搜索关键词 |
| `category` | string | 否 | 插件分类 |

#### 组件路径字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `skills` | string/array | 否 | Skills 目录路径或数组 |
| `commands` | string/array | 否 | Commands 目录路径或数组 |
| `agents` | string/array | 否 | Agents 目录路径或数组 |
| `hooks` | string/object | 否 | Hooks 配置文件路径或内联配置 |
| `mcpServers` | string/object | 否 | MCP 服务器配置路径或内联配置 |

### Skills 字段配置方式

#### 方式 1: 自动扫描 (推荐)

```json
{
  "skills": "./skills"
}
```

系统会自动扫描 `skills/` 目录下的所有子目录，每个包含 `SKILL.md` 的子目录都会被识别为一个 SKILL。

**优点**:
- 添加新 SKILL 不需要修改配置
- 支持多个 SKILLS
- 目录结构清晰

#### 方式 2: 显式指定

```json
{
  "skills": [
    "./skills/ppt-builder",
    "./skills/template-designer",
    "./skills/chart-generator"
  ]
}
```

**优点**:
- 精确控制包含哪些 SKILLS
- 可以选择性地启用/禁用特定 SKILL

---

## SKILL 配置

### 文件位置

```
your-plugin/
└── skills/
    └── skill-name/
        ├── SKILL.md         ✅ SKILL 主文件 (必需)
        ├── references/      📁 参考文档 (可选)
        ├── examples/        📁 示例文件 (可选)
        ├── scripts/         📁 执行脚本 (可选)
        └── templates/       📁 模板文件 (可选)
```

**重要**: SKILL 文件必须位于 `skills/{skill-name}/SKILL.md` 路径下，而不是直接放在插件根目录。

### SKILL.md 文件格式

```markdown
---
name: skill-name
description: 详细描述何时使用此 SKILL。当用户要求 X、Y、Z 时触发。
version: 1.0.0
license: MIT
---

# SKILL 标题

这里是给 Claude 的具体指令内容...

## 章节内容

详细的工作流程和规范...
```

### Frontmatter 字段

#### 必需字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | SKILL 唯一标识符，用于触发 |
| `description` | string | 详细描述 SKILL 的用途和使用场景 |

#### 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | string | SKILL 版本号 (语义化版本) |
| `license` | string | 许可证信息 |

#### 注意事项

❌ **不支持的字段**:
- `allowed-tools` - 不在标准 schema 中
- `event` - 仅用于 hookify rules
- `pattern` - 仅用于 hookify rules
- `action` - 仅用于 hookify rules

✅ **正确的做法**:
- 在 SKILL body 中通过文字说明需要的工具
- 让 Claude 根据任务需求自主决定使用哪些工具

### SKILL 命名规范

1. **使用 kebab-case**: `ppt-builder`, `stock-analysis`
2. **描述性强**: 名称应清晰表达功能
3. **避免通用词**: 不要使用 `helper`, `util` 等泛化名称
4. **与目录名一致**: `skills/{skill-name}/SKILL.md` 中的 skill-name 应与 frontmatter 中的 name 一致

### Description 编写指南

Description 是触发 SKILL 的关键，应该：

1. **说明功能**: 清晰描述 SKILL 做什么
2. **触发场景**: 列出用户可能的提问方式
3. **关键词**: 包含相关的搜索关键词

**示例**:

```yaml
---
name: ppt-builder
description: 专门用于构建企业 PPT 的技能。当用户要求创建 PPT、演示文稿、幻灯片、制作演示文档时触发。支持企业模板、自动布局、图表生成等功能。
---
```

---

## 目录结构规范

### 单 SKILL 插件

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json          ✅ 插件元数据
├── skills/
│   └── my-skill/
│       ├── SKILL.md         ✅ SKILL 主文件
│       ├── references/      📁 参考文档
│       ├── examples/        📁 示例
│       └── scripts/         📁 脚本
├── templates/                📁 共享模板
└── README.md                 📄 插件文档
```

### 多 SKILLS 插件

```
devops-toolkit/
├── .claude-plugin/
│   └── plugin.json          ✅ 插件元数据
├── skills/
│   ├── ci-pipeline/
│   │   └── SKILL.md         ✅ SKILL 1: CI 流水线
│   ├── deploy-automation/
│   │   └── SKILL.md         ✅ SKILL 2: 部署自动化
│   ├── monitoring/
│   │   └── SKILL.md         ✅ SKILL 3: 监控配置
│   └── incident-response/
│       └── SKILL.md         ✅ SKILL 4: 事件响应
├── scripts/                  📁 所有 SKILLS 共享的脚本
├── templates/                📁 所有 SKILLS 共享的模板
└── README.md                 📄 插件文档
```

### 插件配置

```json
{
  "name": "devops-toolkit",
  "version": "1.0.0",
  "description": "企业 DevOps 工具包，包含 CI/CD、监控、事件响应等多个技能",
  "skills": "./skills"  // 自动扫描所有 SKILLS
}
```

---

## 完整示例

### 示例 1: PPT 构建工具

#### Marketplace 配置

```json
// .claude-plugin/marketplace.json
{
  "name": "productivity-tools",
  "version": "1.0.0",
  "owner": {
    "name": "Your Name",
    "email": "your.email@example.com"
  },
  "plugins": [
    {
      "name": "nbl-ppt-builder",
      "source": "./nbl-ppt-builder",
      "description": "NBL 企业 PPT 构建工具",
      "category": "productivity",
      "tags": ["ppt", "presentation", "document"],
      "keywords": ["ppt", "presentation", "slide", "enterprise"]
    }
  ]
}
```

#### Plugin 配置

```json
// nbl-ppt-builder/.claude-plugin/plugin.json
{
  "name": "nbl-ppt-builder",
  "version": "1.0.0",
  "description": "专门用于构建 NBL 企业 PPT，包含标准模板、配色方案和内容规范",
  "author": {
    "name": "Your Name",
    "email": "your.email@example.com"
  },
  "license": "MIT",
  "keywords": ["ppt", "presentation", "slide", "document"],
  "skills": "./skills"
}
```

#### SKILL 配置

```markdown
<!-- nbl-ppt-builder/skills/ppt-builder/SKILL.md -->
---
name: ppt-builder
description: 专门用于构建 NBL 企业 PPT 的技能。当用户要求创建 PPT、演示文稿、幻灯片、制作演示文档时触发。支持企业模板、自动布局、图表生成等功能。
version: 1.0.0
license: MIT
---

你现在是 NBL 企业 PPT 构建专家。

## 核心原则

使用 `templates/` 目录中的 HTML 模板文件作为基础，严格遵循 Tailwind CSS 设计规范和蓝色系配色。

## 工作流程

1. 分析用户提供的资料
2. 规划 PPT 结构
3. 生成 HTML 页面
4. 转换为 PPTX 格式

## Scripts 环境

SKILL 使用 `scripts/` 目录中的工具脚本进行页面验证和 PPTX 生成。

...
```

#### 目录结构

```
nbl-ppt-builder/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── ppt-builder/
│       ├── SKILL.md
│       ├── references/
│       │   ├── HTML页面生成说明.md
│       │   └── PPT规划说明.md
│       └── examples/
│           └── sample-ppt.md
├── templates/
│   ├── cover.html
│   ├── content.html
│   └── ending.html
├── scripts/
│   ├── pptx/
│   │   ├── package.json
│   │   └── convert.js
│   └── validate.py
└── README.md
```

### 示例 2: 多 SKILLS 财经工具包

#### Marketplace 配置

```json
{
  "name": "finance-toolkit",
  "version": "1.0.0",
  "plugins": [
    {
      "name": "stock-analysis-suite",
      "source": "./stock-analysis-suite",
      "description": "股票分析套件，包含数据查询、技术分析、交易模拟等多个技能",
      "category": "finance"
    }
  ]
}
```

#### Plugin 配置

```json
{
  "name": "stock-analysis-suite",
  "version": "2.0.0",
  "description": "综合股票分析工具包，支持实时数据、技术分析、模拟交易",
  "skills": "./skills"
}
```

#### 目录结构

```
stock-analysis-suite/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   ├── market-data/
│   │   └── SKILL.md         ✅ 股票数据查询
│   ├── technical-analysis/
│   │   └── SKILL.md         ✅ 技术指标分析
│   ├── paper-trading/
│   │   └── SKILL.md         ✅ 模拟交易
│   └── portfolio-tracker/
│       └── SKILL.md         ✅ 投资组合跟踪
├── scripts/
│   ├── data_fetcher.py      📁 所有 SKILLS 共享
│   └── indicators.py        📁 所有 SKILLS 共享
└── README.md
```

---

## 常见问题

### 1. 为什么安装成功但 `/` 命令找不到 SKILL？

**原因**: SKILL 文件位置错误

❌ **错误做法**:
```
my-plugin/
├── SKILL.md          ❌ 直接在根目录
└── plugin.json
```

✅ **正确做法**:
```
my-plugin/
├── skills/
│   └── my-skill/
│       └── SKILL.md  ✅ 在正确的目录结构中
└── plugin.json
```

### 2. plugin.json 中的 skills 字段应该怎么配置？

**推荐方式**: 使用目录路径，让系统自动扫描

```json
{
  "skills": "./skills"  // 自动扫描 skills 目录
}
```

**不推荐方式**: 直接指定文件路径

```json
{
  "skills": ["./SKILL.md"]  // ❌ 不符合规范
}
```

### 3. 一个插件可以有多个 SKILLS 吗？

**可以！** 这正是插件系统的设计理念。

```
my-plugin/
└── skills/
    ├── skill-1/SKILL.md
    ├── skill-2/SKILL.md
    └── skill-3/SKILL.md
```

所有 SKILLS 共享插件的 `templates/`, `scripts/` 等资源。

### 4. SKILL.md 支持哪些 frontmatter 字段？

✅ **支持的字段**:
- `name` (必需)
- `description` (必需)
- `version` (可选)
- `license` (可选)

❌ **不支持的字段**:
- `allowed-tools`
- `event`
- `pattern`
- `action`

### 5. 如何调试 SKILL 配置问题？

1. **检查目录结构**: 确保 SKILL.md 在 `skills/{skill-name}/` 下
2. **验证 plugin.json**: 确保 `skills` 字段指向正确的目录
3. **检查 frontmatter**: 确保只使用支持的字段
4. **重启 Claude Code**: 修改后需要重启才能生效

### 6. SKILL 的 name 和插件目录名必须一致吗？

**建议保持一致**，但不是强制要求：

```markdown
<!-- skills/ppt-builder/SKILL.md -->
---
name: ppt-builder    ✅ 与目录名一致
---
```

```markdown
<!-- skills/ppt-builder/SKILL.md -->
---
name: presentation-builder    ⚠️ 不一致，可能造成混淆
---
```

### 7. 如何共享资源给多个 SKILLS？

将共享资源放在插件根目录：

```
my-plugin/
├── skills/
│   ├── skill-1/SKILL.md
│   └── skill-2/SKILL.md
├── templates/         ✅ 所有 SKILLS 可访问
│   └── shared.html
└── scripts/           ✅ 所有 SKILLS 可访问
    └── utils.py
```

在 SKILL.md 中引用：

```markdown
使用 `templates/shared.html` 模板...
运行 `scripts/utils.py` 脚本...
```

---

## 最佳实践

### 1. 目录结构

- ✅ 保持清晰的层级结构
- ✅ 使用有意义的目录名
- ✅ 相关 SKILLS 放在同一个插件
- ❌ 避免过深的嵌套

### 2. 命名规范

- ✅ 使用 kebab-case: `stock-analysis`, `ppt-builder`
- ✅ 描述性强的名称
- ❌ 避免泛化名称: `util`, `helper`, `common`

### 3. Description 编写

- ✅ 清晰描述功能
- ✅ 列出触发场景
- ✅ 包含关键词
- ✅ 50-200 字符为宜

### 4. 版本管理

- ✅ 使用语义化版本
- ✅ 在 CHANGELOG 中记录变更
- ✅ 保持 plugin.json 和 SKILL.md 版本一致

### 5. 文档

- ✅ 每个 SKILL 都应有 README
- ✅ 提供使用示例
- ✅ 说明依赖和环境要求
- ✅ 包含故障排除指南

---

## 参考资料

- [Claude Code Plugin Development](https://github.com/anthropics/claude-code/tree/main/plugins)
- [Plugin Structure Reference](https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/skills/plugin-structure/SKILL.md)
- [SKILL Development Guide](https://github.com/anthropics/claude-code/blob/main/plugins/plugin-dev/skills/skill-development/SKILL.md)

---

**文档版本**: 1.0.0
**最后更新**: 2026-04-12
**作者**: Claude Code Assistant