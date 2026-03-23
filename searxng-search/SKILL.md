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
- **智能引擎选择**：自动从 bing、baidu、360search、sogou、yandex、qwant 等引擎中选择
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

| 任务 | 命令 | 说明 |
|------|---------|----------|
| 通用网络搜索 | `searx-bash "<query>"` | 自动选择引擎 |
| 中文搜索 | `searx-bash "中文查询"` | 优先中文友好引擎 |
| Cargo 包搜索 | `searx-bash "<crate>" --category cargo` | 搜索 crates.io |
| 多仓库包搜索 | `searx-bash "<pkg>" --category packages` | 搜索多个包管理器 |
| 代码仓库搜索 | `searx-bash "<query>" --category repos` | GitHub/GitLab 等 |
| IT 资源搜索 | `searx-bash "<query>" --category it` | 技术文档和资源 |
| 指定引擎 | `searx-bash "<query>" --engines bing,baidu` | 使用指定引擎 |
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
  --engines, -e      使用特定引擎（逗号分隔：--engines bing,google）
  --time-range, -t   时间过滤：day/week/month/year
  --retries, -r      最大重试次数，自动切换引擎（默认：2）
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

# 智能引擎选择（自动随机选择并重试）
searx-bash "人工智能最新发展" --limit 5

# 指定特定引擎
searx-bash "python tutorial" --engines bing,baidu --limit 5

# 增加重试次数应对限制
searx-bash "complex query" --retries 5
```

## 智能引擎选择与错误处理

### 自动引擎选择机制

`searx-bash` 脚本内置了智能引擎选择和重试机制，当不指定 `--engines` 参数时：

**自动选择的引擎组合：**
- `bing,yandex,yep` - 国际化搜索
- `bing,baidu` - 中英双语搜索
- `bing,360search` - 中文优化搜索
- `bing,sogou` - 社交媒体搜索
- `yandex,qwant` - 欧洲搜索引擎
- `baidu,360search` - 纯中文搜索

**工作流程：**
1. 随机选择一个引擎组合
2. 执行搜索并检查结果
3. 如果结果为空且引擎被限制（CAPTCHA/Suspended），自动重试
4. 每次重试选择不同的引擎组合
5. 获取有效结果或达到最大重试次数后返回

### 常见错误处理

#### 1. CAPTCHA 验证失败

**错误信息：**
```
Error: Search failed after 2 retries.
Query: your query
Blocked engines:
  - duckduckgo: CAPTCHA
  - google: Suspended: access denied
```

**原因分析：**
- 搜索引擎检测到自动化请求
- IP 地址被临时限流
- 请求频率过高触发反爬虫机制

**解决方案：**

```bash
# 方案 1：增加重试次数（自动尝试更多引擎）
searx-bash "your query" --retries 5

# 方案 2：指定已知可用的引擎
searx-bash "your query" --engines bing --limit 5
searx-bash "your query" --engines bing,baidu --limit 5

# 方案 3：等待一段时间后重试（避免限流）
sleep 10
searx-bash "your query" --limit 5

# 方案 4：使用不同搜索策略
# 如果中英文搜索受限，尝试：
searx-bash "your english keywords" --limit 5
searx-bash "您的中文关键词" --engines baidu --limit 5
```

#### 2. Suspended/Access Denied

**错误信息：**
```
Error: All search attempts failed after 2 retries.
Blocked engines:
  - brave: Suspended: too many requests
  - google: Suspended: access denied
```

**原因分析：**
- API 暂时被暂停服务
- 访问频率超过限制
- 服务器端维护或故障

**解决方案：**

```bash
# 使用其他可用引擎
searx-bash "your query" --engines "bing,yandex" --limit 5

# 使用多个引擎组合提高成功率
searx-bash "your query" --engines "bing,baidu,360search" --limit 5

# 查看可用引擎状态
curl -s "$SEARXNG_URL/config" | jq '.engines | map(select(.categories[]?. == "general")) | .[] | "\(.name): \((.enabled | tostring))"'
```

#### 3. No Results Found (无搜索结果)

**情况分析：**

```bash
# 情况 A：搜索引擎正常，但结果为空
searx-bash "very rare specific term" --limit 5
# 输出：No search results found for query: very rare specific term
# → 尝试简化搜索词或使用同义词

# 情况 B：引擎被限制且无备选引擎
searx-bash "query" --engines google --limit 5
# 输出：Error + Blocked engines: google: Suspended: access denied
# → 更换其他引擎

