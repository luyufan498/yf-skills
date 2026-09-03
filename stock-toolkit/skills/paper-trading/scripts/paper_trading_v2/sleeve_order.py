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
import time
from datetime import datetime

from paper_trading_v2.db import get_connection, migrate_db
from paper_trading_v2.sleeve_slots import now_iso, shadow_write, get_slot
from paper_trading_v2.sleeve_open import SleeveOpener

# v12 裁决 1：band = [0.95, 1.05] × anchor——保护极端：开盘拉高买不到/跳水接坑。
BAND_LO = 0.95
BAND_HI = 1.05
ORDER_STATES = ('pending_order', 'pending_rejudge')
EXPIRE_REASONS = ('expired', 'band_break')


def _parse_iso(ts, field):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        raise ValueError(f"{field} 不是合法 ISO 时刻：{ts!r}")


class SleeveOrder:
    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    # ---------- sleeve-order-place ----------

    def place(self, event_key, anchor, ttl, source='agent', reason=''):
        """挂单：band=[BAND_LO,BAND_HI]×anchor，槽 open → pending_order。

        anchor=事件入库（newsdb created_at）时刻价格快照（watch_scan 检出时读价
        写 payload，契约修订 1——不是挂单时刻价）；ttl=挂单时刻后第一个交易节收盘
        （ISO，由心跳/调用方按交易日历算好带入）。
        只从 open（sleeve-open 刚建、未挂单）态挂——重复挂单/弃单待重判态出局
        （重判重挂走 rejudge --keep，带刷新语义在那里）。资金零挪动。
        """
        if anchor is None or float(anchor) <= 0:
            raise ValueError(f"anchor 必须是正价格，收到 {anchor!r}")
        anchor = float(anchor)
        ttl_dt = _parse_iso(ttl, 'order_ttl')
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
        5. order_ttl 已过拒（过期单只能 expire 弃单，不得延后成交）。
        成交复用 SleeveOpener.fill_pending（既有开盘价成交逻辑：成员段 trades/
        operations 直写 + 三件套保护 + 恒等式不变），以检测价注入全成员 open_prices。
        全成员成交 → 槽转 open（回主流持仓状态机，order_id 留审计痕）；
        部分跳过 → 槽保持 pending_order 下拍重试（fill_pending 自动还原 fill_status）。
        """
        conn = self._conn()
        now = now_iso()
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
                    datetime.now():
                raise ValueError(f"挂单 {slot['order_id']} TTL 已过（order_ttl="
                                 f"{slot['order_ttl']}）——拒绝成交，先 expire 弃单")
            members = [r['stock'] for r in conn.execute(
                "SELECT stock FROM event_slot_members WHERE event_key=? ORDER BY stock",
                (event_key,)).fetchall()]
            if not members:
                raise ValueError(f"槽 {event_key} 无成员——成交无从建段")
        finally:
            conn.close()
        # 复用既有成交逻辑：检测价注入为全成员"开盘价"（裁决 2：成交价=心跳检测价）
        res = SleeveOpener(self.db_path).fill_pending(
            event_key=event_key, open_prices={m: price for m in members},
            atr=atr, skip_conditions=skip_conditions,
            statuses=('pending_order',), only_unordered=False)
        filled, skipped = [], []
        if res:
            filled, skipped = res[0]['filled'], res[0]['skipped']
        if filled and not skipped:
            conn = self._conn()
            with conn:
                cur = conn.execute(
                    "UPDATE event_slots SET status='open', note=COALESCE(note,'')||? "
                    "WHERE event_key=? AND status='pending_order'",
                    (f" [检测价成交@{price}]", event_key))
                if cur.rowcount:
                    shadow_write(conn, 'news_order_fill', event_key,
                                 {"fill_price": price, "order_id": slot['order_id'],
                                  "band": [b_min, b_max], "filled": filled,
                                  "source": source, "ts": now})
            conn.close()
        return {"event_key": event_key, "fill_price": price if (filled and not skipped)
                else None, "filled": filled, "skipped": skipped,
                "status": "open" if (filled and not skipped) else "pending_order",
                "rejected": bool(skipped)}

    # ---------- sleeve-order-expire ----------

    def expire(self, event_key, reason='expired', source='agent'):
        """弃单：pending_order → pending_rejudge。预算/段现金/坑全保留（不关槽不回款，
        契约洞2/3：槽生命周期与挂单 TTL 分离，关槽只发生在"重判不值得"）。

        reason=expired：TTL 到期——fail-closed 校验 order_ttl 确已过期（未到期不许
        用 expired 名义弃单）；reason=band_break：现价破带即时弃单（不待 TTL）。
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
            return {"event_key": event_key, "status": "pending_rejudge",
                    "reason": reason, "budget_held": slot['budget']}
        finally:
            conn.close()

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
        """重判消费（NEWS_REJUDGE）：轻量闸审判定后落到这里执行。

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
        # keep：新 anchor=当时现价
        if anchor is None:
            anchor = self._spot_price(slot)
            if anchor is None or anchor <= 0:
                raise ValueError("重判 keep 拿不到当时现价（anchor 未注入且触网失败）——"
                                 "fail-closed 拒绝重挂，槽保持 pending_rejudge")
        if not ttl:
            raise ValueError("重判 keep 必须带新 --ttl（下一节收盘）")
        conn = self._conn()
        try:
            order_id = f"order:{event_key}:{int(time.time())}"
            band_min = round(float(anchor) * BAND_LO, 4)
            band_max = round(float(anchor) * BAND_HI, 4)
            with conn:
                cur = conn.execute(
                    "UPDATE event_slots SET status='pending_order', band_min=?, "
                    "band_max=?, anchor_price=?, order_ttl=?, order_id=?, "
                    "note=COALESCE(note,'')||? "
                    "WHERE event_key=? AND status='pending_rejudge'",
                    (band_min, band_max, float(anchor), ttl, order_id,
                     f" [重判重挂@{anchor} 带[{band_min},{band_max}] ttl={ttl}]",
                     event_key))
                if cur.rowcount == 0:
                    raise ValueError(f"重判重挂未获认领（并发已处理），{event_key} 未改动")
                shadow_write(conn, 'news_order_rejudge_keep', event_key,
                             {"anchor": float(anchor), "band": [band_min, band_max],
                              "order_ttl": ttl, "order_id": order_id,
                              "reason": reason, "source": source, "ts": now})
            return {"event_key": event_key, "action": "keep", "status": "pending_order",
                    "order_id": order_id, "anchor": float(anchor),
                    "band_min": band_min, "band_max": band_max, "order_ttl": ttl}
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
