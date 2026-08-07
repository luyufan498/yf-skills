---
name: news-database
description: 独立新闻库（newsdb CLI + SQLite）。新闻采集 agent 负责按 时效性×层级 维护，分析 agent 通过查询端命令读取。包含事件聚合（语义去重）、重要度打标、异动刷新请求队列。
---

# 📰 独立新闻库

Agent 整理后的消息源数据库。核心单元是**事件**（一个持续进展的主题），每条消息强制归属一个事件。入库前由 agent 通过 `newsdb lookup` 做语义去重判断：新进展→归属 / 全新→新建 / 无新信息→跳过。

## 安装（已装则跳过）
```bash
cd <yf-skills>/stock-toolkit/skills/news-database/scripts
uv tool install --editable .
```

## 数据库位置
`STOCK_NEWS_DB` 环境变量指定，默认 `<cwd>/data/news/news.db`。

## 采集端命令（新闻 agent）
| 命令 | 作用 |
|------|------|
| `newsdb init` | 初始化库 |
| `newsdb lookup "关键词" [--entity-type X]` | 入库前查重/查事件归属 |
| `newsdb event <id>` | 查看事件详情/时间线 |
| `newsdb save --new-event/--event <id> ...` | 结构化写入消息 |
| `newsdb update-event <id> --latest-summary "..."` | 刷新事件最新摘要 |
| `newsdb resolve-event <id>` | 标记事件结束 |
| `newsdb track <code> --name ... [--industry] [--watchlist]` | 添加实体跟踪 |
| `newsdb ack-refresh <id>` | 处理完异动请求后确认 |

## 查询端命令（分析 agent）
| 命令 | 作用 |
|------|------|
| `newsdb query-stock <code> [--days N]` | 该股相关事件 |
| `newsdb query-industry <name> [--days N]` | 该行业相关事件 |
| `newsdb query-market [--days N]` | 宏观/政策/大盘事件 |
| `newsdb important [--min-importance 4] [--days N]` | 高重要度事件 |
| `newsdb search "关键词"` | FTS 全文检索 |
| `newsdb refresh-requests [--status pending]` | 读异动刷新请求 |

## 协作端命令
| 命令 | 作用 |
|------|------|
| `newsdb request-refresh <code> --signal "..." [--reason] [--priority]` | 分析 agent 写语义化异动请求 |
| `newsdb ack-refresh <id>` | 新闻 agent 确认处理完成 |

## 退出码契约
| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 目标不存在（event <id> / update-event / resolve-event / ack-refresh 目标不存在） |
| 2 | save 必须且只能指定 --event <id> 或 --new-event 之一 |
| 3 | save --event <id> 指向不存在的事件 |

## 注意
- `newsdb track` 对 `is_watchlist`/`priority` 总是**覆盖**为传入值：重复 track 不带 `--watchlist` 会把已 watchlist 的标的重置为 0。跟踪脚本每次应显式带 `--watchlist`。
- 重要性 `importance` 范围 1-5；相关度 `relevance`/`strength`/`priority` 建议 0-100，超出不校验，由 agent 自律。
- FTS trigram 需 ≥3 字符，2 字符中文查询（如"涨价"）自动回退 LIKE。
- 输出中 `[msg#N]` 是消息 id，可配合 `newsdb event <事件id>` 追溯完整时间线。
