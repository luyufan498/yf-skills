"""SleeveMigrator — 移交桥：消息组 L1 → 技术组 L1 单向迁移（sleeve-m1，方案 2.3/3.2）

【动作语义】持仓从 sleeve 账户平移主仓账户：原成本价结转，不重买、不追价、不改变筹码。
实现（单事务，方案 3.7：跨账户转移+双 ledger 对转+槽状态变更必须同库同事务）：
  1. sleeve 成员 FIFO 剩余 (qty, avg_cost) 结算
  2. sleeve 账户：追加"迁移转出"SELL（positions/operations 留痕）→ 账户清零
  3. 主仓账户（grp='tech'）：追加"迁移成本"BUY positions 行（FIFO 基准，保本锁/止损线
     不错价）+ BUY operations 行（资金账）；主池 pool_ledger 扣 cost（预算承接）
  4. 双 ledger 对转：pool_ledger.free -= cost；sleeve_ledger.free += cost + 成员账户现金
  5. event_slots → migrated（topup_locked=1 加仓锁 + orig_budget 原槽预算）；
     多成员槽仍有活持仓时保持 partial、仅成员行打 migrated_at
  6. 主池池行写 event_key（持仓↔事件权威列）+ strategy='L1'
  7. 影子账 #6 bridge_track 初始行（双轨 8 周宣判起点）

【铁律】单向、一次、不可回迁；迁移对象仅限当前存活持仓。
机械前置（资格 V11/否决项判定权在晨审，这里只做机械可判项）：槽 active + 无失效旗标。
"""
from paper_trading_v2.db import get_connection, migrate_db
from paper_trading_v2.sleeve_slots import (
    now_iso, shadow_write, news_account_id, tech_account_id,
    account_remaining, member_slot)


