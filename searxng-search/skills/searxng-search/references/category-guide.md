# SearXNG 类别搜索指南

本文档详细说明所有可用类别及其支持的引擎。

## 可用类别

获取完整列表：
```bash
curl -s "$SEARXNG_URL/config" | jq -r '.categories[]'
```

当前类别（实际部署）：
- apps
- books
- **cargo** ⭐
- currency
- **define** ⭐
- dictionaries
- files
- general
- icons
- images
- **it** ⭐
- lyrics
- map
- movies
- music
- news
- other
- **packages** ⭐
- **q&a** ⭐
- radio
- **repos** ⭐
- science
- scientific publications
- shopping
- social media
- software wikis
- translate
- videos
- weather
- web
- **wikimedia** ⭐

(⭐ = 最适合开发工作)

## 面向开发的类别

### 1. `packages` - 多仓库包搜索

**包含的引擎：**
- npm (JavaScript/Node.js)
- crates.io (Rust)
- hex (Erlang/Elixir)
- hoogle (Haskell)
- metacpan (Perl)
- packagist (PHP/Composer)
- docker hub (容器镜像)
- alpine linux packages
- lib.rs (Rust 替代注册表)

**示例：**
```bash
# 使用环境变量
export SEARXNG_URL=http://your-server:port
curl -s "$SEARXNG_URL/search?q=express&format=json&categories=packages" | \
  jq '.results[] | {title, url, engine: .engines[0], content}'

# 或使用 searx-bash
searx-bash --server http://your-server:port "express" --category packages --json | jq '.results[] | {title, url, engine}'
```

**使用场景：**
- 在多个生态系统中查找包
- 比较不同语言的实现
- 发现工具的容器镜像

### 2. `cargo` - Rust Crates 专用

**包含的引擎：**
- crates.io

**示例：**
```bash
searx-bash "tokio" --category cargo

# 或使用 curl
curl -s "$SEARXNG_URL/search?q=tokio&format=json&categories=cargo" | \
  jq '.results[] | {title, url, content}'
```

**使用场景：**
- 查找 Rust crates
- 浏览 crates.io 搜索结果
- 获取 crate 描述

### 3. `it` - IT/技术资源

**包含的引擎：**
- GitHub
- Docker Hub
- Stack Overflow
- crates.io
- GitLab
- 还有更多技术导向的来源

**示例：**
```bash
searx-bash "kubernetes helm" --category it --limit 5

# 或使用 curl
curl -s "$SEARXNG_URL/search?q=kubernetes+helm&format=json&categories=it" | \
  jq '.results[0:5] | .[] | {title, url, engines}'
```

**使用场景：**
- 广泛的技术搜索
- 在一次查询中查找 GitHub 仓库、Docker 镜像和技术文档
- Stack Overflow 问答

### 4. `repos` - 代码仓库

**包含的引擎：**
- GitHub
- GitLab
- Codeberg
- Gitea 实例

**示例：**
```bash
searx-bash "machine learning" --category repos --json | \
  jq '.results[] | select(.engines[] == "github") | {title, url, content}'
```

**使用场景：**
- 查找源代码仓库
- 发现开源项目
- 搜索代码示例

### 5. `q&a` - 问答社区

**包含的引擎：**
- Stack Overflow
- Ask Ubuntu
- Super User
- discuss.python
- caddy.community
- pi-hole.community

**示例：**
```bash
# 搜索 Stack Overflow 中的 Python 问题
searx-bash "python list comprehension" --category "q&a" --limit 5

# 或使用 curl
curl -s "$SEARXNG_URL/search?q=rust+ownership&format=json&categories=q&a" | \
  jq '.results[] | {title, url, engines}'
```

**使用场景：**
- 快速查找技术问答
- 获取社区解答
- 学习最佳实践

### 6. `define` - 定义/词典搜索

**包含的引擎：**
- Wordnik

**示例：**
```bash
searx-bash "asynchronous" --category define

# 或使用 curl
curl -s "$SEARXNG_URL/search?q=algorithm&format=json&categories=define" | \
  jq '.results[]'
```

**使用场景：**
- 查询单词定义
- 获取词汇解释
- 理解术语

### 7. `wikimedia` - 维基百科项目

**包含的引擎：**
- Wikipedia (维基百科)
- Wikibooks (维基教科书)
- Wikinews (维基新闻)
- Wikiquote (维基语录)
- Wikisource (维基文库)
- Wikispecies (维基物种)
- Wiktionary (维基词典)
- Wikiversity (维基学院)
- Wikivoyage (维基导游)

