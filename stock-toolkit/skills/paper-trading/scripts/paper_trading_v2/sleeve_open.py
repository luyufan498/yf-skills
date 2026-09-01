"""SleeveOpener — 消息组 L1 事件持仓：sleeve-open / sleeve-fill / sleeve-cancel / sleeve-close-slot

（sleeve-m1，方案 2.2/2.3/3.2）

sleeve-open 是事务组合（非单命令）：sleeve_ledger 扣款 → 成员账户（各户 grp=news，等权）
→ pending 待成交单（fill_status='pending'）→ event_slots 开槽。
- G3 归并：event_key 槽 active(open/partial) → 并入等权重算不加坑；
  槽 closed/archived/migrated 再遇同催化 → 开新槽（二波=新事件，自动派生 `#bN` 键）
- fail-open：event_key 缺失 → 'auto:<首票>:<日期>' 兜底键 + 影子账#9（键坏宁可占多坑不漏事件）

sleeve-fill（开盘成交分支，心跳 ≥9:30 首扫统一成交，CLI 入口 ptrade2 sleeve-fill）：
pending → 按当日开盘价 fill（positions/operations 直写，不经实时价 buy）+ 挂三件套
（atr.py 常量：ATR_K_COST=2.0 / ATR_K_TRAIL=2.5），同步影子账 #4/#7。
TTL 内未成交（停牌等）→ sleeve-cancel 弃单进影子账 #1，坑释放。

sleeve-close-slot：对账 + 归档（全成员清零后槽 closed 释放；残余资金回消息池）。
"""
import json
import os
from datetime import datetime

from paper_trading_v2.atr import ATR_K_COST, ATR_K_TRAIL
from paper_trading_v2.db import get_connection, migrate_db
from paper_trading_v2.sleeve_slots import (
    SLOT_ACTIVE, active_slot_count, now_iso, shadow_write,
    news_account_id, account_remaining, NEWS_KINDS)

MAX_ACTIVE_SLOTS = 20          # 20 事件坑（与主池 20 段位上限并存互不侵占）