class SleeveMigrator:
    def __init__(self, db_path=None):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    def migrate(self, stock, reason='', source='agent', code=None):
        """迁移 stock 的 sleeve 持仓到主仓账户。返回摘要 dict。"""
        conn = self._conn()
        now = now_iso()
        try:
            aid_sleeve = news_account_id(conn, stock)
            if aid_sleeve is None:
                raise ValueError(f"{stock} 无 grp=news 账户，不是 sleeve 成员")
            qty, cost = account_remaining(conn, aid_sleeve)
            if qty <= 0 or cost <= 0:
                raise ValueError(f"{stock} 无存活持仓（qty={qty}），无可迁移对象——"
                                 f"未持仓事件票走技术确认买入=延迟税借尸还魂，永久禁止")
            slot, member = member_slot(conn, stock)
            if not slot:
                raise ValueError(f"{stock} 无活跃事件槽（open/partial）")
            if slot['status'] not in ('open', 'partial'):
                raise ValueError(f"事件槽 {slot['event_key']} 状态 {slot['status']} 不可迁移")
            if slot['invalidation']:
                raise ValueError(f"事件 {slot['event_key']} 已设论点失效旗标"
                                 f"（{slot['invalidation']}）——资格·消息有效不成立，禁止迁移")

            avg_cost = cost / qty
            # 主仓账户（无则建 grp='tech' 空壳）
            aid_tech = conn.execute("SELECT id FROM accounts WHERE stock_name=? AND grp='tech'",
                                    (stock,)).fetchone()
            if aid_tech:
                aid_tech = aid_tech[0]
            else:
                cur = conn.execute(
                    "INSERT INTO accounts (stock_name, stock_code, capital_total, "
                    "capital_available, capital_used, fifo_index, fifo_offset, grp, "
                    "created_at, updated_at) VALUES (?,?,0,0,0,-1,0,'tech',?,?)",
                    (stock, code, now, now))
                aid_tech = cur.lastrowid

            with conn:
                # ① 主池预算承接：pool_ledger.free -= cost
                main_ledger = conn.execute("SELECT * FROM pool_ledger WHERE id=1").fetchone()
                if not main_ledger:
                    raise ValueError("总池未初始化，无法承接迁移持仓")
                if cost > main_ledger['free']:
                    raise ValueError(f"总池空闲不足：迁移承接需 ¥{cost:,.0f}，"
                                     f"空闲 ¥{main_ledger['free']:,.0f}")
                conn.execute("UPDATE pool_ledger SET free=free-?, updated_at=? WHERE id=1",
                             (cost, now))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, reason, source)"
                             " VALUES (?,?,?,?,?,?)",
                             (now, 'sleeve_migrate_in', stock, cost,
                              f"移交桥承接 {slot['event_key']} 迁移成本", source))

                # ② 主仓：迁移成本 BUY（positions=FIFO 基准 + operations=资金账）
                seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM positions "
                                   "WHERE account_id=?", (aid_tech,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO positions (account_id, seq, operation, stock_code, quantity, "
                    "price, total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid_tech, seq, 'buy', code, qty, avg_cost, cost, now,
                     f"迁移成本:{slot['event_key']}（sleeve→tech 原成本结转）"))
                seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM operations "
                                   "WHERE account_id=?", (aid_tech,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO operations (account_id, seq, type, price, quantity, amount, "
                    "cost, profit, capital, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (aid_tech, seq, 'buy', avg_cost, qty, cost, None, None, None, now,
                     f"迁移成本:{slot['event_key']}（sleeve→tech，FIFO 基准）"))
                tech_row = conn.execute("SELECT capital_total FROM accounts WHERE id=?",
                                        (aid_tech,)).fetchone()
                conn.execute("UPDATE accounts SET capital_total=?, "
                             "capital_available=capital_available+?, updated_at=? WHERE id=?",
                             (cost + (tech_row[0] or 0.0), cost, now, aid_tech))

                # ③ sleeve 账户：迁移转出 SELL 留痕 → 账户现金/预算回消息池
                sleeve_cash = conn.execute("SELECT capital_available FROM accounts WHERE id=?",
                                           (aid_sleeve,)).fetchone()[0] or 0.0
                refund = sleeve_cash + cost        # 现金（前次卖出所得）+ 结转成本
                seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM positions "
                                   "WHERE account_id=?", (aid_sleeve,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO positions (account_id, seq, operation, stock_code, quantity, "
                    "price, total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid_sleeve, seq, 'sell', code, qty, avg_cost, cost, now,
                     f"迁移转出:{slot['event_key']}（sleeve→tech）"))
                seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM operations "
                                   "WHERE account_id=?", (aid_sleeve,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO operations (account_id, seq, type, price, quantity, amount, "
                    "cost, profit, capital, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (aid_sleeve, seq, 'sell', avg_cost, qty, cost, cost, 0.0, None, now,
                     f"迁移转出:{slot['event_key']}"))
                conn.execute("UPDATE sleeve_ledger SET free=free+?, updated_at=? WHERE id=1",
                             (refund, now))
                conn.execute("UPDATE accounts SET capital_total=0, capital_available=0, "
                             "capital_used=0, updated_at=? WHERE id=?", (now, aid_sleeve))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, reason, source)"
                             " VALUES (?,?,?,?,?,?)",
                             (now, 'sleeve_migrate_out', stock, refund,
                              f"移交桥转出（含成本结转 {cost:,.0f}）", source))
                member_budget = conn.execute(
                    "SELECT budget FROM position WHERE stock=? AND status='open' "
                    "AND strategy='NEWS' ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
                member_realized = (sleeve_cash + cost) - (member_budget[0] if member_budget else 0)
                if member_budget:
                    conn.execute("UPDATE position SET status='closed', closed_at=?, "
                                 "close_value=?, realized_pnl=? WHERE stock=? AND status='open' "
                                 "AND strategy='NEWS'",
                                 (now, sleeve_cash + cost, member_realized, stock))

                # ④ 槽状态：全成员走完 → migrated（加仓锁+原槽预算）；否则保持 partial
                others = conn.execute(
                    "SELECT COUNT(*) FROM event_slot_members WHERE event_key=? AND stock!=? "
                    "AND exited_at IS NULL AND migrated_at IS NULL",
                    (slot['event_key'], stock)).fetchone()[0]
                conn.execute("UPDATE event_slot_members SET migrated_at=? WHERE event_key=? "
                             "AND stock=?", (now, slot['event_key'], stock))
                if others == 0:
                    conn.execute(
                        "UPDATE event_slots SET status='migrated', migrated_at=?, "
                        "migrated_stock=?, topup_locked=1, orig_budget=COALESCE(orig_budget,budget) "
                        "WHERE event_key=?", (now, stock, slot['event_key']))
                    slot_status = 'migrated'
                else:
                    conn.execute("UPDATE event_slots SET status='partial' WHERE event_key=?",
                                 (slot['event_key'],))
                    slot_status = 'partial'

                # ⑤ 主池池行：写 event_key + 升 L1（与 allocate 联动同语义）
                pool_row = conn.execute("SELECT * FROM pool WHERE stock=?", (stock,)).fetchone()
                if pool_row:
                    conn.execute("UPDATE pool SET event_key=?, strategy='L1', pool_status='active',"
                                 " code=COALESCE(?, code) WHERE stock=?",
                                 (slot['event_key'], code, stock))
                else:
                    conn.execute(
                        "INSERT INTO pool (stock, code, strategy, pool_status, refresh_cadence, "
                        "entered_at, event_key) VALUES (?,?,'L1','active','daily',?,?)",
                        (stock, code, now, slot['event_key']))

                # ⑥ 影子账 #6 移交桥双轨：初始行（心跳盯市步记逐日序列，M3 开闸）
                shadow_write(conn, 'bridge_track', slot['event_key'],
                             {"stock": stock, "qty": qty, "avg_cost": avg_cost,
                              "cost": cost, "sleeve_account_id": aid_sleeve,
                              "tech_account_id": aid_tech, "phase": "migrate",
                              "reason": reason, "source": source, "ts": now})
            return {"stock": stock, "qty": qty, "avg_cost": avg_cost, "cost": cost,
                    "refund_to_sleeve": refund, "member_realized": member_realized,
                    "event_key": slot['event_key'], "slot_status": slot_status,
                    "migrated": others == 0}
        finally:
            conn.close()
