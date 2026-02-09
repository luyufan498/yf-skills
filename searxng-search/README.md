---
name: searxng-search
description: 使用 SearXNG 实例增强的 Web 和软件包仓库搜索
version: 1.0.0
---

# SearXNG 搜索

SearXNG 是一个尊重隐私的元搜索引擎，聚合来自多个搜索引擎和软件包仓库的结果，返回纯净的 JSON 输出。

> **对于复杂的搜索任务**（多源软件包对比、学术研究、仓库分析），请创建 Explore 类型的子代理，并参考 [references/agent-usage.md](references/agent-usage.md) 获取完整指南。

## 快速开始

**1. 设置服务器地址（如果使用远程服务）：**
```bash
export SEARXNG_URL=http://your-server:port
# 或在每个命令中使用 --server 标志
```

**2. 使用搜索辅助工具：**
```bash
# 使用环境变量
export SEARXNG_URL=http://192.168.100.2:38080
scripts/searx-bash tokio --category cargo

# 使用 --server 标志
scripts/searx-bash docker --server http://192.168.100.2:38080 --category packages
```

**3. 或直接使用 curl：**
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
| 通用网页搜索 | `searx-bash "<query>"` | `general` |
| 搜索 Cargo/crates.io | `searx-bash "<crate>" --category cargo` | `cargo` |
| 搜索多仓库软件包 | `searx-bash "<pkg>" --category packages` | `packages` |
| 搜索代码仓库 | `searx-bash "<query>" --category repos` | `repos` |
| 搜索 IT 资源 | `searx-bash "<query>" --category it` | `it` |
| 限制结果数量 | `searx-bash "<query>" --limit N` | - |

## 搜索辅助工具使用

```bash
searx-bash <query> [OPTIONS]

Options:
  --server, -s       SearXNG 服务器地址（默认：$SEARXNG_URL 或 http://localhost:8888）
  --category, -c     搜索类别（默认：general）
  --limit, -l        最大结果数（默认：10）
  --json             输出原始 JSON
  --help, -h         显示此帮助

Environment:
  SEARXNG_URL        默认服务器地址

常用类别：
  general, cargo, packages, it, repos, science, news, "q&a", wikimedia
```

**示例：**
```bash
# 本地服务器（默认）
searx-bash tokio --category cargo

# 通过标志指定远程服务器
searx-bash 'machine learning' --server http://192.168.100.2:38080 --category it

# 通过环境变量指定远程服务器
export SEARXNG_URL=http://192.168.100.2:38080
searx-bash docker --category packages --limit 5
```

## 可用类别

运行以下命令查看所有类别：
```bash
curl -s "$SEARXNG_URL/config" | jq '.categories'
```

重要类别：
- **general**：通用网页搜索（默认）
- **cargo**：来自 crates.io 的 Rust crates
- **packages**：多仓库（npm、rubygems、hex、docker hub、alpine 等）
- **it**：IT/技术资源（包括 GitHub、Docker Hub、crates.io）
- **repos**：代码仓库
- **q&a**：问答社区（Stack Overflow、Ask Ubuntu、Super User 等）
- **wikimedia**：维基媒体项目（Wikipedia、Wikibooks、Wikiquote 等）
- **scientific publications**：学术论文

> **查看 [references/category-guide.md](references/category-guide.md) 以获取完整的类别列表和详细用法。**

## 高级用法

对于复杂任务，使用 `--json` 标志获取原始 JSON 输出：

```bash
searx-bash "query" --json | jq '.results[0:5]'
```

> **对于高级用法**（多源过滤、复杂模式），请创建 Explore 类型的子代理并参考 [references/agent-usage.md](references/agent-usage.md)。

## 配置

远程访问所需的配置：

```yaml
use_default_settings: true
search:
  formats:
    - html
    - json
server:
  secret_key: "change-me-in-production"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  public_instance: false
```

**关键配置项：**
- `bind_address: "0.0.0.0"` - 接受来自任何 IP 的连接
- `formats: [html, json]` - 启用 JSON API（必需）
- `limiter: false` - 禁用速率限制以用于可信使用
- `public_instance: false` - 禁用隐私增强功能（可选）

查看 [SearXNG 设置文档](https://docs.searxng.org/admin/settings/settings.html) 了解所有选项。

## 部署

### 服务器部署

使用 SearXNG Docker 镜像进行容器化部署：

```bash
# 创建配置目录
mkdir -p /var/searxng-config

# 创建最小化的 settings.yml
cat > /var/searxng-config/settings.yml << 'EOF'
use_default_settings: true
search:
  formats:
    - html
    - json
server:
  secret_key: "change-me-in-production"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  public_instance: false
EOF

# 使用 podman 启动
podman run --rm -d --name searxng \
  -p 8080:8080 \
  -v /var/searxng-config:/etc/searxng:Z \
  docker.io/searxng/searxng:latest

# 或使用 docker 启动
docker run --rm -d --name searxng \
  -p 8080:8080 \
  -v /var/searxng-config:/etc/searxng \
  docker.io/searxng/searxng:latest
```

**远程访问的重要配置项：**
- `bind_address: "0.0.0.0"` - 接受来自任何 IP 的连接
- `formats: [html, json]` - 启用 JSON API
- `limiter: false` - 禁用速率限制以用于可信使用
- `public_instance: false` - 禁用隐私增强功能（可选）

### 查看日志

```bash
podman logs searxng  # 或：docker logs searxng
```

### 高级配置

查看 [SearXNG 设置文档](https://docs.searxng.org/admin/settings/settings.html) 了解所有选项。

向默认配置添加 JSON 输出的最小配置：
```yaml
use_default_settings: true
search:
  formats:
    - html
    - json
```