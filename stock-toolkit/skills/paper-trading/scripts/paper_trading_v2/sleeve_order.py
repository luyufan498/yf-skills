"""SleeveOrder — v12 消息挂单链路（plans/v12-news-order-20260903，契约-ptrade2 侧）

消息事件消费链的挂单承接层（专用心跳调用）：

    sleeve-open 开槽（既有，预算已拨付、成员段已建、fill_status='pending'）
      → place  挂单：band=[0.95,1.05]×anchor（anchor=事件入库时刻价快照，watch_scan
               payload 带入，非挂单时刻价），槽转 pending_order，TTL=下一节收盘
      → fill   检测价成交：心跳拍上现价触带 → 按检测价走 sleeve_fill 既有成交逻辑
               建成员段（三件套保护同口径）。fail-closed：价不在带内/无 band/TTL
               已过/槽态不符 → 一律拒绝且不建段。
      → expire 弃单：TTL 到期(expired) 或 价格破带(band_break) → pending_rejudge，
               预算冻结保留（不关槽不退款，坑继续占用）——槽生命周期与挂单 TTL 分离。
      → rejudge 重判：keep=以当时现价为新 anchor 刷新 band 重挂（回 pending_order，
               旧带作废）；close=复用 close-slot 关槽回款 + NEWS 信号行档案化清理。

设计纪律（沿用 M1.x/R2 惯例）：
- 状态转移全部事务内条件 UPDATE（WHERE 带守卫状态）+ rowcount 判定——并发双跑出局；
- 资金零新路径：place/expire/rejudge-keep 不动钱；fill 扣款在 fill_pending 内；
  rejudge-close 回款在 close_slot 内——本模块只搬状态与挂单字段；
- order_id 落列后旧 sleeve-fill（开盘价路径）扫描排除，两路成交互不踩踏；
- 全程 shadow_log 留痕（kind=news_order_place/fill/expire/rejudge_keep/rejudge_close）。

slot_id 契约参数 = event_slots.event_key（本库槽无独立自增 id，event_key 即权威主键）。
"""
import json
import os
import time
from datetime import datetime, timedelta

from paper_trading_v2.db import get_connection, migrate_db
from paper_trading_v2.sleeve_slots import now_iso, shadow_write, get_slot
from paper_trading_v2.sleeve_open import SleeveOpener

# v12 裁决 1：band = [0.95, 1.05] × anchor——保护极端：开盘拉高买不到/跳水接坑。
BAND_LO = 0.95
BAND_HI = 1.05
ORDER_STATES = ('pending_order', 'pending_rejudge')
EXPIRE_REASONS = ('expired', 'band_break')

# v12-patch 补洞轮（双模型对抗审计节，2026-09-03）：
SESSION_CLOSES = ((11, 30), (15, 0))   # E5 交易节收盘（裁决 3：11:30/15:00 取先到）
TTL_STALE_SECONDS = 300                # E3 报价新鲜度：时间戳距今 >5 分钟=陈旧
MAX_REJUDGE_KEEP = 2                   # E4 keep 次数帽（超限强制 close）
MAX_ANCHOR_DRIFT = 0.05                # E4 keep 新锚相对原锚漂移上限（±5%）
DEFAULT_WS_ROOT = '/home/catmouse/Github_Project/daily-stock-workspace'


def next_session_close(now=None) -> datetime:
    """E5 TTL 真源：挂单时刻后第一个交易节的收盘时刻（纯函数）。

    交易节=上午 09:30-11:30（收盘 11:30）/下午 13:00-15:00（收盘 15:00）。
    严格"之后"：恰在 11:30 整挂 → 下一节 15:00；15:00 后/休市日 → 下一交易日 11:30。
    周五晚挂 → 下周一 11:30；节假日（国庆等）逐日跳过。
    日历真源=earliest_fill.is_trading_day（工作区 trading_calendar 单一真源，
    缺位走其周末+官方休市段 fallback）。400 天上限防日历坏数据死循环。
    """
    from paper_trading_v2.earliest_fill import is_trading_day
    dt = now or datetime.now()
    day = dt.date()
    for _ in range(400):
        if is_trading_day(day):
            for h, m in SESSION_CLOSES:
                close = datetime(day.year, day.month, day.day, h, m)
                if close > dt:
                    return close
        day += timedelta(days=1)
        dt = datetime(day.year, day.month, day.day)   # 跨日后以当日 00:00 为基准
    raise ValueError("400 天内找不到交易节收盘（交易日历数据异常）")


