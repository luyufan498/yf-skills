# X (Twitter) 操作手册

> 国际情报渠道（外网消息）。X 是外网信息源，适合搜国际科技 + **所有关注行业**的海外动态：AI/芯片、商业航天/卫星、光通信/CPO、存储、创新药、机器人等。这些不一定直接相关/不一定炒股用，但值得了解。

## 行业词表（动态生成，2026-08-26 起——不再固定全搜）

**动态流程（每次扫描先确定当前关注行业，再生成词表）**：

```bash
# 1) 读当前池内 active 股票 → 所属行业（newsdb）
#    pool 表（master_pool.db）active 股票 → industry_stocks 查 industry_id
#    → industries.name 得到行业名列表（如：光模块/商业航天/存储芯片/AI算力...）
# 2) 行业名 → 英文关键词（下方映射表，按行业名匹配）
# 3) 生成的词表 = 当前命中行业的映射关键词（只搜关注行业，新行业自动覆盖、移除行业自动不搜）
```

**行业名 → 关键词映射表**（覆盖 newsdb 主要行业）：

| 行业名（newsdb） | X 搜索关键词 |
|---|---|
| AI算力/算力/AI/AI大模型/云计算/GPU芯片 | `AI chip` `GPU` `Nvidia` `inference` `data center` |
| 半导体/存储/存储芯片/半导体设备 | `semiconductor` `HBM` `DRAM` `TSMC` `memory chip` |
| 商业航天/卫星互联网/北斗 | `satellite` `Starlink` `space launch` `Beidou` `commercial space` |
| 光模块/光通信/通信设备 | `optical module` `CPO` `1.6T` `silicon photonics` `LPO` |
| 医药/生物医药/CXO/疫苗/医疗器械 | `biotech` `FDA` `clinical trial` `GLP-1` `ADC` `pharma` |
| 人形机器人/机器人 | `humanoid robot` `robotics` `Unitree` `Figure` |
| 新能源汽车/锂电池/磷酸铁锂 | `EV` `battery` `BYD` |
| 磁性材料 | `magnetics` `inductor` `power choke` |
| PCB | `PCB` |
| 磷化铟/化合物半导体 | `InP` `indium phosphide` |
| OLED/消费电子 | `OLED` `display` `Apple` `iPhone` `consumer electronics`（2026-08-31 审计补：苹果等大厂公司动态属大盘外事件，曾漏 CEO 交接） |
| 量子计算 | `quantum` |
| 脑机接口 | `BCI` `brain-computer interface` |
| 液冷/温控 | `liquid cooling` `data center cooling` |

扫描时按命中行业分组轮询（每轮每组 1-2 词，可自适应增删）；推荐流（For you/Explore）本身会覆盖"没想到去搜的"。行业名匹配不到映射的，用行业名直译或跳过（如实报告）。

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