# 情况 C：查询过于复杂
searx-bash "中科曙光 SH603019 2026 最新新闻 近期 动态" --limit 5
# 输出：No results found
# → 尝试简化查询
searx-bash "Sugon SH603019 news" --limit 5
```

**优化策略：**

```bash
# 1. 简化搜索词
searx-bash "company stock news" --limit 10

# 2. 使用英文关键词（中文搜索容易被限制）
searx-bash "Sugon SH603019 stock analysis" --engines bing --limit 5

# 3. 分阶段搜索
searx-bash "Sugon SH603019" --limit 5
searx-bash "SH603019 latest news" --limit 5

# 4. 使用时间过滤
searx-bash "Sugon news" --time-range month --limit 10
```

### AI Agent 集成建议

当 AI Agent 需要处理 SearXNG 搜索时，建议遵循以下策略：

**成功处理流程：**
```python
# 伪代码示例
def search_with_fallback(query, max_retries=2):
    # 第一次尝试：自动引擎选择
    result = searx_bash(query, limit=10)
    if has_results(result):
        return result

    # 第二次尝试：指定可靠引擎
    result = searx_bash(query, engines="bing", limit=10)
    if has_results(result):
        return result

    # 第三次尝试：简化查询
    simplified_query = simplify_query(query)
    result = searx_bash(simplified_query, limit=10)
    return result
```

**错误处理决策树：**
```
搜索失败
├─ 引擎被限制 (CAPTCHA/Suspended)
│  ├─ 有指定引擎 → 检查是否被全部限制
│  │  ├─ 全部被限制 → 建议用户更换等待或使用其他工具
│  │  └─ 部分被限制 → 自动重试未限制的引擎
│  ├─ 无指定引擎 → 自动切换引擎重试
│  └─ 重试失败 → 报告错误信息给用户
│
├─ 无搜索结果
│  ├─ 查询词过于复杂 → 建议简化查询
│  ├─ 语言受限 → 建议使用英文搜索
│  └─ 真的没有结果 → 报告"无相关结果"
│
└─ 其他错误
   └─ 报告详细错误信息
```

### 最佳实践

**✅ 推荐做法：**

```bash
# 1. 优先使用自动引擎选择（让脚本自动选择最优引擎）
searx-bash "your query" --limit 5

# 2. 遇到限制时指定特定引擎
searx-bash "your query" --engines bing --limit 5

# 3. 中文搜索优先使用中文友好引擎
searx-bash "中文查询" --engines "bing,baidu" --limit 5

# 4. 重要搜索增加重试次数
searx-bash "critical query" --retries 5 --limit 10

# 5. 复杂查询分步执行
searx-bash "keyword1" --limit 5
searx-bash "keyword2" --limit 5
```

**❌ 避免做法：**

```bash
# 1. 指定已知受限的引擎
searx-bash "query" --engines google,duckduckgo  # 容易被限制

# 2. 不必要的复杂查询
searx-bash "中科曙光 SH603019 2026 最新新闻 近期 动态 这是一个非常长的查询"

# 3. 短时间内大量搜索（会被限流）
searx-bash "query1"; searx-bash "query2"; searx-bash "query3"  # 建议间隔搜索

# 4. 忽略错误信息
# 应该根据错误信息调整策略，而不是盲目重试
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

### 引擎选择策略

**查看可用引擎：**
```bash
# 获取所有引擎列表
curl -s "$SEARXNG_URL/config" | jq -r '.engines[].name'

# 查看特定类型的引擎
curl -s "$SEARXNG_URL/config" | jq -r '.engines[] | select(.categories[]?. == "general") | .name'

# 查看引擎详细信息
curl -s "$SEARXNG_URL/config" | jq '.engines[] | select(.name == "google")'
```

**引擎选择建议：**

| 搜索类型 | 推荐引擎 | 示例 |
|---------|---------|------|
| 通用搜索（中文优先） | `bing,baidu` | `--engines bing,baidu` |
| 通用搜索（国际优先） | `bing,yandex` | `--engines bing,yandex` |
| 技术/开发文档 | `bing,wikipedia` | `--engines bing,wikipedia` |
| 学术研究 | `bing,arxiv` | `--engines bing,arxiv` |
| 新闻资讯 | `bing,newsapi` | `--engines bing,newsapi` |
| 中文社交媒体 | `bing,sogou` | `--engines bing,sogou` |
| 多源验证 | `bing,baidu,yandex` | `--engines bing,baidu,yandex` |

