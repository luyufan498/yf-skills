# SearXNG for AI Agents

本指南说明 AI 代理（如 Claude）如何使用 SearXNG 获得增强的搜索能力。

## 为什么使用 SearXNG？

**问题**：内置的 WebSearch 工具通常有局限性：
- 每次查询限制约 10 条结果
- 无法搜索特定的包仓库（PyPI、npm、cargo）
- 无法直接控制使用的搜索引擎
- 可能有限速限制或限制

**解决方案**：SearXNG 提供：
- 直接访问 100+ 个搜索引擎
- 专门的包仓库搜索
- 完全控制类别和过滤器
- 无限的本地查询
- JSON API 用于编程访问

## AI 代理工作流程

### 1. 配置服务器地址

```bash
# 使用环境变量（推荐用于多个查询）
export SEARXNG_URL=http://your-server:port

# 或者使用 --server 标志（单次查询）
searx-bash --server http://your-server:port "query"
```

**何时配置**：
- 在开始搜索密集型任务之前
- 当用户明确请求包搜索时
- 当 WebSearch 工具返回结果不足时

### 2. 选择正确的类别

| 用户请求 | 类别 | 示例 |
|----------|------|------|
| "找 Rust 的异步库" | `cargo` | `searx-bash "async" --category cargo` |
| "搜索 React 相关 npm 包" | `packages`（过滤 npm） | `searx-bash "react" --category packages --json \| jq 'select(.engines[] == "npm")'` |
| "多语言包搜索" | `packages` | `searx-bash "logging" --category packages` |
| "Docker 代码仓库" | `repos` | `searx-bash "docker" --category repos` |
| "AI 相关学术论文" | `scientific publications` | `searx-bash "neural networks" --category "scientific publications"` |
| "常规技术搜索" | `it` | `searx-bash "kubernetes" --category it` |
| "技术问答" | `q&a` | `searx-bash "python async" --category "q&a"` |
| "Wikipedia 搜索" | `wikimedia` | `searx-bash "machine learning" --category wikimedia --json \| jq 'select(.engines[] == "wikipedia")'` |
| "查找定义" | `define` | `searx-bash "algorithm" --category define` |

> **提示**：需要完整的类别列表和详细用法？参考 [category-guide.md](category-guide.md)。

### 3. 执行搜索

**选项 A：searx-bash 脚本（推荐，格式化输出）**
```bash
# 使用环境变量
searx-bash "tokio" --category cargo --limit 5

# 使用 --server 标志
searx-bash "react" --server http://192.168.100.2:38080 --category packages
```

**选项 B：直接 curl（快速，可脚本化）**
```bash
curl -s "$SEARXNG_URL/search?q=tokio&format=json&categories=cargo" | \
  jq '.results[0:5] | .[] | {title, url, content}'
```

### 4. 解析和呈现结果

提取关键信息：
```bash
# 获取标题和 URL
jq '.results[] | {title, url}'

# 按特定引擎过滤
jq '.results[] | select(.engines[] == "npm")'

# 获取前 N 条结果
jq '.results[0:N]'

# 检查哪些引擎返回结果
jq '.results[0].engines'
```

## 常见的 AI 代理使用场景

### 包发现

**用户**："找一个 Rust HTTP 请求库"

**AI 代理流程：**
1. 确认服务器地址已配置
2. 搜索 cargo: `searx-bash "http requests" --category cargo`
3. 解析前 5 条结果
4. 呈现：标题、URL、描述
5. 可选：从 crates.io API 获取详细信息

**实现：**
```bash
searx-bash "http requests" --category cargo --limit 5
# 或使用 curl
curl -s "$SEARXNG_URL/search?q=http+requests&format=json&categories=cargo" | \
  jq -r '.results[0:5] | .[] | "[\(.title)](\(.url))\n  \(.content)\n"'
```

### 多仓库搜索

**用户**："不同语言中最好的日志库有哪些？"

**AI 代理流程：**
1. 使用 `categories=packages` 搜索通用术语
2. 按引擎（npm、crates.io、hex 等）分组结果
3. 按生态系统组织呈现

**实现：**
```bash
curl -s "$SEARXNG_URL/search?q=logging&format=json&categories=packages" | \
  jq 'group_by(.results[].engines[0]) |
      map({engine: .[0].engines[0], packages: map(.title)})'
```

### 学术研究

**用户**："找 transformer 架构的最新论文"

**AI 代理流程：**
1. 搜索 scientific publications 类别
2. 如需要按日期过滤
3. 提取：标题、URL、发布日期
4. 提供到 arXiv/论文的链接

**实现：**
```bash
searx-bash "transformer architecture" --category "scientific publications" --limit 10
# 或使用 curl 精细控制
curl -s "$SEARXNG_URL/search?q=transformer+architecture&format=json&categories=scientific+publications" | \
  jq '.results[] | {title, url, date: .publishedDate, source: .engines}'
```

### 代码示例搜索

**用户**："展示 Rust 中 async/await 的例子"

**AI 代理流程：**
1. 使用 `repos` 类别查找代码仓库
2. 或使用 `--engines github_code` 指定引擎
3. 提取代码仓库 URL