**示例：**
```bash
# 搜索 Wikipedia
searx-bash "machine learning" --category wikimedia --json | \
  jq '.results[] | select(.engines[] == "wikipedia")'

# 或使用 curl
curl -s "$SEARXNG_URL/search?q=rust+programming&format=json&categories=wikimedia" | \
  jq '.results[0:3]'
```

**使用场景：**
- 获取背景知识和解释
- 查找教程和学习资料
- 参考权威信息源

## 面向研究的类别

### `scientific publications`

**包含的引擎：**
- arXiv
- CrossRef
- Google Scholar
- PubMed
- Semantic Scholar
- 还有很多

**示例：**
```bash
searx-bash "neural networks" --category "scientific publications" --json | \
  jq '.results[0:3] | .[] | {title, url, content, publishedDate}'
```

### `science`

通用科学资源和数据库。

**示例：**
```bash
searx-bash "quantum computing" --category science --json
```

## 多类别搜索

用逗号组合类别：

```bash
# 使用 curl
curl -s "$SEARXNG_URL/search?q=docker&format=json&categories=packages,it,repos" | \
  jq '.results[] | {title, url, engines, category}'

# 使用 searx-bash (需要先设置环境变量或使用 --server)
export SEARXNG_URL=http://your-server:port
searx-bash "docker" --category "packages,it,repos" --json | \
  jq '.results[] | {title, url, engines, category}'
```

这会同时搜索 Docker Hub、GitHub 和其他 IT 资源。

## 按引擎过滤结果

搜索后，按特定引擎过滤：

```bash
# 搜索 packages，只保留 npm 结果
searx-bash "react" --category packages --json | \
  jq '.results[] | select(.engines[] == "npm")'

# 搜索 IT，只保留 GitHub 结果
searx-bash "rust" --category it --json | \
  jq '.results[] | select(.engines[] == "github")'

# 搜索 packages，只保留 crates.io 结果
searx-bash "serde" --category packages --json | \
  jq '.results[] | select(.engines[] == "crates.io")'

# 搜索 q&a，只保留 Stack Overflow 结果
searx-bash "python list" --category "q&a" --json | \
  jq '.results[] | select(.engines[] == "stackoverflow")'
```

## 检查引擎可用性

查看哪些引擎为某个类别配置了：

```bash
# 检查 packages 类别中的所有引擎
curl -s "$SEARXNG_URL/config" | \
  jq '.engines[] | select(.categories[] | contains("packages")) | .name'

# 检查 cargo 类别中的所有引擎
curl -s "$SEARXNG_URL/config" | \
  jq '.engines[] | select(.categories[] | contains("cargo")) | .name'

# 检查 q&a 类别中的所有引擎
curl -s "$SEARXNG_URL/config" | \
  jq '.engines[] | select(.categories[] | contains("q&a")) | .name'

# 检查 wikimedia 类别中的所有引擎
curl -s "$SEARXNG_URL/config" | \
  jq '.engines[] | select(.categories[] | contains("wikimedia")) | .name'
```

检查特定引擎是否启用：

```bash
curl -s "$SEARXNG_URL/config" | \
  jq '.engines[] | select(.name == "npm")'
```

## 高级：特定引擎搜索

强制只使用特定引擎进行搜索：

```bash
# 只使用 npm
searx-bash "typescript" --engines npm --json

# 使用 curl
curl -s "$SEARXNG_URL/search?q=typescript&format=json&engines=npm" | \
  jq '.results[]'

# 只使用 crates.io
searx-bash "async" --engines crates.io --json

# 使用 curl
curl -s "$SEARXNG_URL/search?q=async&format=json&engines=crates.io" | \
  jq '.results[]'
```

## Bash 辅助函数

```bash
# 按引擎分组搜索 packages
search_packages() {
  local query="$1"
  curl -s "$SEARXNG_URL/search?q=$(printf '%s' "$query" | jq -sRr '@uri')&format=json&categories=packages" | \
    jq 'group_by(.results[].engines[0]) | map({engine: .[0].engines[0], packages: map(.title)})'
}

# 搜索特定类别
searx_cat() {
  local query="$1"
  local category="$2"
  local limit="${3:-10}"

  curl -s "$SEARXNG_URL/search?q=$(printf '%s' "$query" | jq -sRr '@uri')&format=json&categories=$category" | \
    jq ".results[0:$limit] | .[] | {title, url, content, engines}"
}

# 多类别搜索
searx_multi() {
  local query="$1"
  local categories="$2"
  local limit="${3:-10}"

  curl -s "$SEARXNG_URL/search?q=$(printf '%s' "$query" | jq -sRr '@uri')&format=json&categories=$categories" | \
    jq ".results[0:$limit] | .[] | {title, url, content, engines, category}"
}

# 搜索问答 (Stack Overflow 等)
search_qa() {
  local query="$1"
  local limit="${2:-5}"

  searx-bash "$query" --category "q&a" --limit "$limit"
}

# 维基百科搜索
search_wiki() {
  local query="$1"
  local limit="${2:-3}"

  searx-bash "$query" --category wikimedia --json | \
    jq --arg limit "$limit" '.results[] | select(.engines[] == "wikipedia")' | \
    head -n "$limit"
}
```