class SleeveOpener:
    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    # ---------- sleeve-open ----------

    def open_slot(self, stocks, budget, event_key=None, news_kind=None, source='agent',
                  reason='', title=None, code_map=None):
        """开槽（可多成员等权）。返回摘要 dict。开新槽全程单事务。"""
        if isinstance(stocks, str):
            stocks = [stocks]
        stocks = [s.strip() for s in stocks if s and s.strip()]
        if not stocks:
            raise ValueError("sleeve-open 需要至少一个成员股票")
        if budget is None or budget <= 0:
            raise ValueError("sleeve-open 需要 --budget（本事件槽预算，从消息池拨付）")
        if news_kind is not None and news_kind not in NEWS_KINDS:
            raise ValueError(f"news_kind 必须在六类词表 {NEWS_KINDS} 内（方案 1.4）")
        code_map = code_map or {}

        conn = self._conn()
        now = now_iso()
        try:
            ledger = conn.execute("SELECT * FROM sleeve_ledger WHERE id=1").fetchone()
            if not ledger:
                raise ValueError("消息池未初始化：ptrade2 sleeve-pool-init --amount <总资金20%>")
            # fail-open：键缺失 → 票名+日期兜底键 + 影子账#9
            key_missing = not event_key
            if key_missing:
                event_key = f"auto:{stocks[0]}:{datetime.now():%Y-%m-%d}"

            existing = conn.execute("SELECT * FROM event_slots WHERE event_key=?",
                                    (event_key,)).fetchone()
            derived_wave = None
            if existing and existing['status'] in SLOT_ACTIVE:
                mode = 'merge'
            else:
                mode = 'open'
                if existing:
                    # 二波 = 新事件：派生 #bN 键开新槽（永不并回旧键，方案 2.2 G3）
                    n = 2
                    while conn.execute("SELECT 1 FROM event_slots WHERE event_key=?",
                                       (f"{event_key}#b{n}",)).fetchone():
                        n += 1
                    derived_wave = f"{event_key}#b{n}"
                    event_key = derived_wave

            # 同票双组冲突提示（方案 第四.8：两侧都查另一组活跃持仓，晨审人工裁决）
            conflicts = []
            for s in stocks:
                n_tech = conn.execute(
                    "SELECT COUNT(*) FROM position WHERE stock=? AND status='open' "
                    "AND COALESCE(strategy,'')!='NEWS'", (s,)).fetchone()[0]
                if n_tech:
                    conflicts.append(s)
            for s in conflicts:
                print(f"⚠️ {s} 主池另有 open 段（同票双组暴露）——按第四.8 须晨审人工裁决")

            share = None
            with conn:
                if mode == 'merge':
                    members = [r['stock'] for r in conn.execute(
                        "SELECT stock FROM event_slot_members WHERE event_key=?",
                        (event_key,)).fetchall()]
                    new_members = [s for s in stocks if s not in members]
                    all_members = list(dict.fromkeys(members + stocks))
                    n = len(all_members)
                    total_budget = (existing['budget'] or 0.0) + budget
                    share = total_budget / n
                    # 新成员建账户（资金 0 起步，下面补足到等权份额）+ position 段
                    for s in new_members:
                        self._ensure_member_account(conn, s, code_map.get(s), now,
                                                    reset=False, capital=0)
                    # 补足所有成员到新等权份额，实扣 = Σ缺口（等权重算，不加坑）
                    deduct = 0.0
                    for s in all_members:
                        aid = news_account_id(conn, s)
                        cur_total = conn.execute(
                            "SELECT capital_total FROM accounts WHERE id=?", (aid,)
                        ).fetchone()[0] or 0.0
                        if share > cur_total:
                            deduct += share - cur_total
                    if deduct > 0:
                        free = ledger['free']
                        if deduct > free:
                            raise ValueError(f"消息池空闲不足：需 ¥{deduct:,.0f}，空闲 ¥{free:,.0f}")
                        conn.execute("UPDATE sleeve_ledger SET free=free-?, updated_at=? "
                                     "WHERE id=1", (deduct, now))
                    for s in all_members:
                        self._topup_member(conn, s, share, now)
                    for s in new_members:
                        if not conn.execute(
                            "SELECT 1 FROM position WHERE stock=? AND status='open' "
                            "AND strategy='NEWS'", (s,)).fetchone():
                            conn.execute(
                                "INSERT INTO position (stock, code, strategy, status, budget, "
                                "topup_total, opened_at) VALUES (?,?,'NEWS','open',?,?,?)",
                                (s, code_map.get(s), share, share, now))
                        conn.execute(
                            "INSERT OR IGNORE INTO event_slot_members (event_key, stock, weight, "
                            "joined_at) VALUES (?,?,?,?)", (event_key, s, 1.0 / n, now))
                        self._upsert_pool_row(conn, s, code_map.get(s), event_key,
                                              None, now, 'sleeve-open-merge')
                    conn.execute(
                        "UPDATE event_slots SET budget=?, members_json=?, "
                        "news_kind=COALESCE(?, news_kind), title=COALESCE(?, title) "
                        "WHERE event_key=?",
                        (total_budget, json.dumps(all_members, ensure_ascii=False),
                         news_kind, title, event_key))
                    conn.execute("UPDATE event_slot_members SET weight=? WHERE event_key=?",
                                 (1.0 / n, event_key))
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, reason, "
                                 "source) VALUES (?,?,?,?,?,?)",
                                 (now, 'sleeve_open_merge', event_key, deduct,
                                  reason or f"G3 并入成员 {stocks}", source))
                    deducted = deduct
                else:
                    if active_slot_count(conn) >= MAX_ACTIVE_SLOTS:
                        raise ValueError(f"事件坑已满（{active_slot_count(conn)}/"
                                         f"{MAX_ACTIVE_SLOTS}），需先 close-slot 释放")
                    if budget > ledger['free']:
                        raise ValueError(f"消息池空闲不足：需 ¥{budget:,.0f}，"
                                         f"空闲 ¥{ledger['free']:,.0f}")
                    share = budget / len(stocks)
                    note = reason or ''
                    if derived_wave:
                        note = (note + ' ' if note else '') + "[二波新槽，原键已关闭]"
                    # 槽先建（event_slot_members FK 指向它），再落成员
                    conn.execute(
                        "INSERT INTO event_slots (event_key, status, opened_at, budget, "
                        "news_kind, title, members_json, fill_status, note) "
                        "VALUES (?,'open',?,?,?,?,?,'pending',?)",
                        (event_key, now, budget, news_kind, title,
                         json.dumps(stocks, ensure_ascii=False), note))
                    for s in stocks:
                        has_open_pos = conn.execute(
                            "SELECT 1 FROM position WHERE stock=? AND status='open' "
                            "AND strategy='NEWS'", (s,)).fetchone()
                        if has_open_pos:
                            in_active_slot = conn.execute(
                                "SELECT 1 FROM event_slots es JOIN event_slot_members m "
                                "ON m.event_key=es.event_key WHERE m.stock=? AND es.status IN "
                                "('open','partial')", (s,)).fetchone()
                            if in_active_slot:
                                raise ValueError(f"{s} 已在活跃事件槽中（同票多槽冲突），"
                                                 f"先 close-slot/迁移")
                            # 旧槽已关闭但成员段残留（未走 settle）→ 残余现金回消息池后关段重开
                            stale_aid = news_account_id(conn, s)
                            if stale_aid:
                                cash = conn.execute(
                                    "SELECT capital_available FROM accounts WHERE id=?",
                                    (stale_aid,)).fetchone()[0] or 0.0
                                if cash:
                                    conn.execute("UPDATE sleeve_ledger SET free=free+?, "
                                                 "updated_at=? WHERE id=1", (cash, now))
                        self._ensure_member_account(conn, s, code_map.get(s), now, reset=True,
                                                    capital=share)
                        conn.execute(
                            "INSERT INTO position (stock, code, strategy, status, budget, "
                            "topup_total, opened_at) VALUES (?,?,'NEWS','open',?,0,?)",
                            (s, code_map.get(s), share, now))
                        conn.execute(
                            "INSERT OR IGNORE INTO event_slot_members (event_key, stock, weight, "
                            "joined_at) VALUES (?,?,?,?)",
                            (event_key, s, 1.0 / len(stocks), now))
                    conn.execute("UPDATE sleeve_ledger SET free=free-?, updated_at=? WHERE id=1",
                                 (budget, now))
                    for s in stocks:
                        self._upsert_pool_row(conn, s, code_map.get(s), event_key,
                                              news_kind, now, source)
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, reason, "
                                 "source) VALUES (?,?,?,?,?,?)",
                                 (now, 'sleeve_open', event_key, budget,
                                  reason or f"事件槽开立 成员{stocks}", source))
                    deducted = budget
                if key_missing:
                    shadow_write(conn, 'event_key_missing', event_key,
                                 {"stocks": stocks, "reason": reason or 'sleeve-open',
                                  "source": source, "ts": now})
            return {
                "event_key": event_key, "mode": mode,
                "budget": budget if mode == 'open' else None,
                "deducted": deducted, "share": share, "members": stocks,
                "conflicts": conflicts, "derived_wave": derived_wave,
                "key_missing": key_missing,
            }
        finally:
            conn.close()

    # ---- 成员账户 ----

    def _ensure_member_account(self, conn, stock, code, now, reset=True, capital=None):
        """建/重置 grp='news' 成员账户（对齐 allocate 的重入归档语义）。返回 account_id。"""
        cur = conn.execute("SELECT id FROM accounts WHERE stock_name=? AND grp='news'",
                           (stock,)).fetchone()
        if cur:
            aid = cur[0]
            if reset:
                seg_id = conn.execute(
                    "SELECT id FROM position WHERE stock=? AND status='closed' "
                    "ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
                seg_id = seg_id[0] if seg_id else None
                conn.execute(
                    "INSERT INTO operations_archive (account_id, archived_at, segment_id, type, "
                    "price, quantity, amount, cost, profit, capital, timestamp, note, seq) "
                    "SELECT ?, ?, ?, type, price, quantity, amount, cost, profit, capital, "
                    "timestamp, note, seq FROM operations WHERE account_id=?",
                    (aid, now, seg_id, aid))
                conn.execute("DELETE FROM operations WHERE account_id=?", (aid,))
                conn.execute("DELETE FROM positions WHERE account_id=?", (aid,))
                conn.execute("DELETE FROM condition_history WHERE condition_id IN "
                             "(SELECT id FROM conditions WHERE account_id=?)", (aid,))
                conn.execute("DELETE FROM conditions WHERE account_id=?", (aid,))
                conn.execute("DELETE FROM exright_applied WHERE account_id=?", (aid,))
                conn.execute("UPDATE accounts SET stock_code=COALESCE(?, stock_code), "
                             "capital_total=?, capital_available=?, capital_used=0, "
                             "fifo_index=-1, fifo_offset=0, updated_at=? WHERE id=?",
                             (code, capital, capital, now, aid))
                conn.execute("INSERT INTO operations (account_id, seq, type, capital, timestamp, "
                             "note) VALUES (?,0,'init',?,?,'sleeve 成员初始化')",
                             (aid, capital, now))
            return aid
        cur = conn.execute(
            "INSERT INTO accounts (stock_name, stock_code, capital_total, capital_available, "
            "capital_used, fifo_index, fifo_offset, grp, created_at, updated_at) "
            "VALUES (?,?,?,?,0,-1,0,'news',?,?)", (stock, code, capital, capital, now, now))
        aid = cur.lastrowid
        conn.execute("INSERT INTO operations (account_id, seq, type, capital, timestamp, note) "
                     "VALUES (?,0,'init',?,?,'sleeve 成员初始化')", (aid, capital, now))
        return aid

    def _topup_member(self, conn, stock, target_total, now):
        """并入时补足成员账户到新等权份额；position.budget 同步累计。返回实补金额。"""
        aid = news_account_id(conn, stock)
        if aid is None:
            raise ValueError(f"并入成员 {stock} 缺 grp=news 账户（数据异常）")
        cur_total = conn.execute("SELECT capital_total FROM accounts WHERE id=?",
                                 (aid,)).fetchone()[0] or 0.0
        delta = target_total - cur_total
        if delta <= 0:
            return 0.0
        conn.execute("UPDATE accounts SET capital_total=?, capital_available="
                     "capital_available+?, updated_at=? WHERE id=?",
                     (target_total, delta, now, aid))
        seg = conn.execute("SELECT id FROM position WHERE stock=? AND status='open' "
                           "AND strategy='NEWS' ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
        if seg:
            conn.execute("UPDATE position SET budget=budget+?, topup_total=topup_total+? "
                         "WHERE id=?", (delta, delta, seg['id']))
        return delta

    def _upsert_pool_row(self, conn, stock, code, event_key, news_kind, now, source):
        """成员池行：无则建 NEWS 缓冲行；有则只补 code/event_key（同票双组下不动主池档位）。"""
        row = conn.execute("SELECT strategy FROM pool WHERE stock=?", (stock,)).fetchone()
        if row:
            conn.execute("UPDATE pool SET code=COALESCE(?, code), "
                         "event_key=COALESCE(?, event_key) WHERE stock=?",
                         (code, event_key, stock))
        else:
            conn.execute(
                "INSERT INTO pool (stock, code, strategy, pool_status, refresh_cadence, "
                "entered_at, event_key) VALUES (?,?,'NEWS','active','event',?,?)",
                (stock, code, now, event_key))
            conn.execute(
                "INSERT INTO watchlog (timestamp, action, stock, strategy_from, strategy_to, "
                "reason, source, event_key, news_kind) VALUES (?,?,?,?,?,?,?,?,?)",
                (now, 'add', stock, None, 'NEWS', 'sleeve-open 成员入池（消息组缓冲）',
                 source, event_key, news_kind))

    # ---------- sleeve-fill ----------

    def fill_pending(self, event_key=None, open_prices=None, atr=None, skip_conditions=False,
                     prev_close_map=None, high10_map=None, codes=None):
        """pending 待成交单 → 按当日开盘价成交 + 挂三件套（成本保护/移动止损）。

        open_prices: {stock: 开盘价}；缺项触网取实时价的 open_price 字段。
        atr: {stock: ATR}（心跳/测试注入）或标量；缺项触网算（KLine+compute_atr），
        失败则跳过挂保护（裸奔检测会告警，atr-sync 次日补）。
        prev_close_map/high10_map: 影子账 #7（gap>5%）/#4（off10h）数据，缺则跳过该账。
        """
        conn = self._conn()
        now = now_iso()
        summary = []
        try:
            q = "SELECT * FROM event_slots WHERE fill_status='pending' AND status IN (?,?)"
            args = list(SLOT_ACTIVE)
            if event_key:
                q += " AND event_key=?"
                args.append(event_key)
            slots = conn.execute(q, args).fetchall()
            for slot in slots:
                members = conn.execute(
                    "SELECT stock FROM event_slot_members WHERE event_key=? ORDER BY stock",
                    (slot['event_key'],)).fetchall()
                filled, skipped = [], []
                all_filled = True
                for m in members:
                    stock = m['stock']
                    aid = news_account_id(conn, stock)
                    if aid is None:
                        skipped.append((stock, '无 grp=news 账户'))
                        all_filled = False
                        continue
                    qty, _ = account_remaining(conn, aid)
                    if qty > 0:
                        filled.append({"stock": stock, "note": "已有持仓跳过"})
                        continue
                    cash, acct_code = conn.execute(
                        "SELECT capital_available, stock_code FROM accounts WHERE id=?",
                        (aid,)).fetchone()
                    cash = cash or 0.0
                    code = (codes or {}).get(stock) or acct_code
                    price = (open_prices or {}).get(stock)
                    if price is None:
                        price = self._fetch_open_price(code)
                    if not price:
                        skipped.append((stock, '无开盘价（停牌顺延）'))
                        all_filled = False
                        continue
                    qty = int(cash / price)
                    if qty < 1:
                        skipped.append((stock, f'份额 ¥{cash:,.0f} 不足一股@{price}'))
                        all_filled = False
                        continue
                    amount = qty * price
                    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM positions "
                                       "WHERE account_id=?", (aid,)).fetchone()[0]
                    conn.execute(
                        "INSERT INTO positions (account_id, seq, operation, stock_code, quantity,"
                        " price, total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?)",
                        (aid, seq, 'buy', code, qty, price, amount, now,
                         f"sleeve-fill {slot['event_key']}"))
                    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM operations "
                                       "WHERE account_id=?", (aid,)).fetchone()[0]
                    conn.execute(
                        "INSERT INTO operations (account_id, seq, type, price, quantity, amount, "
                        "cost, profit, capital, timestamp, note) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (aid, seq, 'buy', price, qty, amount, None, None, None, now,
                         f"sleeve-fill {slot['event_key']} 开盘成交"))
                    conn.execute("UPDATE accounts SET capital_available=capital_available-?, "
                                 "capital_used=capital_used+?, updated_at=? WHERE id=?",
                                 (amount, amount, now, aid))
                    atr_v = self._resolve_atr(atr, stock, code)
                    if not skip_conditions and atr_v:
                        self._mount_protection(conn, aid, price, atr_v, now)
                    # 影子账 #7 高开观察（成交时记 gap）
                    pre_close = (prev_close_map or {}).get(stock)
                    if pre_close:
                        gap = price / pre_close - 1
                        if gap > 0.05:
                            shadow_write(conn, 'gap_open', slot['event_key'],
                                         {"stock": stock, "open": price,
                                          "pre_close": pre_close, "gap": round(gap, 4),
                                          "ts": now})
                    # 影子账 #4 off10h 观察（T+1 成交时点距 10 日高）
                    h10 = (high10_map or {}).get(stock)
                    if h10:
                        shadow_write(conn, 'off10h', slot['event_key'],
                                     {"stock": stock, "fill_price": price, "high10": h10,
                                      "off10h": round(price / h10 - 1, 4), "ts": now})
                    filled.append({"stock": stock, "qty": qty, "price": price,
                                   "amount": amount})
                if filled:
                    conn.execute("UPDATE event_slots SET fill_status=?, fill_at=? "
                                 "WHERE event_key=?",
                                 ('filled' if all_filled else 'pending', now,
                                  slot['event_key']))
                    if not all_filled:
                        conn.execute(
                            "UPDATE event_slots SET note=COALESCE(note,'')||? WHERE event_key=?",
                            (f" [部分成交 {len(filled)}/{len(members)} 跳过:"
                             f"{[s[0] for s in skipped]}]", slot['event_key']))
                summary.append({"event_key": slot['event_key'], "filled": filled,
                                "skipped": skipped})
            conn.commit()
            return summary
        finally:
            conn.close()

    def _fetch_open_price(self, code):
        if not code:
            return None
        try:
            from paper_trading_v2.price_fetcher import StockPriceFetcher
            info = StockPriceFetcher().get_realtime_price(code)
            return info.open_price if info else None
        except Exception:
            return None

    def _resolve_atr(self, atr, stock, code):
        if isinstance(atr, dict):
            return atr.get(stock)
        if isinstance(atr, (int, float)):
            return float(atr)
        if not code:
            return None
        try:
            from paper_trading_v2.kline_fetcher import KLineDataFetcher
            from paper_trading_v2.atr import compute_atr
            klines = KLineDataFetcher().fetch_kline_data(code, 'day', 30)
            return compute_atr(klines)
        except Exception:
            return None

    def _mount_protection(self, conn, aid, fill_price, atr_v, now):
        """挂三件套（成本保护/移动止损；止盈阶梯由 atr-sync 例行挂载）。
        与 atr-sync 同源常量：ATR_K_COST=2.0 / ATR_K_TRAIL=2.5（atr.py:9-22）。"""
        cost_prot = round(fill_price - ATR_K_COST * atr_v, 2)
        trail = round(fill_price - ATR_K_TRAIL * atr_v, 2)
        for ctype, price, name, peak, auto_link in (
                ('cost_protection', cost_prot, f'sleeve成本保护-{ATR_K_COST}×ATR', None, 1),
                ('trailing_stop', trail, f'sleeve移动止损-{ATR_K_TRAIL}×ATR', fill_price, 0)):
            exists = conn.execute(
                "SELECT id FROM conditions WHERE account_id=? AND type=? AND status='active'",
                (aid, ctype)).fetchone()
            if exists:
                continue
            seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM conditions "
                               "WHERE account_id=?", (aid,)).fetchone()[0]
            conn.execute(
                "INSERT INTO conditions (account_id, cond_key, is_event, type, name, price, "
                "action, category, status, auto_link_cost, peak_price, created_at, modified_at, "
                "seq) VALUES (?,NULL,0,?,?,?,'清仓','hard','active',?,?,?,?,?)",
                (aid, ctype, name, price, auto_link, peak, now, now, seq))

    # ---------- sleeve-cancel（弃单，影子账 #1）----------

    def cancel_pending(self, event_key, reason='', source='agent'):
        """TTL 内未成交弃单：资金回消息池、成员账户清零、槽 archived（坑释放）+ 影子账#1。"""
        conn = self._conn()
        now = now_iso()
        try:
            slot = conn.execute("SELECT * FROM event_slots WHERE event_key=?",
                                (event_key,)).fetchone()
            if not slot:
                raise ValueError(f"事件槽 {event_key} 不存在")
            if slot['fill_status'] != 'pending':
                raise ValueError(f"事件槽 {event_key} 非 pending（fill_status="
                                 f"{slot['fill_status']}），不能弃单")
            with conn:
                refund = 0.0
                for m in conn.execute("SELECT stock FROM event_slot_members WHERE event_key=?",
                                      (event_key,)).fetchall():
                    stock = m['stock']
                    aid = news_account_id(conn, stock)
                    if aid is None:
                        continue
                    qty, _ = account_remaining(conn, aid)
                    if qty > 0:
                        raise ValueError(f"{stock} 已部分成交（{qty} 股），不能整单弃单，"
                                         f"请走正常退出路径")
                    cash = conn.execute("SELECT capital_available FROM accounts WHERE id=?",
                                        (aid,)).fetchone()[0] or 0.0
                    refund += cash
                    conn.execute("UPDATE sleeve_ledger SET free=free+?, updated_at=? WHERE id=1",
                                 (cash, now))
                    conn.execute("UPDATE accounts SET capital_total=0, capital_available=0, "
                                 "capital_used=0, updated_at=? WHERE id=?", (now, aid))
                    conn.execute("UPDATE position SET status='closed', closed_at=?, "
                                 "close_value=0, realized_pnl=0 WHERE stock=? AND status='open' "
                                 "AND strategy='NEWS'", (now, stock))
                conn.execute("UPDATE event_slots SET fill_status='cancelled', status='archived',"
                             " closed_at=?, note=COALESCE(note,'')||? WHERE event_key=?",
                             (now, f" [弃单:{reason}]", event_key))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, reason, source)"
                             " VALUES (?,?,?,?,?,?)",
                             (now, 'sleeve_cancel', event_key, refund,
                              reason or 'TTL 过期弃单', source))
                shadow_write(conn, 'drop_order', event_key,
                             {"reason": reason, "refund": refund, "source": source, "ts": now})
            return {"event_key": event_key, "refund": refund}
        finally:
            conn.close()

    # ---------- sleeve-close-slot ----------

    def close_slot(self, event_key, reason='', source='agent', archive=None):
        """对账 + 归档：全成员清零后槽 closed 释放；残余未清资金回消息池。"""
        if archive is None:
            archive = os.environ.get('SLEEVE_ARCHIVE_ON_CLEAR', '') == '1'
        conn = self._conn()
        now = now_iso()
        try:
            slot = conn.execute("SELECT * FROM event_slots WHERE event_key=?",
                                (event_key,)).fetchone()
            if not slot:
                raise ValueError(f"事件槽 {event_key} 不存在")
            if slot['status'] not in SLOT_ACTIVE:
                raise ValueError(f"事件槽 {event_key} 状态 {slot['status']} 非 open/partial")
            members = conn.execute("SELECT stock FROM event_slot_members WHERE event_key=?",
                                   (event_key,)).fetchall()
            holding = []
            for m in members:
                aid = news_account_id(conn, m['stock'])
                if aid is None:
                    continue
                qty, _ = account_remaining(conn, aid)
                if qty > 0:
                    holding.append({"stock": m['stock'], "qty": qty})
            if holding:
                raise ValueError(f"事件槽 {event_key} 仍有持仓 {holding}——先清仓/迁移再 close-slot")
            with conn:
                residual = 0.0
                for m in members:
                    aid = news_account_id(conn, m['stock'])
                    if aid is None:
                        continue
                    cash = conn.execute("SELECT capital_available FROM accounts WHERE id=?",
                                        (aid,)).fetchone()[0] or 0.0
                    if cash:
                        residual += cash
                        conn.execute("UPDATE sleeve_ledger SET free=free+?, updated_at=? "
                                     "WHERE id=1", (cash, now))
                        conn.execute("UPDATE accounts SET capital_total=0, capital_available=0,"
                                     " capital_used=0, updated_at=? WHERE id=?", (now, aid))
                    conn.execute("UPDATE position SET status='closed', closed_at=?, "
                                 "close_value=COALESCE(close_value,0) WHERE stock=? AND "
                                 "status='open' AND strategy='NEWS'", (now, m['stock']))
                    if archive:
                        tech_open = conn.execute(
                            "SELECT COUNT(*) FROM position WHERE stock=? AND status='open' "
                            "AND COALESCE(strategy,'')!='NEWS'", (m['stock'],)).fetchone()[0]
                        if not tech_open:
                            conn.execute("UPDATE pool SET pool_status='archived', archived_at=? "
                                         "WHERE stock=?", (now, m['stock']))
                conn.execute("UPDATE event_slots SET status='closed', closed_at=?, "
                             "note=COALESCE(note,'')||? WHERE event_key=?",
                             (now, f" [close-slot:{reason}]", event_key))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, reason, source)"
                             " VALUES (?,?,?,?,?,?)",
                             (now, 'sleeve_close_slot', event_key, residual,
                              reason or '槽对账归档', source))
            return {"event_key": event_key, "residual_refund": residual,
                    "budget": slot['budget'], "realized": slot['realized']}
        finally:
            conn.close()
