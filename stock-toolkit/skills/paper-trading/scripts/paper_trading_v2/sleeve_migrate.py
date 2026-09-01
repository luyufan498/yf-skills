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
        """段转策略迁移（v9 段即账户）：NEWS 段原地转 L1 + 资金/槽/池行配套。返回摘要 dict。

        v9 两条路径：
        - 纯段转（同票无 tech open 段，常规态）：NEWS 段原地 strategy→'L1'（段行 id 不动，
          成本基准/FIFO/trades/operations/conditions 全部原地连续），段现金清零
          （原现金 A 随 refund 回消息池），budget=主池实际承接成本——零行移动、零新增行。
        - 同票双组（该股另有非 NEWS open 段）：承接进 tech 段——子行（trades/operations/
          conditions/exright_applied）account_id 改挂 tech 段（seq 续接+留痕标记）、
          tech 段 budget += 承接成本，NEWS 段关闭成历史壳（realized_pnl=回款−原预算）。
          （v8 等价形态：v8 靠"news 账户行随迁 tech 账户"实现，段行只有一行；v9 段即账户，
          双段必须并一段，承载段=tech 段。）
        """
        conn = self._conn()
        now = now_iso()
        try:
            aid_sleeve = news_account_id(conn, stock)
            if aid_sleeve is None:
                raise ValueError(f"{stock} 无 NEWS open 段，不是 sleeve 成员")
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
            # M1.7/F6：code 继承——CLI 不传 code 时从 sleeve 段承接，杜绝承接段
            # code=NULL（sell 断链、code_searcher 查不到的老票二次断链）
            if code is None:
                code = seg['code'] or None

            with conn:
                # ⓪ 成员认领（M1.7/F3）：条件 UPDATE + rowcount 判定——同一成员第二次迁移
                #    （并发双跑/重放）在此出局，杜绝双重对转（资格检查在事务外=TOCTOU）。
                #    认领=本事务首个写，其后的对转/改段全部串行化在其后。
                cur = conn.execute(
                    "UPDATE event_slot_members SET migrated_at=? WHERE event_key=? AND stock=? "
                    "AND migrated_at IS NULL", (now, slot['event_key'], stock))
                if cur.rowcount == 0:
                    raise ValueError(f"{stock} 已迁移或正被并发迁移"
                                     f"（槽 {slot['event_key']}），不能重复迁移")

                # ① 预算承接：pool_ledger.free 条件扣减（rowcount 判定，不足即出局）
                cur = conn.execute(
                    "UPDATE pool_ledger SET free=free-?, updated_at=? WHERE id=1 AND free>=?",
                    (cost, now, cost))
                if cur.rowcount == 0:
                    free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()
                    free = free[0] if free else 0.0
                    raise ValueError(f"总池空闲不足：迁移承接需 ¥{cost:,.0f}，"
                                     f"空闲 ¥{free:,.0f}")

                # ② 段转策略：纯段转（原地）/ 同票双组（并入 tech 段）
                aid_tech = tech_account_id(conn, stock)
                sleeve_cash = seg['cash'] or 0.0
                if aid_tech is None:
                    conn.execute("UPDATE position SET strategy='L1', budget=?, cash=0, "
                                 "code=COALESCE(?, code) WHERE id=?",
                                 (cost, code, seg['id']))
                    transfer_mode = 'in_place'
                else:
                    self._move_segment_rows(conn, aid_sleeve, aid_tech, code,
                                            slot['event_key'])
                    conn.execute("UPDATE position SET budget=budget+?, "
                                 "topup_total=topup_total+? WHERE id=?",
                                 (cost, cost, aid_tech))
                    conn.execute(
                        "UPDATE position SET status='closed', closed_at=?, "
                        "realized_pnl=?, note=COALESCE(note,'')||? WHERE id=?",
                        (now, (sleeve_cash + cost) - (seg['budget'] or 0.0),
                         f" [段转并入 tech 段#{aid_tech} {slot['event_key']}]", aid_sleeve))
                    transfer_mode = 'merge_into_tech'

                # ③ 资金对转：回款=段现金（前次卖出所得）+ 承接成本 → 消息池
                refund = sleeve_cash + cost
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
                     f"移交桥段转策略（{reason or 'V11'}）——{transfer_mode}，成本连续", source,
                     slot['event_key']))

                # ④ 槽状态：全成员走完 → migrated（加仓锁+原槽预算）；否则保持 partial
                #（成员 migrated_at 已由 ⓪ 认领语句落位，不再重复写）
                others = conn.execute(
                    "SELECT COUNT(*) FROM event_slot_members WHERE event_key=? AND stock!=? "
                    "AND exited_at IS NULL AND migrated_at IS NULL",
                    (slot['event_key'], stock)).fetchone()[0]
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
                              "transfer_mode": transfer_mode,
                              "news_account_id": aid_sleeve, "tech_account_id": aid_tech,
                              "phase": "migrate",
                              "reason": reason, "source": source, "ts": now})
            return {"stock": stock, "qty": qty, "avg_cost": avg_cost, "cost": cost,
                    "refund_to_sleeve": refund, "member_realized": member_realized,
                    "event_key": slot['event_key'], "slot_status": slot_status,
                    "transfer_mode": transfer_mode, "migrated": others == 0}
        finally:
            conn.close()

    @staticmethod
    def _move_segment_rows(conn, from_seg, to_seg, code, event_key):
        """段间行随迁（v9 同票双组路径；原 _move_account_rows，锚点 accounts→段）：
        trades/operations/exright_applied/conditions 整体改挂 to_seg——不插入任何新行
        （trades 纯 FIFO 现金流红线）。
        - trades：note 追加段转标记（留痕），seq 重排续接目标段
        - operations：现金流史跟随持仓（M1.7/F1：标记行=迁移前历史，已随 sleeve 回款
          结算，get_account 重建公式按标记分段，不再计入承接段现金流）
        - conditions：保护链三件套按原成本续挂（方案 2.3，sleeve 与主仓参数同构）
        """
        from paper_trading_v2.sleeve_slots import SEGMENT_TRANSFER_MARK
        marker = f" [{SEGMENT_TRANSFER_MARK}{event_key}]"
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM trades "
                            "WHERE account_id=?", (to_seg,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM trades WHERE account_id=? ORDER BY seq",
                            (from_seg,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute(
                "UPDATE trades SET account_id=?, seq=?, stock_code=COALESCE(?, stock_code), "
                "note=COALESCE(note,'')||? WHERE id=?",
                (to_seg, base + i, code, marker, r['id']))
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM operations "
                            "WHERE account_id=?", (to_seg,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM operations WHERE account_id=? ORDER BY seq",
                            (from_seg,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute("UPDATE operations SET account_id=?, seq=?, note=COALESCE(note,'')||? "
                         "WHERE id=?", (to_seg, base + i, marker, r['id']))
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM conditions "
                            "WHERE account_id=?", (to_seg,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM conditions WHERE account_id=? ORDER BY seq, id",
                            (from_seg,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute("UPDATE conditions SET account_id=?, seq=? WHERE id=?",
                         (to_seg, base + i, r['id']))
        base = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM exright_applied "
                            "WHERE account_id=?", (to_seg,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM exright_applied WHERE account_id=? ORDER BY seq",
                            (from_seg,)).fetchall()
        for i, r in enumerate(rows):
            conn.execute("UPDATE exright_applied SET account_id=?, seq=? WHERE id=?",
                         (to_seg, base + i, r['id']))
