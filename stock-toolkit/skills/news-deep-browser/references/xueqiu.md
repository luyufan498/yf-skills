# 雪球 (xueqiu) 操作手册

> 核心渠道。讨论区捕捉趋势恶化提前 1-2 天（赛力斯 8/1 提前讨论销量冰点，官方 8/3 才发）；题材股有深度长文。裸 CDP 驱动真实 Chrome 9222 登录态（股票页会滑块验证，headless 过不了）。

脚本路径（先 export）：
```bash
DIVE_SCRIPTS=/home/catmouse/Github_Project/yf-skills/stock-toolkit/skills/news-deep-browser/scripts
```

## 四层信息源

| 层 | URL | 用途 |
|---|---|---|
| 讨论区 | `https://xueqiu.com/S/<代码>` | 散户情绪/流言，新帖+热帖排序 |
| 资讯 tab | 股票页 tab | 聚合媒体新闻（财联社/证券日报/新浪/格隆汇），带来源+时间戳，高置信 |
| 7×24 快讯 | `https://xueqiu.com/today#/livenews` | 全市场实时快讯+热门榜，覆盖非 watchlist 新异动 |
| 今日话题 | `https://xueqiu.com/today` | 市场焦点 |

## 操作流程

### 1. 按股一键深挖（推荐）

```bash
python3 "$DIVE_SCRIPTS/xq_dig.py" <代码> --pages 2
```

一条命令完成：开页 → 过滑块(如有) → 读讨论 → 翻页到 N → 点资讯 tab，输出结构化 JSON：

```json
{ "code": "SZ002837", "captcha": {"triggered": false}, "pages": [{"page":1,"posts":[...]}], "news": {"count":11, "posts":[...]} }
```

抓取选择器（xq_dig.py 已内置）：帖子 `.timeline__item`，内容 `.content`，用户 `.user-name`/`.name`/`a.user`，日期 `.date-and-source`（如 "08-02 17:56· 来自Android"）。

### 2. 手动底层命令（按需）

```bash
python3 "$DIVE_SCRIPTS/cdp_drive.py" new "https://xueqiu.com/S/<代码>"      # 开页
python3 "$DIVE_SCRIPTS/cdp_drive.py" read <tid> --site xueqiu-discussion    # 读讨论
python3 "$DIVE_SCRIPTS/cdp_drive.py" paginate <tid> 2                       # 翻页(.pagination input 设值+Enter)
python3 "$DIVE_SCRIPTS/cdp_drive.py" click <tid> "text:资讯"                 # 点资讯 tab
python3 "$DIVE_SCRIPTS/cdp_drive.py" read <tid> --site xueqiu-news          # 读资讯
python3 "$DIVE_SCRIPTS/cdp_drive.py" new "https://xueqiu.com/today#/livenews" && cdp_drive.py read <tid> --site xueqiu-livenews  # 7×24
python3 "$DIVE_SCRIPTS/cdp_drive.py" close <tid>                            # 关 tab
```

## 错误与解决

### ⚠ 代码格式：必须带交易所前缀（2026-08-11 实测）
`xq_dig.py <代码>` 传裸代码（如 `300489`）会打开 `S/300489`，页面加载成通用标题、抓到 **0 条**。
必须传完整格式：`xq_dig.py SZ300489`（沪市 `SH603019` 同理）。URL 规范：`https://xueqiu.com/S/<交易所前缀+代码>`。

### ⚠ "text:资讯" 点击失效 → 用 JS eval 点（2026-08-11 实测）
`cdp_drive.py click <tid> "text:资讯"` 报 `no element`。改用 JS 直接点：
```bash
python3 "$DIVE_SCRIPTS/cdp_drive.py" eval <tid> "Array.from(document.querySelectorAll('div,span,a')).find(e=>e.innerText.trim()==='资讯' && e.offsetParent!==null)?.click()"
```
然后 `read <tid> --site xueqiu-news`。注意 read 会把整个页面 body 也带出来（bodyPreview），
资讯列表在 posts 里，筛选 `来自新闻` 条目即可。

### ⚠ 阿里云滑块验证（最常踩的坑）

**触发**：新股票页首次访问；`new` 新开 tab 也会触发；已验证 tab 导航到新股票页仍会触发（除非 WAF 已放行）。

滑块文字是"请按住滑块，拖动到最右边"（非缺口拼图）。**拖到底就通过**，但差最后 ~30px 会失败弹回。

| 方式 | 结果 |
|---|---|
| agent-browser `mouse move/down/up` | ❌ 滑块不跟随（事件没驱动） |
| JS 合成事件 dispatchEvent | ❌ isTrusted=false，无效 |
| 裸 CDP `Input.dispatchMouseEvent` | ✅ 有效，滑块跟随 |
| agent-browser `drag` | ❌ HTML5 拖拽事件，不适用 |

**有效解法（裸 CDP，xq_dig.py 已自动处理）**：
- 滑块 `#aliyunCaptcha-sliding-slider`，目标 `#aliyunCaptcha-sliding-body` 右端 - 滑块宽 - 2px（贴右端）
- 序列：`mouseMoved` → `mousePressed(left,1)` → 多步 `mouseMoved(buttons=1, 加速-减速轨迹)` → 末尾微调慢速 → `mouseReleased`，每步 sleep 0.03-0.15s
- 失败会弹回起点；成功标题变正常股票页
- 手动调用：`python3 "$DIVE_SCRIPTS/cdp_drive.py" captcha <tid>`

**实测轨迹**：agent-browser mouse 拖到 756（滑块~738）失败；CDP 拖到 914（滑块 894，差 30px）失败；CDP 拖到 950（滑块 924 贴右）成功。

### WAF 放行规律（省时间关键）

- 某只股票验证通过后，**同一浏览器上下文后续股票页直接放行**（本轮：英维克过验证后，中科曙光/奥来德导航即成功，无需再过）
- → 深挖多只股票时：**先处理第一只的滑块（耐心拖到底），后续直接用已验证 tab 导航**
- 不要用 `--session` 新建 tab 导航股票页（会触发验证）；用真实 Chrome 9222 现有 tab 导航

### API 拦截

- `stock.xueqiu.com` 行情 API **不走 WAF**
- `xueqiu.com/statuses/...` 讨论 API **走 WAF**：curl/页面 fetch 都被拦（返回验证 HTML），从已验证页面 fetch 也不放行

## 甄别提醒

- 论坛情绪是趋势跟随者：只在趋势方向变化上提前，反转点滞后（V 底当天论坛最看空）
- 置信度刚需：粉黑互撕噪音巨大；情绪宣泄帖跳过
