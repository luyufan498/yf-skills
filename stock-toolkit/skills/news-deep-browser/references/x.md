# X (Twitter) 操作手册

> 国际科技/AI 情报渠道（外网消息）。X 主要是外网信息，适合搜 AI 相关、国际科技消息：芯片涨价小道消息、第三方发现的芯片新用法（如 170HX 解锁致咸鱼二手价疯涨）、存储芯片变化、谷歌论文压缩显存传闻等。这些不一定直接相关/不一定炒股用，但值得了解。

脚本路径（先 export）：
```bash
DIVE_SCRIPTS=/home/catmouse/Github_Project/yf-skills/stock-toolkit/skills/news-deep-browser/scripts
```

## 操作流程（先推荐后搜索）

```bash
# 1) 推荐流扫描：For you 首页(已关注账号+算法推荐) + Explore 趋势(全球热点)
# 注意：脚本实际参数为 --home / --explore（无 --both），需分两次跑
python3 "$DIVE_SCRIPTS/x_scan.py" --home
python3 "$DIVE_SCRIPTS/x_scan.py" --explore
# 2) 定向搜索：AI/芯片/HBM/大模型/具体事件
python3 "$DIVE_SCRIPTS/x_search.py" "AI chip" --f live --since 2026-08-09 --min-faves 50
```

对比去重 → 甄别 → 带置信度入库。推荐流覆盖「没想到去搜的」，搜索覆盖「定向关键词」。

### x_scan.py 输出
```json
{ "home": {"count":N, "tweets":[{author,text,url,time}]},
  "explore": {"x-trends": {trends:[...]}, "x-timeline": {tweets:[...]}} }
```

### x_search.py 参数
`--f top|live`（热门/最新）、`--since YYYY-MM-DD`、`--min-faves N`、`--max N`。

## 抓取选择器

- 推文：`article[data-testid=tweet]`，正文 `[data-testid=tweetText]`，作者 `[data-testid=User-Name]`，时间 `time[datetime]`，原文链接 `a[href*="/status/"]`
- 趋势：`div[data-testid=trend]`（含分类如 Entertainment/Business & finance）
- 登录判定：`[data-testid=SideNav_AccountSwitcher_Button]` 存在 = 已登录

## 环境与错误解决

- 需要 X 登录态（真实 Chrome 9222；**无滑块验证**，不像雪球）
- 未登录会出现登录墙，需先确认 Chrome 登录 X
- 页面加载慢（重型 SPA）：脚本已做 30s `readyState` 轮询 + 4-8s 渲染缓冲
- **付费订阅号主页**：主按钮是 `Subscribe` 不是 `Follow`（如 @amitisinvesting）→ 关注时点顶部 `Follow` 按钮（按 y 坐标最小判断），别误触订阅
- 侧边栏 "Who to follow" 有推荐账号的 Follow 按钮，精确关注时用 `[data-testid$="-follow"]` 或按位置判断，别误关注

## 固定搜索词表（X 扫描 cron 用）

cron（`cron_x_scan.sh`，每天 03:17）按此词表定向搜索，agent **可根据结果自适应增删**（某词反复无价值→降低/去掉；发现新热点→补词）：

```
大模型 / LLM
AI chip / GPU
HBM / DRAM / memory chip
存储涨价 / chip price
chip unlock / GPU unlock        # 170HX 解锁这类"第三方发现新用法"
```

扫描流程：先 `x_scan.py --home` + `--explore`（分两次）看推荐流，再对每个词 `x_search.py "<词>" --f live --since 近3天 --min-faves 30`，合并去重 → 甄别 → 入库。

## 甄别提醒

- For you 首页噪音大（算法推娱乐/无关）→ **只留 AI/芯片/科技/财经相关**，情绪贴/广告跳过
- Explore 趋势有地区性（当前显示香港区）→ 优先 **Business & finance** 类趋势
- 国际小道消息带置信度入库：多方一致可 conf 2（community），单方传闻 conf 1（rumor），**不推动买卖结论**
- 行业级消息挂到 `GPU/AI算力芯片`、`计算机设备/AI算力` 等 industry 事件下

## 已关注账号（2026-08-11，提升推荐流覆盖）

- **AI 情报**：@MelvinInvests（AI 分析师）、@teedubya（AI 基础设施）、@amitisinvesting（NVIDIA/资本开支复盘）
- **存储/HBM**：@algotradingdesk、@yianisz
- **大模型**：@foodtruckbench（benchmark 榜）、@0xWhiteMage（模型产品分析）

新发现有质量账号可关注（`cdp_drive.py` 导航 profile → 点 Follow），持续扩充推荐覆盖。
