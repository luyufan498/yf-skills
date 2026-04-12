---
name: searxng-search
description: Enhanced web and package repository search using SearXNG instance
---

# SearXNG 搜索

SearXNG 是一个尊重隐私的元搜索引擎，它聚合来自多个搜索引擎和代码仓库的结果，返回干净的 JSON 输出。

## ⚠️ 重要：请优先使用封装脚本 (bash脚本)

**本技能提供了封装好的 `searx-bash` 脚本，专门为 SearXNG 搜索优化。**

📂 注意：脚本位置是基于当前说明文档的相对路径，执行前务必切换(cd)正确的路径

✅ **推荐**：使用 `scripts/searx-bash` 脚本
- 自动处理 URL 编码
- 内置错误处理和验证
- 提供格式化输出
- 支持丰富的参数选项
- **智能引擎选择**：自动从 360search、baidu、yandex、sogou、qwant 等中文友好引擎中选择
- **自动重试机制**：遇到 CAPTCHA/Suspended 错误自动切换引擎重试
- **清晰的错误报告**：显示失败引擎和具体解决方案建议

❌ **不推荐**：直接使用 `curl`
- 需要手动处理 URL 编码
- 错误信息不友好
- 结果格式需要手动处理
- 容易出现编码和连接问题

**只有在以下情况才考虑直接使用 curl：**
- `searx-bash` 脚本无法满足的特殊需求
- 需要访问非常规的 API 端点
- 完全了解 SearXNG API 的细节

> **对于复杂的搜索任务**（如多源包对比、学术研究、代码仓库分析），或对简单搜索结果不满意时，建议创建 Explore 类型的子代理并参考 [agent-usage.md](references/agent-usage.md) 获取完整指南。

## 快速开始

**1. 检查服务器地址（必需）：**

首先检查环境变量是否已配置：
```bash
echo $SEARXNG_URL
```

如果已输出服务器地址，则可直接使用。如果未配置，可通过以下方式设置：

**方式一：临时设置（当前会话）**
```bash
export SEARXNG_URL=http://your-server:port
```

**方式二：使用 --server 标志配合每条命令**
```bash
scripts/searx-bash "<query>" --server http://your-server:port
```

**2. 使用搜索助手：**
```bash
# 使用环境变量
export SEARXNG_URL=http://192.168.100.2:38080
scripts/searx-bash tokio --category cargo

# 使用 --server 标志
scripts/searx-bash docker --server http://192.168.100.2:38080 --category packages
```

**3. （不推荐）直接使用 curl：**
> ⚠️ **警告**：建议优先使用 `searx-bash` 脚本。直接使用 curl 需要手动处理 URL 编码、错误处理和结果解析，容易出现问题。

**仅在以下情况使用 curl：**
```bash
# 使用环境变量
export SEARXNG_URL=http://192.168.100.2:38080
curl -s "$SEARXNG_URL/search?q=tokio&format=json&categories=cargo" | jq \'.results[0:3]\'

# 或直接指定服务器
curl -s "http://your-server:port/search?q=express&format=json&categories=packages" | jq \'.results[0:3]\'
```

## 快速参考

| 任务 | 命令 | 说明 |
|------|---------|----------|
| 通用网络搜索 | `searx-bash "<query>"` | 自动选择引擎 |
| 中文搜索 | `searx-bash "中文查询"` | 优先中文友好引擎 |
| Cargo 包搜索 | `searx-bash "<crate>" --category cargo` | 搜索 crates.io |
| 多仓库包搜索 | `searx-bash "<pkg>" --category packages` | 搜索多个包管理器 |
| 代码仓库搜索 | `searx-bash "<query>" --category repos` | GitHub/GitLab 等 |
| IT 资源搜索 | `searx-bash "<query>" --category it` | 技术文档和资源 |
| 指定引擎 | `searx-bash "<query>" --engines 360search,baidu` | 使用指定引擎 |
| 增加重试次数 | `searx-bash "<query>" --retries 5` | 应对引擎限制 |
| 限制结果数 | `searx-bash "<query>" --limit N` | 返回前 N 个结果 |
| 按时间过滤 | `searx-bash "<query>" --time-range day/week/month/year` | 按时间范围过滤 |
| JSON 输出 | `searx-bash "<query>" --json` | 获取原始 JSON |