**引擎组合策略：**

```bash
# 单一引擎（简单快速）
searx-bash "query" --engines bing

# 双引擎（平衡速度和覆盖）
searx-bash "query" --engines bing,baidu

# 多引擎（全面覆盖，但可能被限制）
searx-bash "query" --engines bing,baidu,yandex

# 自动选择（推荐，脚本智能选择）
searx-bash "query"  # 不指定 --engines
```

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
- 使用不同引擎组合获取多样化结果：`--engines bing,yandex`, `--engine baidu,360search`
- 结合时间过滤追踪技术演进：`--time-range week/month/year`
- 使用 JSON 输出进行批量分析：`--json | jq '.results[] | .title'`
- 遇到翻译限制时切换搜索引擎：从 `--engines bing,baidu` 切换到 `--engines bing,yandex`
- 重要搜索增加重试次数：`--retries 5`
- 对中文搜索优化：使用英文关键词或指定中文友好引擎

❌ **避免做法：**
- 仅进行一次搜索就得出结论
- 忽略搜索结果中的引用和相关链接
- 使用过于泛化的搜索词导致噪音过多
- 没有针对不同类别（cargo/it/repos）进行交叉验证
- 短时间内对同一搜索引擎发送太多请求（会被限制）
- 当引擎被限制时仍然尝试相同引擎而不是切换

#### 搜索过程中的引擎适配

**根据搜索结果调整引擎策略：**

```bash
# 1. 初始搜索使用自动选择
searx-bash "人工智能 最新发展" --limit 10

# 2. 如果发现引擎限制（如 CAPTCHA），切换引擎
# 从自动选择 -> 指定可靠性高的单个引擎
searx-bash "人工智能 最新发展" --engines bing --limit 10

# 3. 如果单一引擎仍有限制，切换到多引擎组合
searx-bash "人工智能 最新发展" --engines "bing,baidu,360search" --limit 10

# 4. 如果所有中文引擎受限，尝试中英混合搜索
searx-bash "artificial intelligence latest development China" --engines "bing,yandex" --limit 10

# 5. 逐步增加重试次数应对严格限制
searx-bash "重要查询" --retries 10 --engines "bing,yandex,qwant" --limit 15
```

#### 针对不同语言的搜索策略

**中文搜索优化策略：**

```bash
# 策略 1：中文友好引擎组合
searx-bash "中科曙光" --engines "bing,baidu,360search" --limit 5

# 策略 2：中文 + 英文组合（提高命中率）
searx-bash "中科曙光 SH603019 stock performance" --engines "bing,yandex" --limit 5

# 策略 3：纯英文搜索（中文引擎受限时）
searx-bash "Sugon SH603019 latest news" --engines "bing,yandex,qwant" --limit 5

# 策略 4：搜索公司英文名
searx-bash "Sugon Information Industry" --engines "bing,yandex" --limit 5
```

**英文搜索优化策略：**

```bash
# 策略 1：国际化引擎组合
searx-bash "artificial intelligence latest" --engines "bing,yandex,qwant" --limit 5

# 策略 2：学术搜索
searx-bash "machine learning research" --engines "bing,arxiv,wikipedia" --limit 5

# 策略 3：技术文档搜索
searx-bash "rust async tokio" --engines "bing,wikipedia" --limit 5
```

#### 限流预防和应对

**预防限流的搜索策略：**

```bash
# 1. 使用延迟和重试控制
searx-bash "query1" --retries 3
sleep 2  # 添加延迟
searx-bash "query2" --retries 3

# 2. 使用多个引擎分散请求（避免单一引擎过载）
searx-bash "query" --engines "bing,yandex"  # 分布到两个引擎
searx-bash "query" --engines "baidu,360search"  # 分布到其他引擎

# 3. 重要搜索前准备多个查询策略
# 准备方案 A：广泛搜索
searx-bash "broad topic" --limit 10

# 准备方案 B：具体搜索
searx-bash "specific aspect" --limit 5

# 准备方案 C：如果前两个都失败，简化搜索
searx-bash "simplified topic" --limit 5
```

**应对严重限流的策略：**

