"""预期信号标注：消息 → (signal_direction, signal_type) 关键词分类 + 存量回填。

设计意图（2026-08，newsdb 预期信号调研落地）：
新闻里"预期/先导"类信号（回购、减持预披露、业绩预告、中标、定增等）能领先价格拐点，
但这些信号目前没有结构化字段，无法进统计验证。signal_direction/signal_type 把
"这条消息是否包含对未来走势的预期"标出来，配合技术指标做双层确认。

方向语义：
  bullish —— 偏多预期（知情方/机构表达看多）
  bearish —— 偏空预期（知情方/机构表达看空）
  event  —— 事件驱动（中性，到点需复核：预约披露日、解禁日、分红除权等）
  none   —— 无预期信号（纯已发生的事后描述）

type 语义（与 direction 正交，是"哪类信号"）：
  buyback          回购           increase         增持
  reduction        减持           pledge           质押
  placement        定增/增发       esop             员工持股
  earnings_preview 业绩预告/预约   win_bid          中标/订单
  rating           机构评级        research_visit   机构调研
  unlock           解禁            capital_action   其他资本运作（股权转让等）
  policy           政策/监管        industry_trend   行业趋势/景气
  ""               无
"""

# 分类规则：优先顺序 = 列表中靠前的先匹配
# 每条 (keywords, direction, type)；keywords 任一命中即归该类
SIGNAL_RULES = [
    # --- bearish：知情方看空 ---
    (["减持", "预披露减持", "减持计划"], "bearish", "reduction"),
    # 解除质押是风险下降（偏多），须在"质押"之前匹配（"解除质押"含"质押"子串）
    (["解除质押"], "bullish", "capital_action"),
    (["质押"], "bearish", "pledge"),
    (["业绩暴雷", "预亏", "净利润为负", "净利转亏", "由盈转亏",
      "业绩预告缺席", "预告缺席", "增收不增利", "不及预期", "低于市场预期"], "bearish", "earnings_preview"),
    (["解禁", "限售股上市"], "bearish", "unlock"),
    (["卖出评级", "目标价", "下调评级", "维持卖出"], "bearish", "rating"),
    (["警示函", "立案", "处罚", "风险提示", "退市风险", "ST"], "bearish", "policy"),
    (["财务数据不准确", "造假", "违规", "问询函", "关注函"], "bearish", "policy"),
    # --- event：事件驱动（中性） ---
    (["预约披露", "披露日", "半年度报告", "年报", "一季报", "中报", "业绩预告发布"],
     "event", "earnings_preview"),
    (["分红", "送股", "派息", "股权登记日", "除权"], "event", "capital_action"),
    # --- bullish：知情方看多 ---
    (["回购注销", "回购"], "bullish", "buyback"),
    (["增持"], "bullish", "increase"),
    (["中标", "订单", "大单", "战略合作"], "bullish", "win_bid"),
    (["定增", "增发", "非公开发行"], "bullish", "placement"),
    (["员工持股", "股权激励"], "bullish", "esop"),
    (["调研", "机构调研", "接待调研"], "bullish", "research_visit"),
    (["扩产", "投产", "产能", "新建产线", "放量"], "bullish", "industry_trend"),
    (["获资质", "认证", "通过验收", "获批"], "bullish", "win_bid"),
]

# 供 CLI/测试引用：合法 direction 值
VALID_DIRECTIONS = {"bullish", "bearish", "event", "none"}
VALID_SIGNAL_DIRECTIONS = VALID_DIRECTIONS

# 中文标签（查询端显示用）
DIRECTION_LABEL = {"bullish": "偏多", "bearish": "偏空", "event": "事件", "none": "无"}
SIGNAL_TYPE_LABEL = {
    "buyback": "回购", "increase": "增持", "reduction": "减持", "pledge": "质押",
    "placement": "定增", "esop": "员工持股", "earnings_preview": "业绩预告",
    "win_bid": "中标订单", "rating": "评级", "research_visit": "机构调研",
    "unlock": "解禁", "capital_action": "资本运作", "policy": "政策", "industry_trend": "行业景气",
}


def classify_signal(text: str) -> tuple:
    """关键词分类单条文本 → (direction, type)。无命中返回 ("none", "")。"""
    if not text:
        return "none", ""
    for keywords, direction, sig_type in SIGNAL_RULES:
        if any(k in text for k in keywords):
            return direction, sig_type
    return "none", ""


def backfill_signals(conn):
    """对存量消息回填信号标注：仅处理 signal_direction='none' 的消息（已标注不覆盖）。

    Returns:
        (回填条数, {signal_type: 计数}) 统计
    """
    # 只补 signal_direction='none' 的（direction 是"是否已标注"的唯一判据，type 可单独为空）。
    # 跳过 market 类事件：大盘/美股行情是已发生的市场描述，不含个股预期信号（如"签署MOU"等词会误标）。
    rows = conn.execute("""
        SELECT m.id, m.title, m.summary, m.message_type, e.entity_type
        FROM messages m JOIN events e ON m.event_id = e.id
        WHERE m.signal_direction = 'none' AND e.entity_type != 'market'
    """).fetchall()
    counts = {}
    n = 0
    for r in rows:
        # price_action（股价走势）是已发生的事后描述，不标预期；除非它本身就是公告/预告载体
        if r["message_type"] == "price_action":
            continue
        direction, sig_type = classify_signal(f"{r['title'] or ''} {r['summary'] or ''}")
        if direction == "none" and not sig_type:
            continue
        conn.execute(
            "UPDATE messages SET signal_direction=?, signal_type=? WHERE id=?",
            (direction, sig_type, r["id"]))
        counts[sig_type] = counts.get(sig_type, 0) + 1
        n += 1
    if n:
        conn.commit()
    return n, counts