使用：
```bash
# 设置服务器
export SEARXNG_URL=http://your-server:port

# 查找 express 相关包并按引擎分组
search_packages "express"

# 搜索 cargo 类别，限制 5 条结果
searx_cat "tokio" "cargo" 5

# 多类别搜索
searx_multi "docker" "packages,it,repos" 10

# 搜索 Stack Overflow 中关于 Rust 的问题
search_qa "rust ownership" 5

# 在 Wikipedia 中搜索机器学习
search_wiki "machine learning" 3
```

## 常见模式

### 在所有生态系统中查找一个包：
```bash
searx-bash "http client" --category packages --json | \
  jq '.results | group_by(.engines[0]) | map({engine: .[0].engines[0], packages: map(.title)})'
```

### 技术文档搜索：
```bash
searx-bash "rust async programming" --category it --json | \
  jq '.results[] | select(.url | contains("doc")) | {title, url}'
```

### 学术研究：
```bash
searx-bash "transformer architecture" --category "scientific publications" --json | \
  jq '.results[] | {title, url, date: .publishedDate, content}'
```

### 查找 Docker 镜像：
```bash
searx-bash "nginx" --category packages --json | \
  jq '.results[] | select(.engines[] == "docker hub") | {title, url, content}'
```

### 查找 GitHub 仓库：
```bash
searx-bash "machine learning" --category repos --json | \
  jq '.results[] | select(.engines[] == "github") | {title, url, content}'
```

### 查找技术问答：
```bash
searx-bash "python async await" --category "q&a" --json | \
  jq '.results[] | {title, url, engines}'
```

### 批量搜索多个包：
```bash
for pkg in tokio serde reqwest; do
  echo "=== $pkg ==="
  searx-bash "$pkg" --category cargo --json | \
    jq -r '.results[0] | "\(.title)\n\(.url)\n"'
done
```

### 综合搜索 - 查找某个主题的多方面资源：
```bash
topic="rust async"
echo "=== Packages ($topic) ==="
searx-bash "$topic" --category packages --limit 3

echo -e "\n=== Q&A ($topic) ==="
searx-bash "$topic" --category "q&a" --limit 3

echo -e "\n=== Wikipedia ($topic) ==="
searx-bash "$topic" --category wikimedia --json | \
  jq '.results[0] | "\(.title)\n\(.url)"'
```

## 服务器配置提示

对于远程 SearXNG 实例，确保以下配置项正确设置：
- `bind_address: "0.0.0.0"` - 接受来自任何 IP 的连接
- `formats: [html, json]` - 启用 JSON API
- `limiter: false` (可选) - 为可信使用禁用限速
- `public_instance: false` (可选) - 禁用隐私增强功能

检查配置：
```bash
curl -s "$SEARXNG_URL/config" | jq '{formats: .formats, bind_address: .server.bind_address}'
```

## 类别对比参考

| 任务 | 推荐类别 | 说明 |
|------|----------|------|
| 查找 Rust crates | `cargo` | 专用于 Rust 包 |
| 查找跨语言包 | `packages` | 支持多个生态系统的包仓库 |
| 查找技术文档 | `it` | IT/技术资源，包括 GitHub、文档等 |
| 查找开源代码 | `repos` | 源代码仓库 (GitHub/GitLab 等) |
| 技术问答 | `q&a` | Stack Overflow、 Ask Ubuntu 等 |
| 查找定义 | `define` | 词典/定义搜索 |
| 背景知识 | `wikimedia` | Wikipedia 等维基项目 |
| 学术论文 | `scientific publications` | arXiv、PubMed、Google Scholar 等 |
| 技术搜索 | `it` | 广泛的技术资源搜索 |
| 代码搜索 | 使用 `--engines github_code` | 指定 GitHub Code Search 引擎 |