```bash
# 1. 暂停更长时间
sleep 30
searx-bash "urgent query" --engines "bing,yandex" --retries 5

# 2. 切换到不同的引擎组合
searx-bash "query" --engines "yandex,qwant"  # 从 bing 切换到欧洲引擎

# 3. 使用不同的搜索策略
# 从关键词搜索 -> 同义词搜索 -> 相关概念搜索
searx-bash "search_term"
searx-bash "synonym_term"
searx-bash "related_concept"
```

## AI Agent 搜索失败处理指南

当 AI Agent 需要处理 SearXNG 搜索失败时，基于智能引擎选择机制，建议遵循以下处理流程：

### 搜索失败分类与处理决策

#### 类型 1：引擎限制错误（CAPTCHA/Suspended）

**错误特征：**
- 输出包含 "Error: All search attempts failed"
- 显示具体失败的引擎（如 `google: Suspended: access denied`）
- 建议信息明确指向引擎限制

**处理策略：**

```
如果失败引擎包含 "CAPTCHA" 或 "Suspended"：
  └─ 这不是查询问题，而是搜索引擎限制问题
  └─ 不要改变搜索关键词
  └─ 执行以下重试策略：

  策略 1：增加重试次数
  → 使用 --retries 参数增加重试次数
  → 脚本会自动切换不同的引擎组合

  策略 2：指定不同的引擎
  → 清空当前引擎集合，选择未失败的引擎
  → 使用 --engines 指定特定的可靠引擎
  → 例如：--engines "bing" 或 --engines "bing,yandex"

  策略 3：语言切换（如果是中文搜索）
  → 如果中文字符的搜索失败
  → 尝试使用英文关键词
  → 例如：从 "中科曙光" 切换到 "Sugon" 或 "Sugon SH603019"

  策略 4：等待后重试
  → 添加适当的延迟（建议 5-10 秒）
  → 然后重新执行搜索
```

**实现示例：**

```bash
# 第一次尝试：自动引擎选择
searx-bash "中科曙光 SH603019" --limit 10

# 如果失败且显示引擎限制错误

# 第二次尝试：指定可靠性高的单一引擎
searx-bash "中科曙光 SH603019" --engines bing --limit 10

# 如果仍然失败

# 第三次尝试：切换语言（英文关键词）
searx-bash "Sugon SH603019" --engines bing --limit 10

# 如果仍然失败

# 第四次尝试：增加重试次数 + 延迟
sleep 10
searx-bash "Sugon SH603019" --engines "bing,yandex" --retries 10 --limit 15

# 如果所有尝试都失败

# 向用户报告情况和建议
# "搜索遇到搜索引擎限制。建议：1) 等待后重试 2) 使用其他搜索工具 3) 简化查询"
```

#### 类型 2：无搜索结果（不是错误）

**错误特征：**
- 输出 "No search results found for query: ..."
- 可能显示引擎被限制，但主要是因为结果为空

**处理策略：**

```
如果显示 "No search results found"：
  └─ 这可能是查询问题，不一定是引擎限制

  检查引擎限制信息：
  ├─ 如果显示引擎限制 → 先解决引擎限制（参考类型 1）
  └─ 如果没有引擎限制 → 这是查询优化问题

  查询优化策略：

  策略 1：简化搜索词
  → 移除过于具体或复杂的限定词
  → 例如：从 "2026年中科曙光最新股票分析报告"
  →       简化为 "中科曙光 股票" 或 "Sugon stock"

  策略 2：使用同义词或相关词
  → 如果公司名搜索无结果
  → 尝试股票代码、英文名、行业分类等

  策略 3：分阶段搜索
  → 不要一次性搜索复杂查询
  → 分解为多个简单搜索
  → 综合多次搜索结果

  策略 4：切换搜索类别
  → 尝试不同的搜索类别
  → 例如：--category general → --category news → --category repos
```

**实现示例：**

```bash
# 复杂查询失败
searx-bash "中科曙光 SH603019 2026年第一季度财报分析" --limit 10
# 输出：No search results found for query: ...

# 简化查询
searx-bash "中科曙光 财报 2026" --limit 10

# 仍然无结果？尝试不同关键词
searx-bash "Sugon SH603019 financial report 2026" --limit 10

# 尝试股票代码
searx-bash "SH603019 financial report" --limit 10

# 尝试时间过滤
searx-bash "Sugon financial report" --time-range year --limit 10

# 尝试不同类别
searx-bash "Sugon stock" --category it --limit 10
```

