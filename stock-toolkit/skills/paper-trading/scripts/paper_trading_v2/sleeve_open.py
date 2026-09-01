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
FILL_MAX_DEV_PREV_CLOSE = 0.30  # R7：开盘价偏离昨收 >30% 拒绝成交（脏价防线）
FILL_MIN_PRICE = 0.01          # M1.7/F5：价格下限（A股最小报价单位；<0.01=脏价）


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
        if budget is None or budget < 0:
            raise ValueError("sleeve-open 需要 --budget（本事件槽预算，从消息池拨付；"
                             "G3 纯归并 merge 可传 0=零拨款）")
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
                # R4/B2：含已迁移成员的槽禁 merge——G3 强制开新槽（二波=新事件，永不并回迁移槽）
                has_migrated = conn.execute(
                    "SELECT 1 FROM event_slot_members WHERE event_key=? AND migrated_at "
                    "IS NOT NULL LIMIT 1", (event_key,)).fetchone()
                if has_migrated:
                    mode = 'open'
                    n = 2
                    while conn.execute("SELECT 1 FROM event_slots WHERE event_key=?",
                                       (f"{event_key}#b{n}",)).fetchone():
                        n += 1
                    derived_wave = f"{event_key}#b{n}"
                    event_key = derived_wave
                else:
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
                    pure_merge = (budget == 0)      # R4/B1'：纯归并合法形态——零拨款，
                    #                                 不回收既有成员、新成员 0 元起步
                    # 新成员建账户（资金 0 起步，下面补足到等权份额）+ position 段
                    for s in new_members:
                        self._ensure_member_account(conn, s, code_map.get(s), now,
                                                    reset=False, capital=0)
                        # 段先建（budget=0），由 _topup_member 与账户同步计入——
                        # 保证"段预算恒=账户实际占用（不超分）"在 merge 下同样成立
                        if not conn.execute(
                            "SELECT 1 FROM position WHERE stock=? AND status='open' "
                            "AND strategy='NEWS'", (s,)).fetchone():
                            conn.execute(
                                "INSERT INTO position (stock, code, strategy, status, budget, "
                                "topup_total, opened_at) VALUES (?,?,'NEWS','open',0,0,?)",
                                (s, code_map.get(s), now))
                    # 补足所有成员到新等权份额，实扣 = Σ缺口（等权重算，不加坑）
                    deduct = 0.0
                    if not pure_merge:
                        for s in all_members:
                            aid = news_account_id(conn, s)
                            cur_total = conn.execute(
                                "SELECT budget FROM position WHERE id=?", (aid,)
                            ).fetchone()[0] or 0.0
                            if share > cur_total:
                                deduct += share - cur_total
                        if deduct > 0:
                            # R2/A1：拨款=条件 UPDATE（free>=缺口），rowcount 判定
                            cur = conn.execute(
                                "UPDATE sleeve_ledger SET free=free-?, updated_at=? "
                                "WHERE id=1 AND free>=?", (deduct, now, deduct))
                            if cur.rowcount == 0:
                                free = conn.execute(
                                    "SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
                                raise ValueError(f"消息池空闲不足：需 ¥{deduct:,.0f}，"
                                                 f"空闲 ¥{free:,.0f}")
                        for s in all_members:
                            self._topup_member(conn, s, share, now)
                    for s in new_members:
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
                    if budget <= 0:
                        raise ValueError("sleeve-open 需要 --budget（本事件槽预算，从消息池拨付）")
                    share = budget / len(stocks)
                    note = reason or ''
                    if derived_wave:
                        note = (note + ' ' if note else '') + "[二波新槽，原键已关闭]"
                    # 槽先建（event_slot_members FK 指向它），再落成员。
                    # R2/A2：20 事件坑上限=条件 INSERT（计数与写入同语句原子，
                    # TOCTOU 免疫——并发抢不到坑即出局，不再"查-判-写"）
                    cur = conn.execute(
                        "INSERT INTO event_slots (event_key, status, opened_at, budget, "
                        "news_kind, title, members_json, fill_status, note) "
                        "SELECT ?, 'open', ?, ?, ?, ?, ?, 'pending', ? "
                        "WHERE (SELECT COUNT(*) FROM event_slots WHERE status IN (?,?)) < ?",
                        (event_key, now, budget, news_kind, title,
                         json.dumps(stocks, ensure_ascii=False), note,
                         SLOT_ACTIVE[0], SLOT_ACTIVE[1], MAX_ACTIVE_SLOTS))
                    if cur.rowcount == 0:
                        raise ValueError(f"事件坑已满（{active_slot_count(conn)}/"
                                         f"{MAX_ACTIVE_SLOTS}），需先 close-slot 释放")
                    # R2/A1：拨款=条件 UPDATE（free>=预算），rowcount 判定
                    cur = conn.execute(
                        "UPDATE sleeve_ledger SET free=free-?, updated_at=? "
                        "WHERE id=1 AND free>=?", (budget, now, budget))
                    if cur.rowcount == 0:
                        free = conn.execute("SELECT free FROM sleeve_ledger WHERE id=1"
                                            ).fetchone()[0]
                        raise ValueError(f"消息池空闲不足：需 ¥{budget:,.0f}，空闲 ¥{free:,.0f}")
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
                                    "SELECT cash FROM position WHERE id=?",
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

    # ---- 成员段（v9 段即账户：不再建 accounts 行，成员=NEWS 段 + 段现金）----

    def _ensure_member_account(self, conn, stock, code, now, reset=True, capital=None):
        """建/重置 NEWS 成员段（v9：段即账户，组由 strategy='NEWS' 推导）。返回段 id。

        reset=True（新槽/旧槽已关残留重开）：段现金重置为 capital、FIFO 清零；
        reset=False（并入等权）：段已存在（调用方先建 budget=0 段），不重建不重置。
        旧实现的重入归档（operations_archive/DELETE 流水）在 v9 废除——
        旧段（closed）自带全部 trades/operations 历史，段即账户历史天然分段。
        """
        seg = conn.execute("SELECT id FROM position WHERE stock=? AND status='open' "
                           "AND strategy='NEWS' ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
        if seg:
            aid = seg[0]
            if reset:
                conn.execute("UPDATE position SET cash=?, fifo_index=-1, fifo_offset=0, "
                             "code=COALESCE(?, code) WHERE id=?", (capital or 0.0, code, aid))
                conn.execute("INSERT INTO operations (account_id, seq, type, capital, "
                             "timestamp, note) VALUES (?,0,'init',?,?,'sleeve 成员初始化（段直建）')",
                             (aid, capital, now))
            return aid
        cur = conn.execute(
            "INSERT INTO position (stock, code, strategy, status, budget, topup_total, "
            "opened_at, cash, fifo_index, fifo_offset) VALUES (?,?,'NEWS','open',?,0,?,?,-1,0)",
            (stock, code, capital or 0.0, now, capital or 0.0))
        aid = cur.lastrowid
        conn.execute("INSERT INTO operations (account_id, seq, type, capital, timestamp, note) "
                     "VALUES (?,0,'init',?,?,'sleeve 成员初始化（段直建）')", (aid, capital, now))
        return aid

    def _topup_member(self, conn, stock, target_total, now):
        """并入时补足成员段到新等权份额（段 cash/budget 同步累计）。返回实补金额。"""
        aid = news_account_id(conn, stock)
        if aid is None:
            raise ValueError(f"并入成员 {stock} 缺 NEWS open 段（数据异常）")
        seg = conn.execute("SELECT cash, budget FROM position WHERE id=?", (aid,)).fetchone()
        cur_total = seg['budget'] or 0.0
        cur_cash = seg['cash'] or 0.0
        delta = target_total - cur_total
        if delta <= 0:
            return 0.0
        conn.execute("UPDATE position SET budget=?, cash=?, topup_total=topup_total+? "
                     "WHERE id=?", (target_total, cur_cash + delta, delta, aid))
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
        atr: {stock: ATR}（心跳/测试注入）或标量；缺项触网算（KLine+compute_atr）。
        prev_close_map/high10_map: 影子账 #7（gap>5%）/#4（off10h）数据，兼作 R7 脏价防线
        参照（缺则触网取 pre_close，再缺则放行——无参照不构成立罪证据）。
        R7/H1/C1 防线：价 ≤0 / 偏离昨收>30% / ATR 解析失败 → 拒绝该成员成交
        （shadow_log kind='fill_blocked'，槽保持 pending 下轮重试，禁静默裸奔）。
        R2/A1：槽级认领+成员扣款均为事务内条件 UPDATE + rowcount 判定——
        并发 fill/cancel 同槽时抢不到行即出局，杜绝双买入/双花。
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
                # R2/A1：槽级认领（条件 UPDATE）——并发 fill/cancel 同槽时抢不到=已被处理
                cur = conn.execute(
                    "UPDATE event_slots SET fill_status='filled', fill_at=? "
                    "WHERE event_key=? AND fill_status='pending'", (now, slot['event_key']))
                if cur.rowcount == 0:
                    summary.append({"event_key": slot['event_key'], "filled": [],
                                    "skipped": [("槽级", "已被并发处理（非 pending），出局")]})
                    continue
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
                        "SELECT cash, code FROM position WHERE id=?", (aid,)).fetchone()
                    cash = cash or 0.0
                    code = (codes or {}).get(stock) or acct_code
                    price = (open_prices or {}).get(stock)
                    if price is None:
                        price = self._fetch_open_price(code)
                    if isinstance(price, str):
                        price = float(price)        # 字符串注入 → ValueError 上抛（不落库）
                    if not price or price <= 0:
                        skipped.append((stock, '无开盘价/脏价≤0（停牌顺延）'))
                        all_filled = False
                        continue
                    # M1.7/F5：价格下限——<0.01（A股最小报价单位）=脏价，拒单留痕
                    #（旧防线只挡 ≤0，0.001 会成交 1 亿股：资金守恒但持仓荒谬）
                    if price < FILL_MIN_PRICE:
                        shadow_write(conn, 'fill_blocked', slot['event_key'],
                                     {"stock": stock, "code": code, "open": price,
                                      "reason": f"价格 {price} < 下限 {FILL_MIN_PRICE}（脏价），"
                                                f"拒绝成交", "ts": now})
                        skipped.append((stock, f'脏价 {price}（< {FILL_MIN_PRICE} 下限）'))
                        all_filled = False
                        continue
                    # R7：偏离昨收>30% 拒绝（昨收缺项触网补，仍缺则放行=无参照不立罪）。
                    # M1.7/F5：昨收=0/负=有参照但非法（除权日脏数据）→ 拒单留痕，
                    # 不再 falsy 短路裸奔成交。
                    pre_close = (prev_close_map or {}).get(stock)
                    if pre_close is None:
                        pre_close = self._fetch_pre_close(code)
                    if pre_close is not None and pre_close > 0 \
                            and abs(price / pre_close - 1) > FILL_MAX_DEV_PREV_CLOSE:
                        shadow_write(conn, 'fill_blocked', slot['event_key'],
                                     {"stock": stock, "code": code, "open": price,
                                      "pre_close": pre_close,
                                      "dev": round(price / pre_close - 1, 4),
                                      "reason": f"偏离昨收>{FILL_MAX_DEV_PREV_CLOSE:.0%}，拒绝成交",
                                      "ts": now})
                        skipped.append((stock, f'偏离昨收>30%（{price} vs 昨收 {pre_close}）'))
                        all_filled = False
                        continue
                    if pre_close is not None and pre_close <= 0:
                        # M1.7/F5：昨收=0/负=脏参照（有参照但非法）——偏离防线无法计算，
                        # 拒单留痕（旧 falsy 短路=跳过防线裸奔成交）
                        shadow_write(conn, 'fill_blocked', slot['event_key'],
                                     {"stock": stock, "code": code, "open": price,
                                      "pre_close": pre_close,
                                      "reason": "昨收脏值（≤0），偏离防线无法计算，拒绝成交",
                                      "ts": now})
                        skipped.append((stock, f'昨收脏值 {pre_close}（拒绝成交）'))
                        all_filled = False
                        continue
                    qty = int(cash / price)
                    if qty < 1:
                        skipped.append((stock, f'份额 ¥{cash:,.0f} 不足一股@{price}'))
                        all_filled = False
                        continue
                    amount = qty * price
                    atr_v = self._resolve_atr(atr, stock, code)
                    if not skip_conditions and not atr_v:
                        # R7：ATR 解析失败 → 拒绝成交（禁静默裸奔），槽保持 pending 下轮重试
                        shadow_write(conn, 'fill_blocked', slot['event_key'],
                                     {"stock": stock, "code": code,
                                      "reason": "ATR 解析失败，拒绝裸奔成交", "ts": now})
                        skipped.append((stock, 'ATR 解析失败，拒绝裸奔成交（下轮重试）'))
                        all_filled = False
                        continue
                    # R2/A1：扣款认领=条件 UPDATE（段现金充足才扣），并发同成员抢不到=出局
                    cur = conn.execute(
                        "UPDATE position SET cash=cash-?, updated_at=? "
                        "WHERE id=? AND cash>=?",
                        (amount, now, aid, amount))
                    if cur.rowcount == 0:
                        skipped.append((stock, '扣款未获认领（并发成交让位/资金不足）'))
                        all_filled = False
                        continue
                    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM trades "
                                       "WHERE account_id=?", (aid,)).fetchone()[0]
                    conn.execute(
                        "INSERT INTO trades (account_id, seq, operation, stock_code, quantity,"
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
                    if not skip_conditions:
                        self._mount_protection(conn, aid, price, atr_v, now)
                    else:
                        # M1.7/F5：显式后门（CLI --skip-conditions）成交即留痕——
                        # 0 保护 0 记录=审计黑洞；影子账可回溯"哪笔成交是裸奔回来的"
                        shadow_write(conn, 'skip_conditions', slot['event_key'],
                                     {"stock": stock, "code": code, "price": price,
                                      "qty": qty, "atr": atr_v,
                                      "reason": "显式跳过保护挂载（CLI --skip-conditions 后门）",
                                      "ts": now})
                    # 影子账 #7 高开观察（成交时记 gap）
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
                if not all_filled:
                    # 槽回 pending（下轮补齐/重试）；认领时的 fill_at 一并还原
                    conn.execute("UPDATE event_slots SET fill_status='pending', fill_at=NULL, "
                                 "note=COALESCE(note,'')||? WHERE event_key=?",
                                 (f" [部分成交 {len(filled)}/{len(members)} "
                                  f"跳过:{[s[0] for s in skipped]}]", slot['event_key']))
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

    def _fetch_pre_close(self, code):
        """R7 脏价防线参照：实时价的昨收（prev_close_map 缺项时触网补）。失败→None（放行）。"""
        if not code:
            return None
        try:
            from paper_trading_v2.price_fetcher import StockPriceFetcher
            info = StockPriceFetcher().get_realtime_price(code)
            return info.pre_close if info else None
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
            # U7.1：INSERT 前查活跃同型线，存在则 UPDATE 不 INSERT
            #（凯莱英 8/31 三连挂实证：重复挂低垂 trailing 线先触发→提前误卖；多引擎读线歧义）
            exists = conn.execute(
                "SELECT id, price FROM conditions WHERE account_id=? AND type=? AND "
                "status='active'", (aid, ctype)).fetchone()
            if exists:
                conn.execute("UPDATE conditions SET price=?, peak_price=COALESCE(?, peak_price), "
                             "modified_at=? WHERE id=?",
                             (price, peak, now, exists['id']))
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
        """TTL 内未成交弃单：资金回消息池、成员账户清零、槽 archived（坑释放）+ 影子账#1。

        R2/A1：弃单认领=事务内条件 UPDATE（WHERE fill_status='pending'）+ rowcount 判定——
        并发 cancel/fill 同槽时抢不到行即出局，杜绝双回款（旧"读-判-写"检查在事务外=TOCTOU）。
        认领后若发现成员已部分成交 → 异常回滚，槽还原 pending（先认领后核验仍安全）。
        """
        conn = self._conn()
        now = now_iso()
        try:
            slot = conn.execute("SELECT * FROM event_slots WHERE event_key=?",
                                (event_key,)).fetchone()
            if not slot:
                raise ValueError(f"事件槽 {event_key} 不存在")
            with conn:
                cur = conn.execute(
                    "UPDATE event_slots SET fill_status='cancelled', status='archived', "
                    "closed_at=?, note=COALESCE(note,'')||? "
                    "WHERE event_key=? AND fill_status='pending'",
                    (now, f" [弃单:{reason}]", event_key))
                if cur.rowcount == 0:
                    raise ValueError(f"事件槽 {event_key} 非 pending（fill_status="
                                     f"{slot['fill_status']}），不能弃单")
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
                    cash = conn.execute("SELECT cash FROM position WHERE id=?",
                                        (aid,)).fetchone()[0] or 0.0
                    refund += cash
                    conn.execute("UPDATE sleeve_ledger SET free=free+?, updated_at=? WHERE id=1",
                                 (cash, now))
                    conn.execute("UPDATE position SET cash=0, fifo_index=-1, fifo_offset=0 "
                                 "WHERE id=?", (aid,))
                    conn.execute("UPDATE position SET status='closed', closed_at=?, "
                                 "close_value=0, realized_pnl=0 WHERE stock=? AND status='open' "
                                 "AND strategy='NEWS'", (now, stock))
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
        """对账 + 归档：全成员清零后槽 closed 释放；残余未清资金回消息池。

        M1.7/F3：槽关账=事务内条件认领（`WHERE status IN (open,partial)` + rowcount 判定）——
        并发 close×2 / close×cancel 同槽时抢不到行即出局，杜绝双回款（旧"读-判-写"检查在
        事务外=TOCTOU，认领 UPDATE 无条件=双写）。认领后核验仍有持仓 → 异常回滚，槽还原。
        """
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
            with conn:
                # M1.7/F3：槽认领=条件 UPDATE（状态守卫进事务）——抢不到=已被并发处理
                cur = conn.execute(
                    "UPDATE event_slots SET status='closed', closed_at=?, "
                    "note=COALESCE(note,'')||? WHERE event_key=? AND status IN (?,?)",
                    (now, f" [close-slot:{reason}]", event_key, SLOT_ACTIVE[0], SLOT_ACTIVE[1]))
                if cur.rowcount == 0:
                    raise ValueError(f"事件槽 {event_key} 已被并发处理（状态 {slot['status']}），"
                                     f"不能重复 close-slot")
                holding = []
                for m in members:
                    aid = news_account_id(conn, m['stock'])
                    if aid is None:
                        continue
                    qty, _ = account_remaining(conn, aid)
                    if qty > 0:
                        holding.append({"stock": m['stock'], "qty": qty})
                if holding:
                    raise ValueError(f"事件槽 {event_key} 仍有持仓 {holding}"
                                     f"——先清仓/迁移再 close-slot")
                residual = 0.0
                for m in members:
                    aid = news_account_id(conn, m['stock'])
                    if aid is None:
                        continue
                    cash = conn.execute("SELECT cash FROM position WHERE id=?",
                                        (aid,)).fetchone()[0] or 0.0
                    if cash:
                        residual += cash
                        conn.execute("UPDATE sleeve_ledger SET free=free+?, updated_at=? "
                                     "WHERE id=1", (cash, now))
                        conn.execute("UPDATE position SET cash=0, fifo_index=-1, "
                                     "fifo_offset=0 WHERE id=?", (aid,))
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
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, reason, source)"
                             " VALUES (?,?,?,?,?,?)",
                             (now, 'sleeve_close_slot', event_key, residual,
                              reason or '槽对账归档', source))
            return {"event_key": event_key, "residual_refund": residual,
                    "budget": slot['budget'], "realized": slot['realized']}
        finally:
            conn.close()
