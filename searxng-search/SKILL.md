---
name: searxng-search
description: Enhanced web and package repository search using SearXNG instance
version: 1.0.0
---

# SearXNG 搜索

SearXNG 是一个尊重隐私的元搜索引擎，它聚合来自多个搜索引擎和代码仓库的结果，返回干净的 JSON 输出。

> **注意**：对于复杂的搜索任务（如多源包对比、学术研究、代码仓库分析），或对简单搜索结果不满意时，建议创建 Explore 类型的子代理并参考 [agent-usage.md](references/agent-usage.md) 获取完整指南。

## 快速开始

**1. 设置服务器地址（必需）：**
```bash
export SEARXNG_URL=http://your-server:port
# 或者使用 --server 标志配合每条命令
```

**2. 使用搜索助手：**
```bash
# 使用环境变量
export SEARXNG_URL=http://192.168.100.2:38080
scripts/searx-bash tokio --category cargo

# 使用 --server 标志
scripts/searx-bash docker --server http://192.168.100.2:38080 --category packages
```

**3. 或者直接使用 curl：**
```bash
# 使用环境变量
export SEARXNG_URL=http://192.168.100.2:38080
curl -s "$SEARXNG_URL/search?q=tokio&format=json&categories=cargo" | jq '.results[0:3]'

# 或直接指定服务器
curl -s "http://your-server:port/search?q=express&format=json&categories=packages" | jq '.results[0:3]'
```

## 快速参考

| 任务 | 命令 | 类别 |
|------|---------|----------|
| 通用网络搜索 | `searx-bash "<query>"` | `general` |
| 搜索 Cargo/crates.io | `searx-bash "<crate>" --category cargo` | `cargo` |
| 搜索多仓库包 | `searx-bash "<pkg>" --category packages` | `packages` |
| 搜索代码仓库 | `searx-bash "<query>" --category repos` | `repos` |
| 搜索 IT 资源 | `searx-bash "<query>" --category it` | `it` |
| 限制结果数 | `searx-bash "<query>" --limit N` | - |

## 搜索助手用法

```bash
searx-bash <query> [OPTIONS]

选项:
  --server, -s       SearXNG 服务器 URL（默认：$SEARXNG_URL 或 http://localhost:8888）
  --category, -c     搜索类别（默认：general）
  --limit, -l        最大结果数（默认：10）
  --json             输出原始 JSON
  --help, -h         显示帮助信息

环境变量:
  SEARXNG_URL        默认服务器 URL

常用类别:
  general, cargo, packages, it, repos, science, news, "q&a", wikimedia
```

**基本示例：**
```bash
# 本地服务器（默认）
searx-bash tokio --category cargo

# 通过标志使用远程服务器
searx-bash 'machine learning' --server http://192.168.100.2:38080 --category it

# 通过环境变量使用远程服务器
export SEARXNG_URL=http://192.168.100.2:38080
searx-bash docker --category packages --limit 5
```

## 可用类别

查看所有类别：
```bash
curl -s "$SEARXNG_URL/config" | jq '.categories'
```

**常用类别：**
- **general** - 通用网络搜索（默认）
- **cargo** - Rust crates (crates.io)
- **packages** - 多语言包仓库（npm、crates.io、docker hub 等）
- **it** - IT/技术资源（GitHub、Docker Hub 等）
- **repos** - 代码仓库
- **q&a** - Q&A 站点（Stack Overflow 等）
- **wikimedia** - 维基百科项目
- **scientific publications** - 学术论文

> **更多详情**：查看 [category-guide.md](references/category-guide.md) 获取完整类别列表和详细用法。

## 高级用法

> 对于复杂的搜索任务（如多源包对比、学术研究、代码仓库分析），建议创建 Explore 类型的子代理并参考 [agent-usage.md](references/agent-usage.md) 获取完整指南。

### 使用 JSON 输出进行更灵活的处理

```bash
# 获取原始 JSON
searx-bash "query" --json

# 使用 jq 过滤结果
searx-bash "query" --json | jq '.results[0:5] | .[] | {title, url}'
```

## 服务器配置

远程访问所需配置：

```yaml
use_default_settings: true
search:
  formats:
    - html
    - json
server:
  bind_address: "0.0.0.0"
  limiter: false
  public_instance: false
```

**配置说明：**
- `bind_address: "0.0.0.0"` - 接受来自任何 IP 的连接
- `formats: [html, json]` - 启用 JSON API（必须）
- `limiter: false` - 为可信使用禁用限速
- `public_instance: false` - 禁用隐私增强功能（可选）

详细配置请参阅 [SearXNG 设置文档](https://docs.searxng.org/admin/settings/settings.html)。