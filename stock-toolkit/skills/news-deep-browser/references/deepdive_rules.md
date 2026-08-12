# 深度浏览规则(实测沉淀)

> 各网站操作流程与错误解决已按站独立成文：
> - **雪球** → `xueqiu.md`（操作流程 + 滑块验证/WAF/API 拦截）
> - **知乎** → `zhihu.md`（操作流程 + 40362 cookie 指纹限流修复）
> - **小红书** → `xiaohongshu.md`（排除记录）
>
> 本文保留跨站策略与工具级坑。

## 实证结论(2026-08-09，9 只 watchlist + 知乎/小红书)

### 渠道价值
- **雪球**：核心渠道。讨论区捕捉趋势恶化提前 1-2 天(赛力斯 8/1 提前讨论销量冰点，官方 8/3 才发)；题材股有深度长文
- **知乎**：辅助渠道。中期前瞻(赛力斯预亏 15-18 亿问题 7/13 已有深度讨论，比销量快报早 3 周)
- **小红书**：排除。IP 风控频繁 + 消费向信息密度低

### 股票类型 → 深挖优先级
| 类型 | 论坛价值 | 优先级 |
|---|---|---|
| 高关注+基本面变化 | 趋势恶化提前 1-2 天 | 高 |
| 题材/异动股 | 深度长文+题材跟踪 | 高 |
| 防御/稳定股 | 活跃但无前瞻信号 | 低 |
| 低关注度 | 稀疏/公告流 | 低 |

### 局限
1. V 底反转点论坛滞后(英维克 8/3 底部当天主流仍在喊跌)
2. 论坛情绪是趋势跟随者
3. 置信度过滤刚需(粉黑互撕噪音巨大)

## 裸 CDP 环境与清理（2026-08-11 起全面切换）

1. 脚本目录 `DIVE_SCRIPTS=/home/catmouse/Github_Project/yf-skills/stock-toolkit/skills/news-deep-browser/scripts`（源码仓库稳定路径，不依赖插件缓存版本目录）
2. 真实 Chrome 9222 登录态最完整（雪球股票页会滑块验证，headless 过不了）；连不上先确认 `--remote-debugging-port=9222`
3. `DIVE_SESSION_FILE`（cron 设为 `/tmp/news_deep_browser_tabs.json`）：dive 脚本 `new` 开 tab 时把 id 写入，任务收尾 `cdp_drive.py dedupe`（每站留 1 个）；cron 兜底才用 `clean` 全清
4. 无 agent-browser、无 daemon —— 从根上消除孤儿 daemon 造成的"开 tab 秒关"churn
5. cron 兜底：claude 退出后（无论成败）按 session 文件 curl `GET /json/close/{id}` 关残留 tab（注意 **`/json/close` 是 GET，`/json/new` 才是 PUT**），只关 dive 开的、不碰用户手动开的
6. **/json/close 必须用完整 target id**（32 位 hex）——显示/打印时截断成 12 位会 404 `No such target id` 且标签页没关掉（2026-08-11 踩过）；脚本里一律 `t['id']` 原样传
7. **`/json/new?url=...` 有时只建空 tab 不导航**（Chrome 144 实测，url 参数可能被忽略，新 tab 卡 about:blank）——开新页优先用 `cdp_drive.py new <url>`（导航 + 记录 session），别用裸 /json/new