**实现：**
```bash
# 方法1: 使用 repos 类别
searx-bash "rust async await" --category repos --json | \
  jq '.results[] | select(.engines[] == "github") | {title, url}'

# 方法2: 指定特定引擎
searx-bash "rust async await" --engines github_code --json | \
  jq '{title, url}'
```

## AI 代理最佳实践

### 1. 配置远程服务器

```bash
# 检查环境变量是否设置
if [ -z "$SEARXNG_URL" ]; then
  echo "请设置 SEARXNG_URL 环境变量"
  echo "export SEARXNG_URL=http://your-server:port"
fi

# 或使用 --server 标志
searx-bash --server http://192.168.100.2:38080 "query"
```

### 2. 使用适当的类别

- 不要用 `general` 搜索包 - 使用 `packages`、`cargo` 等
- 用 `it` 进行广泛的技术搜索
- 用 `repos` 专门搜索 GitHub/GitLab
- 用 `--engines github_code` 进行代码搜索
- 用 `q&a` 查找技术问答
- 用 `wikimedia` 查阅 Wikipedia 等百科

### 3. 处理空结果

```bash
RESULTS=$(curl -s "$SEARXNG_URL/search?q=query&format=json" | jq '.results | length')
if [ "$RESULTS" -eq 0 ]; then
  echo "未找到结果。尝试更广泛的搜索..."
  # 尝试不同的类别或更广泛的术语
fi
```

### 4. 与其他工具结合

- 使用 SearXNG 查找包
- 然后使用包特定 API（crates.io、npm）获取详细信息

### 5. 尊重资源

- 不要在循环中频繁发送查询
- 尽可能重用结果
- 远程服务器由管理员管理，无需停止服务

## 错误处理

### SearXNG 无响应

```bash
# 检查服务器是否可达
curl -sf "$SEARXNG_URL/" > /dev/null 2>&1 || echo "服务器不可达"

# 检查配置
curl -sf "$SEARXNG_URL/config" | jq '.engines[] | select(.name == "pypi")'
```

### 空结果

1. 检查响应中的 `unresponsive_engines`
2. 尝试更广泛的搜索术语
3. 尝试不同的类别
4. 检查特定引擎是否宕机


## 性能提示

### 限制结果

```bash
# 使用 --limit 参数
searx-bash "query" --limit 10

# 或在 curl 输出中过滤
curl -s "...&format=json" | jq '.results[0:10]'
```

### 并行搜索

对于多个查询，并行运行：
```bash
curl -s "$SEARXNG_URL/search?q=query1&categories=cargo" > cargo.json &
curl -s "$SEARXNG_URL/search?q=query2&categories=packages" > packages.json &
wait
```

### 缓存结果

存储常用搜索：
```bash
# 缓存常用包
curl -s "$SEARXNG_URL/search?q=tokio&categories=cargo" > /tmp/tokio-search.json
```

## 集成示例

### Bash 函数

```bash
set_env_server() {
  if [ -z "$SEARXNG_URL" ]; then
    export SEARXNG_URL="http://your-server:port"
  fi
}

search_package() {
  local query="$1"
  local category="${2:-packages}"
  searx-bash "$query" --category "$category" --limit 5
}

# 使用
export SEARXNG_URL=http://192.168.100.2:38080
search_package "express" "packages"
search_package "tokio" "cargo"
```

### Python 集成

```python
import os
import requests

SEARXNG_URL = os.environ.get('SEARXNG_URL', 'http://localhost:8888')

def searxng_search(query, category='general', limit=10, server_url=None):
    url = server_url or SEARXNG_URL
    resp = requests.get(f'{url}/search', params={
        'q': query,
        'format': 'json',
        'categories': category
    })
    results = resp.json()['results'][:limit]
    return [{'title': r['title'], 'url': r['url'], 'content': r.get('content', '')}
            for r in results]

# 使用
packages = searxng_search('async', category='cargo', limit=5,
                         server_url='http://192.168.100.2:38080')
for pkg in packages:
    print(f"{pkg['title']}: {pkg['url']}")
```

### Bash 集成函数

```bash
# 使用 searx-bash 脚本
# 支持环境变量和 --server 标志

function searx-filter() {
  local query="$1"
  local engine="$2"
  local category="${3:-packages}"

  export SEARXNG_URL=${SEARXNG_URL:-http://your-server:port}

  local results=$(searx-bash "$query" --category "$category" --json | \
    jq -r ".results[] | select(.engines[] == \"$engine\") | \"\(.title): \(.url)\"")

  if [ -z "$results" ]; then
    echo "未找到来自 $engine 的结果"
  else
    echo "$results"
  fi
}

# 使用
export SEARXNG_URL=http://192.168.100.2:38080
searx-filter "express" "npm" "packages"
searx-filter "tokio" "crates.io" "cargo"
```

## 何时使用 SearXNG 

### 使用 SearXNG 当：
- 搜索包仓库（cargo、npm 等）
- 需要超过 10 条结果
- 需要按特定引擎过滤
- 搜索学术论文
- 需要完全控制搜索类别
- 其他工具达到限速限制



## 清理

远程服务器由管理员管理，客户端只需要清理临时文件：

```bash
# 清除本地临时文件（如果有）
rm -f /temp/*.json
```