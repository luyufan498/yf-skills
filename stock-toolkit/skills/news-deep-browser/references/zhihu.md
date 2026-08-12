# 知乎 (zhihu) 操作手册

> 辅助渠道。中期前瞻价值：搜"预计XX/评价XX"前瞻型问题，问题+答案形式有深度分析（比雪球长文更深）。实证：赛力斯预亏 15-18 亿问题 7/13 已有深度讨论，比销量快报早 3 周。**不触发雪球那种滑块验证。**

脚本路径（先 export）：
```bash
DIVE_SCRIPTS=/home/catmouse/Github_Project/yf-skills/stock-toolkit/skills/news-deep-browser/scripts
```

## 操作流程

### 0. 前置：保登录清指纹（开工前一次）

```bash
python3 "$DIVE_SCRIPTS/zh_cookie_clean.py"
```
删指纹 cookies、保留 z_c0 → 问题页不再 40362 且保持登录。40362 复发时重跑。

### 1. 搜索（推荐入口）

```bash
python3 "$DIVE_SCRIPTS/zh_search.py" "预计赛力斯"
```
输出 JSON：`{ok, query, count, results:[{text}]}`。搜索页不触发 40362，可作问题页的兜底渠道。选择器：`.SearchResult-Card, .SearchItem, .Card.SearchResult-Card`。

### 2. 问题页（深度回答）

```bash
python3 "$DIVE_SCRIPTS/cdp_drive.py" navigate <tid> "https://www.zhihu.com/question/<id>"
python3 "$DIVE_SCRIPTS/cdp_drive.py" read <tid>
```
**前置条件**：先做一次"保登录清指纹"（第 0 步），否则问题页 40362。

## 错误与解决

### ⚠ 40362 限流（"您当前请求存在异常，暂时限制本次访问"）—— 必踩的坑

**根因**：**cookie 绑定的设备指纹限流，与 IP、登录账号、导航速度、tab 历史都无关**。

**只影响 question 页**；搜索页/热榜页不触发。

**触发 cookie**：JOID / osd / SESSIONID / captcha_session_v2 / __snaker__id / gdxidpyhxdE（阿里云验证）/ HMACCOUNT 这类指纹/tracking cookies。
**不触发**：`z_c0`（登录 cookie）。

**实验证据（CDP 逐层 A/B）**：

| 状态 | 40362 | 登录 |
|---|---|---|
| 原始 20 条 cookies（含 z_c0） | ❌ 限流 | ✅ |
| 清空全部 zhihu cookies | ✅ 正常 | ❌ 变"登录/注册" |
| 清空后恢复原始 20 条 | ❌ 立即复发 | ✅ |
| **只留 z_c0（登录 cookie）** | ✅ 正常 | ✅ 已登录 |

### ✅ 修复：保登录清指纹（`zh_cookie_clean.py`）

清掉指纹/tracking cookies、**保留 z_c0** → 问题页正常打开且保持登录。副作用仅丢无用 tracking cookies，正常浏览时知乎会自动重发。

实现要点（脚本已内置）：
- 用 page target 的 WS 调 `Network.getAllCookies`（browser target 上该方法不存在，-32601）
- 逐个 `Network.deleteCookies` 删触发 cookie，**不要删 z_c0**
- 可选 `--backup PATH` 先备份全部 zhihu cookies

### 复发处理

正常浏览会重发 tracking cookies，若再次 403，重跑 `zh_cookie_clean.py` 即可。

## 甄别提醒

- 中期前瞻型：问题多为开放式讨论，信息密度高但需甄别答主立场
- 与雪球互补：雪球看情绪/流言节奏，知乎看中期基本面前瞻深度讨论
