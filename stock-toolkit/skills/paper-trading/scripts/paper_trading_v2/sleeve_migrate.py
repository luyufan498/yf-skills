"""SleeveMigrator — 移交桥：消息组 L1 → 技术组 L1 单向迁移（sleeve-m1.5 段转策略，方案 2.3 v4.2）

【动作语义 v4.2 ＝段转策略（M1 五路审计证伪"跨账户平移"形态后重写：A3 卖不断链 / Y1 资金链 /
Y8 治理绕行，三审计员独立命中同一根柱子）】
成员持仓段原地 strategy 'NEWS'→'L1'：段行 id 不动，成本基准/FIFO/realized 天然连续，
不重买、不追价、筹码不变——**禁止插入任何无现金流的"迁移成本" operation 行**
（positions 保持纯 FIFO 现金流语义；资金对转审计留痕一律走 pool audit / shadow_log）。
单事务配套（方案 2.3）：
  1. 机械资格：grp=news 账户存活持仓 + 活跃槽（open/partial）+ 无论点失效旗标
  2. 段转策略：position 段 strategy='NEWS'→'L1'（id 不动），budget 改写为主池实际承接成本
  3. FIFO 行随迁（留痕）：positions/operations/exright_applied/conditions 的 account_id
     改挂同名 grp=tech 账户（无则建）——保护链三件套按原成本续挂、成本历史跟随持仓，
     news 账户清零成历史壳（FK 保留，活跃寻址经 resolve_account 段锚定排除，防 A3 复辟）
  4. 资金：pool_ledger.free -= C（承接成本）；sleeve_ledger.free += A+C（账户现金+成本对转）；
     tech 账户承接 C；news 账户清零——守恒：-C + (A+C) + (C-T) = 0（T=账户总资金）
  5. 槽：全成员走完 → migrated（topup_locked=1 + orig_budget）；否则 partial；成员行 migrated_at
  6. pool 行 strategy='L1' + event_key 改写（键活槽空，同催化二波按 G3 开新槽）
  7. 影子账 #6 bridge_track 初始行（双轨 8 周宣判起点）
承接后 topup 走正规 master_pool.topup，--source migrate 豁免甜点动量检查（宪法 2.6），
资金帽/段位帽/冷却不豁免（gate + CLI，见 master_pool/gate）。
【铁律】单向、一次、不可回迁；迁移对象仅限当前存活持仓——
未持仓事件票走技术确认后买入=延迟税借尸还魂，永久禁止。
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
        """段转策略迁移：NEWS 段原地转 L1 + 资金/账户/槽/池行配套。返回摘要 dict。"""
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
            seg = conn.execute(
                "SELECT * FROM position WHERE stock=? AND status='open' AND strategy='NEWS' "
                "ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
            if not seg:
                raise ValueError(f"{stock} 无 NEWS open 段（段转策略锚点缺失）")

            avg_cost = cost / qty
            # 主仓账户（无则建 grp='tech'；有则承接——含活跃双组场景，行只追加不覆盖）
            aid_tech = tech_account_id(conn, stock)
            if aid_tech is None:
                cur = conn.execute(
                    "INSERT INTO accounts (stock_name, stock_code, capital_total, "
                    "capital_available, capital_used, fifo_index, fifo_offset, grp, "
                    "created_at, updated_at) VALUES (?,?,0,0,0,-1,0,'tech',?,?)",
                    (stock, code, now, now))
                aid_tech = cur.lastrowid

            with conn:
                # ① 预算承接：pool_ledger.free 条件扣减（rowcount 判定，不足即出局）
                cur = conn.execute(
                    "UPDATE pool_ledger SET free=free-?, updated_at=? WHERE id=1 AND free>=?",
                    (cost, now, cost))
                if cur.rowcount == 0:
                    free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()
                    free = free[0] if free else 0.0
                    raise ValueError(f"总池空闲不足：迁移承接需 ¥{cost:,.0f}，"
                                     f"空闲 ¥{free:,.0f}")

                # ② 段转策略：NEWS 段原地转 L1（id 不动，成本/FIFO 连续），预算=实际承接成本
                conn.execute("UPDATE position SET strategy='L1', budget=?, "
                             "code=COALESCE(?, code) WHERE id=?",
                             (cost, code, seg['id']))

                # ③ FIFO 行随迁（留痕）：只改 account_id/seq/note——不插入任何无现金流行
                self._move_account_rows(conn, aid_sleeve, aid_tech, code,
                                        slot['event_key'])

                # ④ 资金：news 账户清零成历史壳；tech 账户承接占用成本
                sleeve_cash = conn.execute(
                    "SELECT capital_available FROM accounts WHERE id=?",
                    (aid_sleeve,)).fetchone()[0] or 0.0
                refund = sleeve_cash + cost        # 现金（前次卖出所得）+ 承接成本 → 消息池
                conn.execute("UPDATE accounts SET capital_total=0, capital_available=0, "
                             "capital_used=0, updated_at=? WHERE id=?", (now, aid_sleeve))
                conn.execute("UPDATE accounts SET capital_total=capital_total+?, "
                             "capital_used=capital_used+?, updated_at=? WHERE id=?",
                             (cost, cost, now, aid_tech))
                main_free = conn.execute(
                    "SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
                conn.execute(
                    "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                    "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                    (now, 'sleeve_migrate_in', stock, cost, main_free + cost, main_free,
                     f"移交桥段转策略承接 {slot['event_key']}（成本 ¥{cost:,.0f} 计段位）", source))
                sleeve_free = conn.execute(
                    "SELECT free FROM sleeve_ledger WHERE id=1").fetchone()[0]
                conn.execute("UPDATE sleeve_ledger SET free=free+?, updated_at=? WHERE id=1",
                             (refund, now))
                conn.execute(
                    "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                    "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                    (now, 'sleeve_migrate_out', stock, refund, sleeve_free,
                     sleeve_free + refund,
                     f"移交桥段转策略回款（现金 {sleeve_cash:,.0f}+承接成本 {cost:,.0f}）", source))
                # 槽对账：成员 sleeve 侧退出价值 = 回款 − 原段预算（资金一致态下=0）
                member_realized = refund - (seg['budget'] or 0.0)
                conn.execute("UPDATE event_slots SET realized=COALESCE(realized,0)+? "
                             "WHERE event_key=?", (member_realized, slot['event_key']))
                conn.execute(
                    "INSERT INTO watchlog (timestamp, action, stock, strategy_from, strategy_to, "
                    "reason, source, event_key) VALUES (?,?,?,?,?,?,?,?)",
                    (now, 'set_strategy', stock, 'NEWS', 'L1',
                     f"移交桥段转策略（{reason or 'V11'}）——段行 id 不动、成本连续", source,
                     slot['event_key']))

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
                #    ——必须最后写（事务回滚测试以它为崩溃注入点，全表零副作用可验证）
                shadow_write(conn, 'bridge_track', slot['event_key'],
                             {"stock": stock, "qty": qty, "avg_cost": avg_cost,
                              "cost": cost, "mode": "segment_transfer",
                              "news_account_id": aid_sleeve, "tech_account_id": aid_tech,
                              "phase": "migrate",
                              "reason": reason, "source": source, "ts": now})
            return {"stock": stock, "qty": qty, "avg_cost": avg_cost, "cost": cost,
                    "refund_to_sleeve": refund, "member_realized": member_realized,
                    "event_key": slot['event_key'], "slot_status": slot_status,
                    "migrated": others == 0}
        finally:
            conn.close()

    @staticmethod
    def _move_account_rows(conn, from_aid, to_aid, code, event_key):
        """账户行随迁（段转策略留痕）：positions/operations/exright_applied/conditions
        整体改挂 to_aid——不插入任何新行（positions 纯 FIFO 现金流红线）。
        - positions：note 追加段转标记（留痕），seq 重排续接目标账户
        - operations：现金流史跟随持仓（load_operations 与 FIFO/available 复算一致）
        - conditions：保护链三件套按原成本续挂（方案 2.3，sleeve 与主仓参数同构）
        """
        marker = f" [段转{event_key}]"
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM positions "
                            "WHERE account_id=?", (to_aid,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM positions WHERE account_id=? ORDER BY seq",
                            (from_aid,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute(
                "UPDATE positions SET account_id=?, seq=?, stock_code=COALESCE(?, stock_code), "
                "note=COALESCE(note,'')||? WHERE id=?",
                (to_aid, base + i, code, marker, r['id']))
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM operations "
                            "WHERE account_id=?", (to_aid,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM operations WHERE account_id=? ORDER BY seq",
                            (from_aid,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute("UPDATE operations SET account_id=?, seq=?, note=COALESCE(note,'')||? "
                         "WHERE id=?", (to_aid, base + i, marker, r['id']))
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM conditions "
                            "WHERE account_id=?", (to_aid,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM conditions WHERE account_id=? ORDER BY seq, id",
                            (from_aid,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute("UPDATE conditions SET account_id=?, seq=? WHERE id=?",
                         (to_aid, base + i, r['id']))
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM exright_applied "
                            "WHERE account_id=?", (to_aid,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM exright_applied WHERE account_id=? ORDER BY seq",
                            (from_aid,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute("UPDATE exright_applied SET account_id=?, seq=? WHERE id=?",
                         (to_aid, base + i, r['id']))