def _parse_iso(ts, field):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        raise ValueError(f"{field} 不是合法 ISO 时刻：{ts!r}")


def _validate_ttl(ttl: str, ttl_dt: datetime) -> str:
    """E5 fail-closed：ttl 必须=挂单时刻后第一个交易节的收盘时刻（place/keep 共用）。"""
    expected = next_session_close(datetime.now())
    if ttl_dt != expected:
        raise ValueError(
            f"order_ttl {ttl} 非法：必须是挂单时刻后第一个交易节的收盘时刻 "
            f"{expected.isoformat(timespec='seconds')}（11:30/15:00 取先到，节假日跳过）"
            f"——fail-closed 拒绝，用 next_session_close 推导后再挂")
    return expected.isoformat(timespec='seconds')


class SleeveOrder:
    def __init__(self, db_path=None, tasks_db=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path
        self.tasks_db = tasks_db           # E2 测试注入点；缺省走 _tasks_db() 定位

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    # ---------- E2：tasks.db 定位与同事务事件发射 ----------

    def _tasks_db(self) -> str:
        """任务库定位（E2）：显式注入 > STOCK_TASKS_DB env > 工作区默认
        （<root>/data/tasks/tasks.db，与 watch_scan TASKS_DB 同一布局）。"""
        if self.tasks_db:
            return str(self.tasks_db)
        env = os.environ.get('STOCK_TASKS_DB')
        if env:
            return env
        root = os.environ.get('STOCK_ANALYSIS_WORKSPACE_ROOT', DEFAULT_WS_ROOT)
        return os.path.join(root, 'data', 'tasks', 'tasks.db')

    def _attach_tasks_db(self, conn):
        """ATTACH tasks.db（须在事务外）+ 确保任务表存在（与 taskbus SCHEMA 同构）。"""
        db = self._tasks_db()
        parent = os.path.dirname(db)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn.execute("ATTACH DATABASE ? AS taskbus", (db,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS taskbus.task_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 3, "
            "source TEXT, entity TEXT, payload TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), "
            "claimed_at TEXT, done_at TEXT, note TEXT)")

    def _insert_rejudge_event(self, conn, slot, reason, now):
        """E2：同事务 INSERT MSG_REJUDGE（预算冻结坑的唯一唤醒通道——只转态不发事件
        =坑永久冻结，E2 审计原文）。payload 带消费方（news-watch）重判全上下文。"""
        row = conn.execute(
            "SELECT p.code FROM event_slot_members m "
            "LEFT JOIN position p ON p.stock=m.stock AND p.status='open' "
            "AND p.strategy='NEWS' WHERE m.event_key=? AND p.code IS NOT NULL "
            "ORDER BY m.joined_at LIMIT 1", (slot['event_key'],)).fetchone()
        payload = {
            "event_key": slot['event_key'],
            "code": row[0] if row else None,
            "reason": reason,
            "expired_at": now,
            "order_id": slot['order_id'],
            "budget_held": slot['budget'],
            "note": f"挂单弃单（{reason}）预算冻结待重判：值得→sleeve-order-rejudge "
                    f"--keep 刷带重挂；不值得→--close 关槽回款",
        }
        conn.execute(
            "INSERT INTO taskbus.task_events (type, entity, source, priority, payload) "
            "VALUES ('MSG_REJUDGE', ?, 'sleeve-order', 1, ?)",
            (slot['event_key'], json.dumps(payload, ensure_ascii=False)))

    # ---------- E3：行情快照与防线 ----------

    def _fetch_quote(self, code):
        """行情快照（E3/E9/E11 数据源：high/low/昨收/时间戳/停牌量）。
        取不到 → None（调用方 fail-closed）。复用 fill 既有取价路径（tencent 快照）。"""
        if not code:
            return None
        try:
            from paper_trading_v2.price_fetcher import StockPriceFetcher
            return StockPriceFetcher().get_realtime_price(code)
        except Exception:
            return None

    @staticmethod
    def _quote_block_reason(quote, now=None):
        """E3 行情防线：返回拒因文本（None=放行）。
        快照缺失 / 无有效现价 / 停牌标记（volume=0） / 一字板（high==low≠昨收）/
        报价陈旧（非今日或距今 >5 分钟，时间戳不可解析同拒）——宁可不成交不脏成交。"""
        now = now or datetime.now()
        if quote is None:
            return "取不到行情快照（fail-closed，宁可不成交不脏成交）"
        try:
            px = float(quote.current_price) if quote.current_price is not None else None
        except (TypeError, ValueError):
            px = None
        if px is None or px <= 0:
            return f"行情快照无有效现价（{quote.current_price!r}，停牌/脏数据）"
        vol = str(quote.volume or '').strip()
        try:
            suspended = (not vol) or float(vol) == 0
        except ValueError:
            suspended = False
        if suspended:
            return f"停牌标记（volume={vol or '空'}），拒绝成交"
        try:
            hi = float(quote.high) if quote.high is not None else 0.0
            lo = float(quote.low) if quote.low is not None else 0.0
            pc = float(quote.pre_close) if quote.pre_close is not None else 0.0
        except (TypeError, ValueError):
            return "行情快照 high/low/昨收字段非法，无法核验，拒绝成交"
        if hi > 0 and lo > 0 and hi == lo and pc > 0 and hi != pc:
            return f"一字板（当日 high==low={hi} ≠昨收 {pc}）——挂死买不到，拒绝按检测价成交"
        d = str(quote.date or '')[:10]
        if d != now.strftime('%Y-%m-%d'):
            return f"报价非今日（date={d or '空'}），拒绝脏价成交"
        t = str(quote.time or '').strip()
        try:
            parts = [int(x) for x in t.split(':')][:3]
            while len(parts) < 3:
                parts.append(0)
            qt = now.replace(hour=parts[0], minute=parts[1], second=parts[2],
                             microsecond=0)
        except (ValueError, IndexError):
            return f"报价时间戳缺失/不可解析（time={t!r}），无法证明新鲜，拒绝成交"
        if (now - qt).total_seconds() > TTL_STALE_SECONDS:
            return (f"报价陈旧（time={t} 距今>{TTL_STALE_SECONDS // 60} 分钟），"
                    f"拒绝脏价成交")
        return None

    # ---------- sleeve-order-place ----------

    def place(self, event_key, anchor, ttl, source='agent', reason=''):
        """挂单：band=[BAND_LO,BAND_HI]×anchor，槽 open → pending_order。

        anchor=事件入库（newsdb created_at）时刻价格快照（watch_scan 检出时读价
        写 payload，契约修订 1——不是挂单时刻价）；ttl=挂单时刻后第一个交易节收盘
        （E5 fail-closed 校验：必须=next_session_close(now)，否则拒绝）。
        只从 open（sleeve-open 刚建、未挂单）态挂——重复挂单/弃单待重判态出局
        （重判重挂走 rejudge --keep，带刷新语义在那里）。资金零挪动。
        """
        if anchor is None or float(anchor) <= 0:
            raise ValueError(f"anchor 必须是正价格，收到 {anchor!r}")
        anchor = float(anchor)
        ttl_dt = _parse_iso(ttl, 'order_ttl')
        ttl = _validate_ttl(ttl, ttl_dt)          # E5：TTL 真源校验（fail-closed）
        conn = self._conn()
        now = now_iso()
        try:
            slot = get_slot(conn, event_key)
            if not slot:
                raise ValueError(f"事件槽 {event_key} 不存在——先 sleeve-open 开槽再挂单")
            if slot['status'] in ORDER_STATES or slot['order_id']:
                raise ValueError(
                    f"槽 {event_key} 已挂单/待重判（status={slot['status']}, "
                    f"order_id={slot['order_id']}）——重复挂单拒绝；"
                    f"弃单后重挂走 sleeve-order-rejudge --keep")
            if slot['status'] != 'open':
                raise ValueError(f"槽 {event_key} 状态 {slot['status']} 不可挂单"
                                 f"（仅 open：sleeve-open 后首挂）")
            order_id = f"order:{event_key}:{int(time.time())}"
            band_min = round(anchor * BAND_LO, 4)
            band_max = round(anchor * BAND_HI, 4)
            with conn:
                # R2/A1 口径：状态守卫条件 UPDATE——并发双 place 抢不到行即出局
                cur = conn.execute(
                    "UPDATE event_slots SET status='pending_order', band_min=?, "
                    "band_max=?, anchor_price=?, order_ttl=?, order_id=?, "
                    "note=COALESCE(note,'')||? "
                    "WHERE event_key=? AND status='open' AND COALESCE(order_id,'')=''",
                    (band_min, band_max, anchor, ttl, order_id,
                     f" [挂单@{anchor} 带[{band_min},{band_max}] ttl={ttl}]", event_key))
                if cur.rowcount == 0:
                    raise ValueError(f"挂单未获认领（并发已挂/状态已变），{event_key} 未改动")
                shadow_write(conn, 'news_order_place', event_key,
                             {"anchor": anchor, "band": [band_min, band_max],
                              "order_ttl": ttl, "order_id": order_id,
                              "reason": reason, "source": source, "ts": now})
            return {"event_key": event_key, "order_id": order_id, "anchor": anchor,
                    "band_min": band_min, "band_max": band_max, "order_ttl": ttl,
                    "status": "pending_order"}
        finally:
            conn.close()

    # ---------- sleeve-order-fill ----------

    def fill(self, event_key, detected_price, atr=None, skip_conditions=False,
             source='agent'):
        """检测价触带成交（心跳拍上现价进带即成交，不追历史价——裁决 2）。

        fail-closed 链（任一命中即拒绝，不建段、槽不动）：
        1. 槽必须 pending_order 态（未挂单/已弃单/已成交槽不可检测价成交）；
        2. band 列必须在位（NULL=非挂单槽，不可比带）；
        3. detected_price 脏价（None/≤0/非数）拒；
        4. band_min ≤ price ≤ band_max（含边界）——带外拒 + shadow fill_blocked；
        5. order_ttl 已过拒（过期单只能 expire 弃单，不得延后成交）；
        6. E3 行情防线（逐成员）：快照缺失/无有效现价/停牌标记/一字板（high==low
           ≠昨收）/报价陈旧（非今日或 >5 分钟）→ 拒——宁可不成交不脏成交。
        E11 分价：多成员槽按成员各自 code 取各自现价成交（各自带内判定），不得
        单价注入全成员；某成员行情不合格/带外 → 该成员跳过下拍重试（与 fill_pending
        部分成交同语义，实现最简），全成员不合格 → 整单拒。
        单成员路径不变：成交价=检测价（裁决 2），行情快照仅作防线。
        成交复用 SleeveOpener.fill_pending（成员段 trades/operations 直写 + 三件套
        保护 + 恒等式不变）。全成员成交 → 槽转 open；部分跳过 → 槽保持
        pending_order 下拍重试（fill_pending 自动还原 fill_status）。
        """
        conn = self._conn()
        now = now_iso()
        now_dt = datetime.now()
        try:
            slot = get_slot(conn, event_key)
            if not slot:
                raise ValueError(f"事件槽 {event_key} 不存在")
            if slot['status'] != 'pending_order':
                raise ValueError(f"槽 {event_key} 状态 {slot['status']} 非 pending_order，"
                                 f"检测价成交拒绝（fail-closed）")
            b_min, b_max = slot['band_min'], slot['band_max']
            if b_min is None or b_max is None:
                raise ValueError(f"槽 {event_key} 无成交带（band_min/max=NULL）——"
                                 f"非挂单槽不可检测价成交（fail-closed）")
            try:
                price = float(detected_price)
            except (TypeError, ValueError):
                raise ValueError(f"检测价非法：{detected_price!r}（fail-closed 拒）")
            if price <= 0:
                raise ValueError(f"检测价脏价 ≤0：{price}（fail-closed 拒）")
            if not (b_min <= price <= b_max):
                shadow_write(conn, 'fill_blocked', event_key,
                             {"stock_scope": "slot", "detected": price,
                              "band": [b_min, b_max], "order_id": slot['order_id'],
                              "reason": f"检测价 {price} 不在带 [{b_min},{b_max}] 内，"
                                        f"拒绝成交（fail-closed，不建段）", "ts": now})
                conn.commit()
                raise ValueError(f"检测价 {price} 不在成交带 [{b_min}, {b_max}] 内——"
                                 f"拒绝成交，不建段（带外应走 expire --reason band_break）")
            if slot['order_ttl'] and _parse_iso(slot['order_ttl'], 'order_ttl') <= \
                    now_dt:
                raise ValueError(f"挂单 {slot['order_id']} TTL 已过（order_ttl="
                                 f"{slot['order_ttl']}）——拒绝成交，先 expire 弃单")
            members = conn.execute(
                "SELECT m.stock, p.code AS code FROM event_slot_members m "
                "LEFT JOIN position p ON p.stock=m.stock AND p.status='open' "
                "AND p.strategy='NEWS' WHERE m.event_key=? "
                "ORDER BY m.joined_at, m.stock", (event_key,)).fetchall()
            if not members:
                raise ValueError(f"槽 {event_key} 无成员——成交无从建段")
            # E3/E11：逐成员行情防线 + 各自检测价（单成员=检测价，多成员=各自现价）
            multi = len(members) > 1
            prices, quote_skips = {}, []
            for m in members:
                stock, code = m['stock'], m['code']
                q = self._fetch_quote(code)
                block = self._quote_block_reason(q, now_dt)
                if block is None and multi:
                    px = float(q.current_price)
                    if not (b_min <= px <= b_max):
                        block = (f"成员 {stock} 现价 {px} 不在带 [{b_min},{b_max}] 内"
                                 f"（E11 各自带内判定）")
                    else:
                        prices[stock] = px
                elif block is None:
                    prices[stock] = price     # 单成员：检测价成交（裁决 2，路径不变）
                if block is not None:
                    shadow_write(conn, 'fill_blocked', event_key,
                                 {"stock": stock, "code": code, "order_id": slot['order_id'],
                                  "reason": f"E3/E11 行情防线拒成交：{block}", "ts": now})
                    quote_skips.append((stock, block))
            conn.commit()
            if not prices:
                raise ValueError(
                    f"槽 {event_key} 全成员行情不合格/带外——拒绝成交不建段"
                    f"（fail-closed）：{quote_skips}")
        finally:
            conn.close()
        # 复用既有成交逻辑：单成员=检测价注入（裁决 2）；多成员=各成员自己的现价（E11）
        res = SleeveOpener(self.db_path).fill_pending(
            event_key=event_key, open_prices=prices,
            atr=atr, skip_conditions=skip_conditions,
            statuses=('pending_order',), only_unordered=False)
        filled, skipped = [], []
        if res:
            filled, skipped = res[0]['filled'], res[0]['skipped']
        skipped = list(skipped) + quote_skips
        if filled and not skipped:
            conn = self._conn()
            with conn:
                cur = conn.execute(
                    "UPDATE event_slots SET status='open', note=COALESCE(note,'')||? "
                    "WHERE event_key=? AND status='pending_order'",
                    (f" [检测价成交@{price}]", event_key))
                if cur.rowcount:
                    shadow_write(conn, 'news_order_fill', event_key,
                                 {"fill_price": price, "fill_prices": prices,
                                  "order_id": slot['order_id'],
                                  "band": [b_min, b_max], "filled": filled,
                                  "source": source, "ts": now})
            conn.close()
        all_filled = filled and not skipped
        return {"event_key": event_key,
                "fill_price": price if all_filled else None,   # 单成员语义（兼容）
                "fill_prices": dict(prices) if all_filled else None,  # E11 分价明细
                "filled": filled, "skipped": skipped,
                "status": "open" if all_filled else "pending_order",
                "rejected": bool(skipped)}

    # ---------- sleeve-order-expire ----------

    def expire(self, event_key, reason='expired', source='agent'):
        """弃单：pending_order → pending_rejudge。预算/段现金/坑全保留（不关槽不回款，
        契约洞2/3：槽生命周期与挂单 TTL 分离，关槽只发生在"重判不值得"）。

        reason=expired：TTL 到期——fail-closed 校验 order_ttl 确已过期（未到期不许
        用 expired 名义弃单）；
        reason=band_break：现价破带即时弃单（不待 TTL）——E9 必须核价（复用 fill 同款
        取价）：现价 ≥ band_min → 拒（未破带不可弃单，只能 reason=expired）；
        现价确 < band_min 才允许；取不到价 fail-closed 拒。
        E2：转 pending_rejudge 成功的同事务里向 tasks.db INSERT MSG_REJUDGE
        （ATTACH 同连接同事务；只转态不发事件=预算坑永久冻结，E2 审计原文）。
        tasks.db 不可定位/写入失败 → 本事务回滚，弃单不落地（fail-closed，下拍重试）。
        """
        if reason not in EXPIRE_REASONS:
            raise ValueError(f"reason 必须在 {EXPIRE_REASONS} 内，收到 {reason!r}")
        conn = self._conn()
        now = now_iso()
        try:
            slot = get_slot(conn, event_key)
            if not slot:
                raise ValueError(f"事件槽 {event_key} 不存在")
            if slot['status'] != 'pending_order':
                raise ValueError(f"槽 {event_key} 状态 {slot['status']} 非 pending_order，"
                                 f"不可弃单")
            if reason == 'expired':
                if not slot['order_ttl']:
                    raise ValueError(f"槽 {event_key} 无 order_ttl，无法按到期弃单"
                                     f"（破带用 --reason band_break）")
                ttl_dt = _parse_iso(slot['order_ttl'], 'order_ttl')
                if ttl_dt > datetime.now():
                    raise ValueError(f"挂单未到期（order_ttl={slot['order_ttl']} > now）——"
                                     f"expired 弃单被拒（fail-closed），到期前不得弃单")
            else:
                # E9：band_break 必须带现价证据——价 < band_min 才有效
                q = self._fetch_quote(self._first_member_code(conn, event_key))
                if q is None or q.current_price is None:
                    raise ValueError(
                        f"band_break 取不到现价（fail-closed）——未破带不可弃单，"
                        f"槽 {event_key} 保持挂单")
                px = float(q.current_price)
                if slot['band_min'] is None:
                    raise ValueError(f"槽 {event_key} 无 band_min，破带无法核价")
                if px >= slot['band_min']:
                    raise ValueError(
                        f"band_break 核价被拒：现价 {px} ≥ band_min {slot['band_min']}"
                        f"（未破带不可弃单，只能 --reason expired 到期弃单）")
            self._attach_tasks_db(conn)     # E2：事务外 ATTACH（ATTACH 不可入事务）
            try:
                with conn:
                    cur = conn.execute(
                        "UPDATE event_slots SET status='pending_rejudge', "
                        "note=COALESCE(note,'')||? "
                        "WHERE event_key=? AND status='pending_order'",
                        (f" [弃单:{reason}@{now}]", event_key))
                    if cur.rowcount == 0:
                        raise ValueError(f"弃单未获认领（并发已处理），{event_key} 未改动")
                    shadow_write(conn, 'news_order_expire', event_key,
                                 {"reason": reason, "order_id": slot['order_id'],
                                  "budget_held": slot['budget'],
                                  "source": source, "ts": now})
                    self._insert_rejudge_event(conn, slot, reason, now)   # E2 同事务
            finally:
                conn.execute("DETACH DATABASE taskbus")
            return {"event_key": event_key, "status": "pending_rejudge",
                    "reason": reason, "budget_held": slot['budget'],
                    "rejudge_event": "emitted"}
        finally:
            conn.close()

    def _first_member_code(self, conn, event_key):
        """首成员代码（position NEWS 段回填；E9 核价 / E2 payload 共用）。"""
        row = conn.execute(
            "SELECT p.code FROM event_slot_members m "
            "LEFT JOIN position p ON p.stock=m.stock AND p.status='open' "
            "AND p.strategy='NEWS' WHERE m.event_key=? AND p.code IS NOT NULL "
            "ORDER BY m.joined_at LIMIT 1", (event_key,)).fetchone()
        return row[0] if row else None

    def expire_due(self, now=None, source='cron'):
        """心跳批量：全部 order_ttl 已过的 pending_order 槽 → expire(expired)。
        未到期槽不动。返回弃单 event_key 列表。"""
        now_dt = now or datetime.now()
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT event_key, order_ttl FROM event_slots "
                "WHERE status='pending_order' AND order_ttl IS NOT NULL").fetchall()
        finally:
            conn.close()
        done = []
        for r in rows:
            try:
                if _parse_iso(r['order_ttl'], 'order_ttl') <= now_dt:
                    self.expire(r['event_key'], reason='expired', source=source)
                    done.append(r['event_key'])
            except ValueError:
                continue        # 并发已处理/脏 ttl：跳过，下拍再判
        return done

    # ---------- sleeve-order-rejudge ----------

    def rejudge(self, event_key, keep=False, close=False, anchor=None, ttl=None,
                reason='', source='agent'):
        """重判消费（MSG_REJUDGE）：轻量闸审判定后落到这里执行。

        keep=True：值得——以当时现价为新 anchor 刷新 band 重挂（回 pending_order，
        旧带作废；anchor 缺省触网取现价，取不到 fail-closed 拒），ttl 必传新到期；
        close=True：不值得/事件过期——复用 close-slot 关槽回款（残余段现金回消息池、
        成员段 closed、坑释放）+ NEWS 信号行档案化清理（archive=True：同票无技术组
        open 段时 pool_status='archived'，方案 2.6b"回池要新证据"）。
        只认 pending_rejudge 态——挂单中不可重判（先 expire）。
        """
        if keep == close:
            raise ValueError("rejudge 必须二选一：--keep（刷新重挂）或 --close（关槽回款）")
        conn = self._conn()
        now = now_iso()
        try:
            slot = get_slot(conn, event_key)
            if not slot:
                raise ValueError(f"事件槽 {event_key} 不存在")
            if slot['status'] != 'pending_rejudge':
                raise ValueError(f"槽 {event_key} 状态 {slot['status']} 非 pending_rejudge"
                                 f"——重判只接弃单/破带槽（挂单中先 expire 弃单）")
        finally:
            conn.close()
        if close:
            r = SleeveOpener(self.db_path).close_slot(
                event_key, reason=f"v12 重判不值得：{reason}" if reason
                else 'v12 重判不值得关槽', source=source, archive=True)
            conn = self._conn()
            with conn:
                shadow_write(conn, 'news_order_rejudge_close', event_key,
                             {"refund": r['residual_refund'],
                              "signal_rows_archived": True,
                              "reason": reason, "source": source, "ts": now})
            conn.close()
            return {"event_key": event_key, "action": "close",
                    "status": "closed", "refund": r['residual_refund']}
        # keep：E4 次数帽先于一切 keep 语义——keep 已达 2 次 → 强制 close
        #（防"弃单→重挂→再弃单"无限循环永久冻结预算坑；返回 close 语义 + 超限注记）
        if (slot['rejudge_count'] or 0) >= MAX_REJUDGE_KEEP:
            r = SleeveOpener(self.db_path).close_slot(
                event_key, reason='v12 重判超限（keep 已达 '
                                  f'{MAX_REJUDGE_KEEP} 次）强制关槽', source=source,
                archive=True)
            conn = self._conn()
            with conn:
                shadow_write(conn, 'news_order_rejudge_keep', event_key,
                             {"forced_close": True, "rejudge_count": slot['rejudge_count'],
                              "note": "重判超限强制关槽", "reason": reason,
                              "source": source, "ts": now})
            conn.close()
            return {"event_key": event_key, "action": "close", "forced": True,
                    "status": "closed", "refund": r['residual_refund'],
                    "note": f"重判超限（keep 已达 {MAX_REJUDGE_KEEP} 次）强制关槽回款"}
        # keep：新 anchor=当时现价
        if anchor is None:
            anchor = self._spot_price(slot)
            if anchor is None or anchor <= 0:
                raise ValueError("重判 keep 拿不到当时现价（anchor 未注入且触网失败）——"
                                 "fail-closed 拒绝重挂，槽保持 pending_rejudge")
        anchor = float(anchor)
        # E4 漂移限制：新锚相对原锚漂移 >±5%（新带与原带无交集）→ 拒 keep 只能 close
        #（接刀器防线：跳水后以跳水价重挂 / 拉高后追价重挂都不许）
        orig = slot['anchor_price']
        if orig and orig > 0 and abs(anchor / orig - 1) > MAX_ANCHOR_DRIFT:
            raise ValueError(
                f"重判 keep 新锚 {anchor} 相对原锚 {orig} 漂移 "
                f"{abs(anchor / orig - 1):.1%} > ±{MAX_ANCHOR_DRIFT:.0%}"
                f"（新带与原带无交集）——拒绝重挂，只可 --close 关槽回款")
        # E10/E5：ttl 缺省自动推下一交易节收盘（不靠 agent 自觉）；显式传入则校验
        if not ttl:
            ttl = next_session_close().isoformat(timespec='seconds')
        ttl_dt = _parse_iso(ttl, 'order_ttl')
        ttl = _validate_ttl(ttl, ttl_dt)
        conn = self._conn()
        try:
            order_id = f"order:{event_key}:{int(time.time())}"
            band_min = round(anchor * BAND_LO, 4)
            band_max = round(anchor * BAND_HI, 4)
            with conn:
                cur = conn.execute(
                    "UPDATE event_slots SET status='pending_order', band_min=?, "
                    "band_max=?, anchor_price=?, order_ttl=?, order_id=?, "
                    "rejudge_count=COALESCE(rejudge_count,0)+1, "
                    "note=COALESCE(note,'')||? "
                    "WHERE event_key=? AND status='pending_rejudge'",
                    (band_min, band_max, anchor, ttl, order_id,
                     f" [重判重挂@{anchor} 带[{band_min},{band_max}] ttl={ttl}]",
                     event_key))
                if cur.rowcount == 0:
                    raise ValueError(f"重判重挂未获认领（并发已处理），{event_key} 未改动")
                shadow_write(conn, 'news_order_rejudge_keep', event_key,
                             {"anchor": anchor, "band": [band_min, band_max],
                              "order_ttl": ttl, "order_id": order_id,
                              "rejudge_count": (slot['rejudge_count'] or 0) + 1,
                              "reason": reason, "source": source, "ts": now})
            return {"event_key": event_key, "action": "keep", "status": "pending_order",
                    "order_id": order_id, "anchor": anchor,
                    "band_min": band_min, "band_max": band_max, "order_ttl": ttl,
                    "rejudge_count": (slot['rejudge_count'] or 0) + 1}
        finally:
            conn.close()

    def _spot_price(self, slot):
        """keep 缺省 anchor：首成员实时现价（触网，失败 None=fail-closed 上层拒）。"""
        conn = self._conn()
        try:
            m = conn.execute("SELECT s.code FROM event_slot_members m "
                             "LEFT JOIN position s ON s.stock=m.stock AND s.status='open' "
                             "AND s.strategy='NEWS' WHERE m.event_key=? "
                             "ORDER BY m.joined_at LIMIT 1",
                             (slot['event_key'],)).fetchone()
        finally:
            conn.close()
        code = m['code'] if m else None
        if not code:
            return None
        try:
            from paper_trading_v2.price_fetcher import StockPriceFetcher
            info = StockPriceFetcher().get_realtime_price(code)
            return info.current_price if info else None
        except Exception:
            return None
