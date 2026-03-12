---
name: searxng-search
description: Enhanced web and package repository search using SearXNG instance
version: 1.0.0
---

# SearXNG 搜索

SearXNG 是一个尊重隐私的元搜索引擎，它聚合来自多个搜索引擎和代码仓库的结果，返回干净的 JSON 输出。

## ⚠️ 重要：请优先使用封装脚本

**本技能提供了封装好的 `searx-bash` 脚本，专门为 SearXNG 搜索优化。**

✅ **推荐**：使用 `scripts/searx-bash` 脚本
- 自动处理 URL 编码
- 内置错误处理和验证
- 提供格式化输出
- 支持丰富的参数选项

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
searx-bash "<query>" --server http://your-server:port
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

| 任务 | 命令 | 类别 |
|------|---------|----------|
| 通用网络搜索 | `searx-bash "<query>"` | `general` |
| 搜索 Cargo/crates.io | `searx-bash "<crate>" --category cargo` | `cargo` |
| 搜索多仓库包 | `searx-bash "<pkg>" --category packages` | `packages` |
| 搜索代码仓库 | `searx-bash "<query>" --category repos` | `repos` |
| 搜索 IT 资源 | `searx-bash "<query>" --category it` | `it` |
| 限制结果数 | `searx-bash "<query>" --limit N` | - |
| 按时间过滤 | `searx-bash "<query>" --time-range day/week/month/year` | - |

## 搜索助手用法

```bash
searx-bash <query> [OPTIONS]

选项:
  --server, -s       SearXNG 服务器 URL（默认：$SEARXNG_URL 或 http://localhost:8888）
  --category, -c     搜索类别（默认：general）
  --limit, -l        最大结果数（默认：10）
  --engines, -e      使用特定引擎
  --time-range, -t   时间过滤：day/week/month/year
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
searx-bash \'machine learning\' --server http://192.168.100.2:38080 --category it

# 通过环境变量使用远程服务器
export SEARXNG_URL=http://192.168.100.2:38080
searx-bash docker --category packages --limit 5

# 按时间过滤（最近一周）
searx-bash "rust news" --time-range week

# 按时间过滤（最近一天）
searx-bash "ai" --time-range day --limit 3
```

## 可用类别

查看所有类别：
```bash
curl -s "$SEARXNG_URL/config" | jq \'.categories\'
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
searx-bash "query" --json | jq \'.results[0:5] | .[] | {title, url}\'
```

### 深度研究 - 访问搜索结果页面

当搜索结果中发现感兴趣的页面时，可以使用以下工具访问和读取内容：

**推荐工具（优先级按顺序）：**
1. **agent-browser** ⭐ 推荐 - 自动化浏览器交互和内容提取
2. **web-fetch/web-search** - 获取网页内容并进行分析
3. **curl** - 直接获取页面原始内容

**使用流程示例：**

```bash
# 1. 先用 SearXNG 搜索获取候选结果
searx-bash "rust async framework" --category cargo

# 2. 优先使用 agent-browser 访问和提取页面内容
agent-browser open https://github.com/username/repo
agent-browser wait --load networkidle
agent-browser snapshot                      # 获取完整页面内容（推荐用于分析）
agent-browser get title                    # 获取页面标题

# 3. 使用 JSON 输出进行结构化解析
agent-browser snapshot --json | jq \'.accessibility_tree\'

# 4. 或使用 web-fetch 获取页面内容
web-fetch "https://github.com/username/repo" "extract README content and key features"

# 5. 简单场景使用 curl 快速读取
curl -s "https://github.com/username/repo" | grep -A 10 "description"
```

**场景建议：**
- 需要截图/交互操作 → agent-browser（优先）
- 快速信息获取 → curl
- 需要解析分析 → web-fetch + 提示词
- 大量页面分析 → Explore 子代理 + agent-usage.md

### 深度研究 - 多轮迭代理索与深度挖掘

深度研究不应止步于单次搜索。通过分析搜索结果中的关键词、引用和其他线索，可以进行多轮迭代理索以获取更深入的信息。

#### 迭代理索方法

**方法 1：从内容中提取关键词进行二次搜索**

```bash
# 1. 初步搜索获取概览
searx-bash "rust async framework" --category cargo

# 2. 分析结果，发现 "tokio"、"async-std" 等关键词
# 3. 基于关键词进行针对性搜索
searx-bash "tokio vs async-std performance" --category it
searx-bash "tokio best practices" --category it
searx-bash "tokio debugging async tasks" --category it
```

**方法 2：按时间范围进行追踪搜索**

```bash
# 1. 搜索当前最新信息
searx-bash "rust async" --time-range week --category it

# 2. 追踪历史变化
searx-bash "tokio 1.0 release notes" --time-range year --category it
searx-bash "rust async await evolution" --time-range year --category it
```

**方法 3：跨类别综合搜索**

```bash
# 1. 从 multiple 角度探索同一主题
searx-bash "tokio" --category cargo        # 包信息
searx-bash "tokio tutorial" --category it  # 教程文档
searx-bash "tokio" --category repos        # 代码仓库
searx-bash "tokio" --category "q&a"        # 实践问题
```

**方法 4：从搜索结果中发现线索链**

```bash
# 1. 初始搜索
searx-bash "rust web framework comparison" --category it

# 2. 发现结果中提到的框架：actix、axum、rocket
# 3. 逐一深入搜索
searx-bash "actix vs axum performance" --category it
searx-bash "axum tutorial" --category it
searx-bash "rocket framework async" --category it

# 4. 探索相关技术栈
searx-bash "tower middleware rust" --category cargo
searx-bash "hyper async server" --category cargo
```

#### 多轮执行建议

**阶段 1：广度探索**
```bash
# 使用多个相关搜索词，构建主题地图
searx-bash "rust async framework"
searx-bash "rust async patterns"
searx-bash "rust async best practices"
searx-bash "rust async performance"
```

**阶段 2：深度挖掘**
```bash
# 针对最有价值的主题进行深入搜索
searx-bash "tokio runtime architecture" --category it --limit 15
searx-bash "tokio scheduler implementation" --category it
searx-bash "tokio tracing debugging" --category it
```

**阶段 3：案例研究**
```bash
# 基于真实的代码仓库和项目进行学习
searx-bash "tokio production" --category repos --limit 10
searx-bash "tokio microservices example" --category repos
searx-bash "tokio error handling patterns" --category it
```

#### 迭代理索的最佳实践

✅ **推荐做法：**
- 每轮搜索记录关键发现和新的搜索方向
- 使用不同搜索引擎获取多样化结果：`--engine google`, `--engine duckduckgo`, `--engine wikipedia`
- 结合时间过滤追踪技术演进：`--time-range week/month/year`
- 使用 JSON 输出进行批量分析：`--json | jq '.results[] | .title'`

❌ **避免做法：**
- 仅进行一次搜索就得出结论
- 忽略搜索结果中的引用和相关链接
- 使用过于泛化的搜索词导致噪音过多
- 没有针对不同类别（cargo/it/repos）进行交叉验证

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