## 搜索助手用法

```bash
searx-bash <query> [OPTIONS]

选项:
  --server, -s       SearXNG 服务器 URL（默认：$SEARXNG_URL 或 http://localhost:8888）
  --category, -c     搜索类别（默认：general）
  --limit, -l        最大结果数（默认：10）
  --engines, -e      使用特定引擎（逗号分隔：--engines 360search,baidu）
  --time-range, -t   时间过滤：day/week/month/year
  --retries, -r      最大重试次数，自动切换引擎（默认：2）
  --json             输出原始 JSON
  --help, -h         显示帮助信息

环境变量:
  SEARXNG_URL        默认服务器 URL

常用类别:
  general, cargo, packages, it, repos, "q&a", wikimedia, define, science, news
```

**基本示例：**

📂 执行路径：请在执行命令前切换(cd)至当前说明文档目录

```bash
# 本地服务器（默认）
searx-bash tokio --category cargo

# 通过标志使用远程服务器
searx-bash \'machine learning\' --server http://192.168.100.2:38080 --category it

# 通过环境变量使用远程服务器
export SEARXNG_URL=http://192.168.100.2:38080
searx-bash docker --category packages --limit 5

# 按时间过滤（最近一周）
searx-bash "rust news" --time-range week

# 按时间过滤（最近一天）
searx-bash "ai" --time-range day --limit 3

# 智能引擎选择（自动随机选择并重试）
searx-bash "人工智能最新发展" --limit 5

# 指定特定引擎
searx-bash "python tutorial" --engines 360search,baidu --limit 5

# 增加重试次数应对限制
searx-bash "complex query" --retries 5
```

## 引擎选择

### 自动选择机制

脚本内置智能引擎选择，不指定 `--engines` 时自动从以下组合中随机选择：
- `360search,baidu` - 中文优化（推荐）
- `360search,yandex` - 中文+国际
- `baidu,sogou` - 纯中文
- `yandex,qwant` - 国际引擎

遇到引擎限制（CAPTCHA/Suspended）时自动重试其他组合。

### 常用引擎组合

| 场景 | 推荐引擎 |
|------|---------|
| 中文搜索 | `360search,baidu` |
| 国际搜索 | `360search,yandex` |
| 技术文档 | `360search,wikipedia` |
| 单引擎快速 | `360search` |

### 快速示例

```bash
# 自动选择（推荐）
searx-bash "query"

# 指定引擎
searx-bash "中文查询" --engines "360search,baidu"

# 增加重试
searx-bash "query" --retries 5

# 无结果时简化查询词再试
```

## 可用类别

| 类别 | 说明 |
|------|------|
| **general** | 通用网络搜索（默认） |
| **cargo** | Rust crates |
| **packages** | 多语言包仓库（npm、docker hub 等） |
| **it** | IT/技术资源（GitHub、Docker Hub 等） |
| **repos** | 代码仓库 |
| **q&a** | 问答站点（Stack Overflow 等） |
| **wikimedia** | 维基百科项目 |
| **define** | 词典定义 |
| **scientific publications** | 学术论文 |
| **news** | 新闻资讯 |
| **science** | 科学资源 |

查看全部类别：`curl -s "$SEARXNG_URL/config" | jq '.categories'`

## 高级用法

### JSON 输出

```bash
# 获取原始 JSON
searx-bash "query" --json

# 使用 jq 过滤
searx-bash "query" --json | jq '.results[0:5] | .[] | {title, url}'
```

### 访问搜索结果页面

获取搜索结果后，可用以下工具访问页面：
- **agent-browser** - 浏览器自动化（推荐）
- **web-fetch** - 获取并分析内容
- **curl** - 快速获取原始内容

### 深度研究技巧

**迭代理索：**
```bash
# 1. 初步搜索
searx-bash "rust async framework" --category cargo

# 2. 基于关键词深入
searx-bash "tokio vs async-std" --category it
searx-bash "tokio best practices" --category it
```

**跨类别验证：**
```bash
searx-bash "tokio" --category cargo   # 包信息
searx-bash "tokio" --category repos    # 代码仓库
searx-bash "tokio tutorial" --category it     # 教程
```

> 复杂搜索任务建议创建 Explore 子代理，参考 [agent-usage.md](references/agent-usage.md)