#### 类型 3：网络/服务器错误

**错误特征：**
- 无法连接到服务器
- 服务器返回 5xx 错误
- 响应超时

**处理策略：**

```
如果显示连接错误或服务器错误：
  └─ 这是基础设施问题，不是搜索或引擎问题

  策略 1：确认服务器状态
  → 检查 $SEARXNG_URL 是否正确
  → 测试基本连接：curl -I "$SEARXNG_URL"

  策略 2：检查配置
  → 确认服务器已启用 JSON 格式
  → 检查服务器限制和访问权限

  策略 3：报告给用户
  → 明确告知是服务器连接问题
  → 建议检查服务器状态或网络连接
```

### AI Agent 最佳实践总结

#### 搜索失败决策矩阵

| 失败类型 | 根本原因 | 解决方向 | 关键行为 |
|---------|---------|----------|----------|
| CAPTCHA/Suspended | 搜索引擎限制 | 更换引擎/增加重试 | 不改查询词 |
| 无结果 | 查询优化问题 | 优化查询词 | 改关键词/简化 |
| 连接错误 | 服务器/网络问题 | 检查基础设施 | 修复连接 |
| 格式错误 | 脚本/配置问题 | 修复脚本 | 检查配置 |

#### AI Agent 搜索流程（伪代码）

```
function smart_search(query, context):
    # 第一次尝试：自动引擎选择
    result = searx_bash(query)

    if is_success(result):
        return result

    # 分析失败原因
    error_type = analyze_error(result)

    if error_type == "ENGINE_LIMITED":
        # 引擎限制：不改查询，改引擎
        for engine_set in [["bing"], ["bing,yandex"], ["bing,yandex,qwant"]]:
            result = searx_bash(query, engines=engine_set)
            if is_success(result):
                return result
            sleep(3)

        # 尝试语言切换
        if contains_chinese(query):
            english_query = translate_to_english(query)
            result = searx_bash(english_query, engines=["bing,yandex"])
            if is_success(result):
                return result

    elif error_type == "NO_RESULTS":
        # 无结果：优化查询
        simplified_query = simplify_query(query)
        result = searx_bash(simplified_query)

        if is_success(result):
            return result

        # 尝试同义词
        synonyms = get_synonyms(query)
        for synonym in synonyms:
            result = searx_bash(synonym)
            if is_success(result):
                return result

    # 所有尝试都失败：报告给用户
    return format_error_report(query, error_type, tried_strategies)
```

#### 具体应用场景

**场景 1：股票信息查询**

```bash
# 用户请求：搜索中科曙光股票最新动态

# AI Agent 策略：
1. searx-bash "中科曙光 SH603019 最新动态" --limit 10
   # 结果：搜索引擎限制错误

2. searx-bash "中科曙光 SH603019" --engines bing --limit 10
   # 结果：无结果

3. searx-bash "Sugon SH603019 stock" --engines bing --limit 10
   # 结果：成功返回股票信息

# 如果第 3 步也失败：
4. searx-bash "Sugon stock" --engines "bing,yandex" --retries 5 --limit 15
   # 结果：成功返回一般股票信息
```

**场景 2：技术文档搜索**

```bash
# 用户请求：搜索 Rust async 框架对比

# AI Agent 策略：
1. searx-bash "Rust async framework comparison" --limit 10
   # 结果：成功返回文章列表

2. 用户还需要具体的代码示例
   searx-bash "tokio vs async-std code examples" --limit 10
   # 结果：搜索引擎限制（第 2 次搜索）

3. searx-bash "tokio vs async-std examples" --engines "bing,yandex" --retries 3 --limit 10
   # 结果：成功返回代码示例
```

#### 错误报告模板

当所有搜索尝试失败后，向用户提供清晰的错误报告：

```
搜索失败报告

搜索主题：[查询内容]
失败原因：[引擎限制/无结果/连接错误]

已尝试的策略：
1. 自动引擎选择（多个引擎组合）
2. 指定可靠引擎（bing, yandex 等）
3. [如果适用] 语言切换（中文→英文）
4. [如果适用] 查询简化
5. 增加重试次数（N 次）

建议的后续操作：
1. 等待几分钟后重试（避免引擎限流）
2. 使用其他搜索工具（如 agent-browser）
3. 修改搜索关键词尝试不同的表述方式
4. 检查网络连接和 SearXNG 服务器状态
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
