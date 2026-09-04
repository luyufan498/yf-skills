"""MasterPoolManager — 总池账本（pool_ledger）+ 消息池账本（sleeve_ledger）+ 资金流水（audit）

sleeve-m1 池感知参数化（方案 2.5/3.2/3.7）：
- 所有资金入口/出口/查询都带 pool 参数（'main' 默认 / 'sleeve'），路由双 ledger；
  互不透支 = 两个独立资金操作入口。
- pool='main' 默认路径与改造前行为一致（无 NEWS 数据时 SQL 语义逐字节等价）；
  唯一必要增量为段统计排除 strategy='NEWS' 行（sleeve 成员段），保证双池互不侵占——
  M2 之前库里不存在 NEWS 行，故旧行为不受影响。
- release 增 archive 参数（默认读 env SLEEVE_ARCHIVE_ON_CLEAR，方案 2.6b：
  清仓 → archived 终态不降 L2；flag 关 = 旧"降回 L2"行为原样）。
"""
import os
from datetime import datetime, timedelta
from paper_trading_v2.db import get_connection, migrate_db

LEDGER_TABLES = {'main': 'pool_ledger', 'sleeve': 'sleeve_ledger'}

# v11 池模型常量（方案 2026-09-02 + 9/3 用户裁决：轮换门基于真实值，预留 1/3）
ROTATION_GATE = 2.0 / 3.0   # Σ净持仓成本/total > 2/3（预留 1/3）→ 新段须轮换伴配
                            # （9/3 裁决：承诺率>80% 口径废除——预算睡觉不该挡新入场，
                            #  门必须量真实占用；承诺率降级为展示口径非门）
FLOOR_RATIO = 0.20          # 物理 floor = 20%×total（只在入场 buy 单点，换仓缓冲）
MAX_OPEN_ROTATIONS = 1      # 未平仓轮换义务 ≤1（超出=拒）

# 主池段统计/段位上限的排除口径：sleeve 成员段（strategy='NEWS'）归消息池，不侵占主池
_MAIN_SEG_FILTER = "COALESCE(strategy,'') != 'NEWS'"

# M1.8/R2 总资金注入基准（总行初始注入常量，写死）：生产 pool_ledger id=1 于 2026-08-10
# init 注入 10,000,000（audit 表 id=1 有 init 行为证）。此后一切合法资金路径都不得改变
# 双池 Σtotal——消息池 init 只能从主池划拨（init_pool 配对扣减，M1.8/R1）。
# reconcile 以此抓一切印钱路径（含历史遗留：未配对直写/越权 INSERT）。
W_BASE = 10_000_000.0


class MasterPoolManager:
    """总池账本：total 只被 init 驱动（消息池 init 从主池划拨扣减，M1.8/R1），
    free 被 allocate/topup/release 驱动，审计留痕。

    pool='main'：趋势池（pool_ledger，id=1）。pool='sleeve'：消息池（sleeve_ledger，id=1）。
    """

    def __init__(self, db_path=None, pool='main'):
        if db_path is None:
            from paper_trading_v2.config import get_workspace_config
            db_path = get_workspace_config()['db_path']
        self.db_path = db_path
        self.pool = pool

    def _conn(self):
        conn = get_connection(self.db_path)
        migrate_db(conn)
        return conn

    @staticmethod
    def _ledger(pool) -> str:
        if pool not in LEDGER_TABLES:
            raise ValueError(f"未知资金池 {pool!r}（可选 {tuple(LEDGER_TABLES)}）")
        return LEDGER_TABLES[pool]

    @staticmethod
    def _label(pool) -> str:
        return '总池' if pool == 'main' else '消息池'

    # ---------- init ----------

    def init_pool(self, total: float, source="manual", pool=None):
        pool = pool or self.pool
        table = self._ledger(pool)
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            with conn:
                row = conn.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()
                if row:
                    raise ValueError(f"{self._label(pool)}已初始化 total={row['total']}，"
                                     f"需删除数据库重置")
                if pool == 'sleeve':
                    # M1.8/R1 配对划拨：消息池资金=从主池划拨（用户裁决 800万/200万），
                    # 同一事务内主池条件扣减——只 INSERT sleeve_ledger 不动主池 = 凭空印钱。
                    # 20% 门落实 docstring 承诺（cli --help "=总池 20%" 此前无代码强制）。
                    main_row = conn.execute(
                        "SELECT total, free FROM pool_ledger WHERE id=1").fetchone()
                    if not main_row:
                        raise ValueError("主池未初始化：消息池资金须从主池划拨，请先 init 主池")
                    cap = 0.2 * main_row['total']
                    if total > cap + 0.005:
                        raise ValueError(
                            f"消息池初始化超主池 20% 上限：¥{total:,.0f} > "
                            f"20%×¥{main_row['total']:,.0f}（上限 ¥{cap:,.0f}）")
                    # 主池条件扣减（M1.7/F4 同款相对条件写）：total/free 同步 -X，
                    # rowcount≠1（含并发丢失）即出局回滚——扣减与建账同事务，不许单边落库
                    cur = conn.execute(
                        "UPDATE pool_ledger SET total=total-?, free=free-?, updated_at=? "
                        "WHERE id=1 AND total>=? AND free>=?",
                        (total, total, now, total, total))
                    if cur.rowcount != 1:
                        raise ValueError(f"主池资金不足（配对划拨失败）：需 ¥{total:,.0f}，"
                                         f"主池空闲 ¥{main_row['free']:,.0f}")
                    main_free_after = main_row['free'] - total
                    conn.execute(f"INSERT INTO {table} (id, total, free, updated_at) "
                                 "VALUES (1, ?, ?, ?)", (total, total, now))
                    # audit 两行：主池 -X（free_before/after 真实）+ 消息池 +X，均注明划拨出处
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                                 "free_before, free_after, reason, source) "
                                 "VALUES (?,?,?,?,?,?,?,?)",
                                 (now, 'sleeve_init_transfer', None, -total,
                                  main_row['free'], main_free_after,
                                  "消息池初始化：从主池划拨", source))
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                                 "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                                 (now, 'init', None, total, 0, total,
                                  f"初始化{self._label(pool)}（从主池划拨）", source))
                else:
                    conn.execute(f"INSERT INTO {table} (id, total, free, updated_at) "
                                 "VALUES (1, ?, ?, ?)",
                                 (total, total, now))
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                                 "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                                 (now, 'init', None, total, 0, total,
                                  f"初始化{self._label(pool)}", source))
            return True
        finally:
            conn.close()

    # ---------- show ----------

    def show(self, pool=None) -> dict:
        pool = pool or self.pool
        table = self._ledger(pool)
        conn = self._conn()
        try:
            ledger = conn.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()
            if not ledger:
                return {"error": f"{self._label(pool)}未初始化"}
            if pool == 'main':
                open_count = conn.execute(
                    f"SELECT COUNT(*) c FROM position WHERE status='open' AND {_MAIN_SEG_FILTER}"
                ).fetchone()['c']
                occupied = conn.execute(
                    f"SELECT COALESCE(SUM(budget),0) s FROM position "
                    f"WHERE status='open' AND {_MAIN_SEG_FILTER}").fetchone()['s']
                # v11 水位三口径：承诺率（计划层 Σbudget/total）、真实率（物理层
                # Σ净持仓成本/total）、floor 水位（20%×total，入场 buy 单点门）。
                # realized_pnl 换物理口径（分池恒等式移项，迁移前后同值）：
                # realized = free + Σ段cash + Σ净成本 − total（旧式 free+Σbudget−total
                # 在信封制下把承诺当钱显示=假盈亏）
                seg_cash_sum = conn.execute(
                    f"SELECT COALESCE(SUM(cash),0) FROM position WHERE status='open' "
                    f"AND {_MAIN_SEG_FILTER}").fetchone()[0]
                real_cost = self._real_net_cost(conn)
                realized = (ledger['free'] + seg_cash_sum + real_cost
                            - ledger['total'])
                return {
                    "total": ledger['total'], "free": ledger['free'],
                    "occupied": occupied,
                    "usage_rate": occupied / ledger['total'] if ledger['total'] else 0,
                    "realized_pnl": realized,
                    "open_segments": open_count,
                    "commitment_rate": occupied / ledger['total'] if ledger['total'] else 0,
                    "real_rate": real_cost / ledger['total'] if ledger['total'] else 0,
                    "real_cost": real_cost,
                    "floor": FLOOR_RATIO * ledger['total'],
                    "rotation_gate": ROTATION_GATE,
                }
            # sleeve：槽视角对账（占用=活跃槽预算；realized=Σ槽 realized）
            # v12：SLOT_ACTIVE 含 pending_order/pending_rejudge——挂单/待重判预算
            # 已拨付占用（free+占用=total 恒等式不因新态出现资金黑洞）
            from paper_trading_v2.sleeve_slots import SLOT_ACTIVE as _SLOT_ACT
            _ph = ','.join('?' * len(_SLOT_ACT))
            occupied = conn.execute(
                f"SELECT COALESCE(SUM(budget),0) s FROM event_slots WHERE status IN ({_ph})",
                tuple(_SLOT_ACT)).fetchone()['s']
            realized = conn.execute(
                "SELECT COALESCE(SUM(realized),0) s FROM event_slots").fetchone()['s']
            member_segments = conn.execute(
                "SELECT COUNT(*) c FROM position WHERE status='open' AND strategy='NEWS'"
            ).fetchone()['c']
            active_slots = conn.execute(
                f"SELECT COUNT(*) c FROM event_slots WHERE status IN ({_ph})",
                tuple(_SLOT_ACT)).fetchone()['c']
            pending = conn.execute(
                f"SELECT COUNT(*) c FROM event_slots WHERE fill_status='pending' "
                f"AND status IN ({_ph})", tuple(_SLOT_ACT)).fetchone()['c']
            return {
                "total": ledger['total'], "free": ledger['free'],
                "occupied": occupied,
                "usage_rate": occupied / ledger['total'] if ledger['total'] else 0,
                "realized_pnl": realized,
                "open_segments": member_segments,
                "active_slots": active_slots,
                "pending_slots": pending,
            }
        finally:
            conn.close()

    def _get_free(self, conn, pool=None):
        pool = pool or self.pool
        row = conn.execute(
            f"SELECT free FROM {self._ledger(pool)} WHERE id=1").fetchone()
        return row[0] if row else None

    def _get_strategy(self, conn, stock):
        row = conn.execute("SELECT strategy, code FROM pool WHERE stock=? AND pool_status='active'",
                           (stock,)).fetchone()
        return (row['strategy'], row['code']) if row else ('L2', None)

    # ---------- allocate ----------

    def _v11_commitment(self, conn):
        """承诺率（v11 计划层）：Σ 技术 open 段 budget / total（可>1，诚实稀缺信号）。"""
        total = conn.execute("SELECT total FROM pool_ledger WHERE id=1").fetchone()[0]
        occupied = conn.execute(
            f"SELECT COALESCE(SUM(budget),0) FROM position WHERE status='open' "
            f"AND {_MAIN_SEG_FILTER}").fetchone()[0]
        return (occupied / total if total else 0.0), total

    @staticmethod
    def _real_net_cost(conn):
        """Σ 技术组净持仓成本（物理层，trades FIFO 现算——不依赖段列）。"""
        from paper_trading_v2.sleeve_slots import account_remaining
        cost = 0.0
        for (sid,) in conn.execute(
                f"SELECT id FROM position WHERE status='open' AND {_MAIN_SEG_FILTER}"):
            cost += account_remaining(conn, sid)[1]
        return cost

    @staticmethod
    def _is_v11_native(conn, seg, stock):
        """v11 原生段判据（buy/sell/topup 路径共用，单一真源）：
        段列 source 非空（v11 allocate 建段 / pool_publicize 盖章）或该票已有
        v11 资金流水（pool_grant/pool_return/v11_publicize）。

        ⚠ 刻意排除移交桥承接段与 v11 前存量段：它们按 v9 物理现金模型装配
        （allocate 已搬 cash / 桥段 available 由 [段转随迁] 标记重建式维护），
        grant/return/信封接线会造成双重记账（m17 F1 幻影现金防线）。这些段的
        buy/sell/topup/release 保持旧行为逐字不变，直到 pool_publicize 公开化
        盖章（cash→free + source='v11_publicized'）后转入信封制。"""
        if seg is None or (seg['strategy'] or '') == 'NEWS':
            return False
        cols = seg.keys()
        if 'source' in cols and (seg['source'] or ''):
            return True
        return conn.execute(
            "SELECT 1 FROM audit WHERE stock=? AND action IN "
            "('pool_grant','pool_return','v11_publicize') LIMIT 1",
            (stock,)).fetchone() is not None

    def entry_gate_and_grant(self, stock, required, conn_pool_db=None):
        """v11 买路径单点（entry/topup buy 门 + 自动拨付）。仅 v11 原生技术段生效；
        旧段（source IS NULL 且无 v11 流水）/ NEWS 段 / 无池库 → 直通（旧行为）。

        门矩阵（方案 2026-09-02）：
        - 首仓（qty 0→n，normal）：pool.free − cost < floor(20%×total) → 拒（真实口径）
        - rotation 段：floor 豁免，但未平仓轮换义务 > MAX_OPEN_ROTATIONS → 拒
        - topup（qty>0 再买）：floor 豁免（机动资金本意）
        - source=manual：全豁免（L1 人工特权同款）
        段 cash 不足 → 事务内条件拨付差额（audit 'pool_grant'）；池不足=物理硬拒。
        """
        import re
        from paper_trading_v2.storage import resolve_account
        from paper_trading_v2.sleeve_slots import account_remaining
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            seg = resolve_account(conn, stock)
            if seg is None or (seg['strategy'] or '') == 'NEWS':
                return                                  # 无段/NEWS 段=旧路径（红线：零改动）
            if not self._is_v11_native(conn, seg, stock):
                return                                  # 旧段/桥段：v9 物理现金模型不动
            ledger = conn.execute("SELECT total, free FROM pool_ledger WHERE id=1").fetchone()
            if ledger is None:
                return
            total, free = ledger['total'], ledger['free']
            floor = FLOOR_RATIO * total
            qty, _ = account_remaining(conn, seg['id'])
            is_entry = qty <= 0
            manual = (seg['source'] or '') == 'manual'
            entry_mode = seg['entry_mode'] or 'normal'
            if is_entry and not manual:
                if entry_mode == 'rotation':
                    # 轮换义务帽：未平仓义务（audit rotation_in 且其 rotation_out 标的
                    # 技术段仍有持仓=卖出义务未销账）不含本段 >1 → 拒
                    open_obs = []
                    for r in conn.execute("SELECT stock, reason FROM audit WHERE "
                                          "action='rotation_in'").fetchall():
                        if r['stock'] == stock:
                            continue
                        m = re.search(r"rotation_out=(\S+)", r['reason'] or '')
                        if not m:
                            continue
                        target = m.group(1).rstrip('|')
                        tseg = conn.execute(
                            "SELECT id FROM position WHERE stock=? AND status='open' "
                            "AND COALESCE(strategy,'')!='NEWS' ORDER BY id DESC "
                            "LIMIT 1", (target,)).fetchone()
                        if tseg and account_remaining(conn, tseg['id'])[0] > 0:
                            open_obs.append(target)
                    if len(open_obs) >= MAX_OPEN_ROTATIONS:
                        raise ValueError(
                            f"轮换义务帽：已有 {len(open_obs)} 笔未平仓轮换义务"
                            f"（{', '.join(open_obs)} 尚未换出），同时只允许 "
                            f"{MAX_OPEN_ROTATIONS} 笔——先完成上一轮换（卖出换出票）再来")
                else:
                    if free - required < floor:
                        raise ValueError(
                            f"物理穿底（真实口径）：成交后池现金 ¥{free - required:,.0f} "
                            f"< floor ¥{floor:,.0f}（20%×total，换仓期缓冲）。free 低是"
                            f"真实持仓吃掉现金，不是预算假稀缺——入场拒（§8.2.3：等 sell "
                            f"回款/降信封/走轮换伴配 entry_mode=rotation）")
            cash = conn.execute("SELECT cash FROM position WHERE id=?",
                                (seg['id'],)).fetchone()[0] or 0.0
            deficit = required - cash
            if deficit <= 0:
                return                                  # 段现金足覆：先段后池，pool 不动
            with conn:
                cur = conn.execute(
                    "UPDATE pool_ledger SET free=free-?, updated_at=? WHERE id=1 AND free>=?",
                    (deficit, now, deficit))
                if cur.rowcount == 0:
                    raise ValueError(
                        f"物理硬拒：池空闲 ¥{free:,.0f} 不足拨付差额 ¥{deficit:,.0f}"
                        f"（成交需 ¥{required:,.0f}，段现金 ¥{cash:,.0f}）")
                cur2 = conn.execute(
                    "UPDATE position SET cash=cash+? WHERE id=? AND status='open'",
                    (deficit, seg['id']))
                if cur2.rowcount == 0:
                    raise ValueError(f"{stock} 段已被并发关闭，拨付中止")
                conn.execute(
                    "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                    "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                    (now, 'pool_grant', stock, deficit, free, free - deficit,
                     f"buy 自动拨付（成交 ¥{required:,.0f} − 段现金 ¥{cash:,.0f}）", 'pool'))
            return True
        finally:
            conn.close()

    def sell_return_to_pool(self, stock, amount, note=''):
        """v11 sell 回池单点（trading.sell_stock 在 save_account 之后调用）。

        仅 v11 原生技术段生效（entry_mode/source 段列或本段已有 v11 流水）：
        回款从段 cash 条件扣出 → pool.free += amount（audit 'pool_return'），
        段.cash 不留存回款（流动性归公；残留 ≈0 由 release 清）。
        旧段/NEWS 段/迁移桥票据 → False（旧行为零变化）。同额重放=两笔真实卖出
        （trades 流水为真源，卖出主链本身无重放路径）。"""
        from paper_trading_v2.storage import resolve_account
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            seg = resolve_account(conn, stock)
            if not self._is_v11_native(conn, seg, stock):
                return False
            if amount is None or amount <= 0:
                return True
            with conn:
                # 段现金认领：条件扣减（cash>=amount），扣不到=并发已被他路径结算，出局不双计
                cur = conn.execute(
                    "UPDATE position SET cash=cash-? WHERE id=? AND status='open' AND cash>=?",
                    (amount, seg['id'], amount))
                if cur.rowcount == 0:
                    return False
                conn.execute(
                    "UPDATE pool_ledger SET free=free+?, updated_at=? WHERE id=1",
                    (amount, now))
                free = conn.execute("SELECT free FROM pool_ledger WHERE id=1").fetchone()[0]
                conn.execute(
                    "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                    "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                    (now, 'pool_return', stock, amount, free - amount, free,
                     f"sell 回款直接回池（v11 流动性归公）{('：' + note) if note else ''}",
                     'pool'))
            return True
        finally:
            conn.close()

    def allocate(self, stock, amount, reason, source="agent", code=None, pool=None,
                 grp=None, entry_mode='normal', rotation_out=None):
        """开持仓段（v11 信封化：只动 budget 承诺，不从 pool 搬 cash——段.cash=0 常态，
        物理钱只在 entry buy 瞬间经 pool_grant 出池）。段与审计同事务。

        v11 门（pool='main'，宪法禁 LLM 裁量，全部机械判据）：
        - 承诺率门：Σ段预算/total > 80% 且 entry_mode='normal' 且非 manual → 拒，
          话术引 §8.2.3（须先做轮换评估：候选 vs 场内低价值），除非 --rotation-out CODE
          伴配（CODE 必须有技术组 open 段，否则拒；audit rotation_out/rotation_in 行
          + 卖出 watchpoint（kv_store，mode=sell 现价×0.99 限价，2026-09-04 起——
          ROTATION_EXIT 事件已退役），跨包失败降级：stdout 警告+audit 留痕，
          入场不回滚——卖单可晨审补挂）。
        - 30% 单股帽/20 段位帽/冷却/同票互斥：保留（相对条件写+rowcount 认领不变）。

        pool='sleeve'：消息池成员拨款——**段 cash 模型分毫不动（红线：明早 9:30
        sleeve-fill 是 8 周灰度锚）**，仍从 sleeve_ledger 搬 cash 进段。
        grp 参数保留兼容签名；v9 组恒由 strategy 推导，传入值忽略。
        v9 变更：不再建/重置 accounts 行（表已退役）；旧段操作不再归档清空——
        旧段（closed）自带全部 trades/operations 历史（段即账户，历史天然分段）。
        """
        pool = pool or self.pool
        from paper_trading_v2.gate import enforce
        if pool == 'main':
            enforce(stock, 'allocate', pool=pool, source=source)   # 闸门：禁直接买 NEWS 票
        table = self._ledger(pool)
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            # R6/Y4：对侧检查（与 sleeve-open 同款语义：告警不拦截，晨审人工裁决，方案第四.8）——
            # 技术组 allocate 时该股另有活跃消息组段/槽 → 同票双组暴露
            if pool == 'main':
                n_news_seg = conn.execute(
                    "SELECT COUNT(*) FROM position WHERE stock=? AND status='open' "
                    "AND strategy='NEWS'", (stock,)).fetchone()[0]
                n_slot = conn.execute(
                    "SELECT COUNT(*) FROM event_slots es JOIN event_slot_members m "
                    "ON m.event_key=es.event_key WHERE m.stock=? AND es.status IN (?,?)",
                    (stock, 'open', 'partial')).fetchone()[0]
                if n_news_seg or n_slot:
                    print(f"⚠️ {stock} 消息组另有活跃事件段/槽（NEWS段×{n_news_seg}，"
                          f"活跃槽×{n_slot}）——同票双组暴露，晨审人工裁决（第四.8）")
            free = self._get_free(conn, pool)
            if free is None:
                raise ValueError(f"{self._label(pool)}未初始化，请先 init（pool={pool}）")
            if amount <= 0:
                raise ValueError("分配金额必须 > 0")
            if pool == 'sleeve' and amount > free:
                raise ValueError(f"{self._label(pool)}空闲不足：需 ¥{amount:,.0f}，空闲 ¥{free:,.0f}")
            already_open = conn.execute(
                "SELECT id FROM position WHERE stock=? AND status='open' "
                + ("AND strategy='NEWS'" if pool == 'sleeve' else f"AND {_MAIN_SEG_FILTER}"),
                (stock,)).fetchone()
            if already_open:
                raise ValueError(f"{stock} 已有 open 段，需先 release 再重新 allocate")
            rotation_out_code = None
            if pool == 'main':
                total = conn.execute(f"SELECT total FROM {table} WHERE id=1").fetchone()[0]
                if amount > 0.3 * total:
                    raise ValueError(f"单股分配超过总池 30%：¥{amount:,.0f} > 30%×{total:,.0f}")
                # ---- v11 承诺率门（信封可超售，假稀缺退场；真约束=入场 floor 门） ----
                if entry_mode not in ('normal', 'rotation'):
                    raise ValueError(f"未知 entry-mode {entry_mode!r}（可选 normal/rotation）")
                if entry_mode == 'rotation' and not rotation_out:
                    raise ValueError("轮换换入必须伴配 --rotation-out CODE（禁无伴配轮换入，"
                                     "否则承诺率门被空转）")
                if rotation_out:
                    trow = conn.execute(
                        "SELECT code FROM position WHERE stock=? AND status='open' "
                        "AND COALESCE(strategy,'')!='NEWS' ORDER BY id DESC LIMIT 1",
                        (rotation_out,)).fetchone()
                    if trow is None:
                        raise ValueError(
                            f"--rotation-out {rotation_out} 不存在（无技术组 open 段）——"
                            f"伪造伴配拒；轮换对象必须是场内真实持仓段")
                    rotation_out_code = trow['code']
                # 9/3 裁决：轮换门量真实占用（Σ净持仓成本/total > 2/3，预留 1/3）。
                # 承诺率（Σ预算/total）只作展示——预算睡觉不该挡新入场（假稀缺根除）。
                real_cost = self._real_net_cost(conn)
                _c, total = self._v11_commitment(conn)
                real_rate = real_cost / total if total else 0.0
                if real_rate > ROTATION_GATE and entry_mode == 'normal' \
                        and source != 'manual':
                    raise ValueError(
                        f"真实占用率门：Σ净持仓成本/total = {real_rate*100:.1f}% > "
                        f"{ROTATION_GATE*100:.1f}%（预留 1/3 机动，9/3 裁决）——新入场须先做"
                        f"轮换评估（候选 vs 场内低价值，机械分，纪律 §8.2.3）：确认候选价值"
                        f"高于场内垫底持仓后，用 --entry-mode rotation --rotation-out CODE "
                        f"伴配入场（CODE=拟换出票，自动挂卖出 watchpoint 限 T+1 挂出）；"
                        f"人工裁决可 --source manual（L1 特权同款）")
                strat, pool_code = self._get_strategy(conn, stock)
                if code is None:
                    code = pool_code
                if strat != 'L1':
                    cool = conn.execute(
                        "SELECT cooldown_until FROM position WHERE stock=? ORDER BY id DESC LIMIT 1",
                        (stock,)).fetchone()
                    if cool and cool[0] and datetime.now() < datetime.fromisoformat(cool[0]):
                        raise ValueError(f"{stock} 在冷却期内（至 {cool[0]}），禁止 allocate")
                    open_count = conn.execute(
                        f"SELECT COUNT(*) c FROM position WHERE status='open' AND {_MAIN_SEG_FILTER}"
                    ).fetchone()['c']
                    if open_count >= 20:
                        raise ValueError(f"持仓段已满（{open_count}/20），需先 release 再开新段")
            else:
                # 消息池成员段：20 事件坑 = event_slots 活跃行（开新槽由 SleeveOpener 校验，
                # 本方法补段不做槽上限；消息池单股帽=消息池 total 由 init 侧保证）
                strat = 'NEWS'
                pool_code = conn.execute("SELECT code FROM pool WHERE stock=?",
                                         (stock,)).fetchone()
                if code is None:
                    code = pool_code['code'] if pool_code else None
            with conn:
                if pool == 'main':
                    # v11 信封化：pool.free 分文不动（budget=计划层承诺，可超售）；
                    # 段认领=条件 INSERT（同票活跃段互斥 + 20 段位帽，原子守卫保留）
                    pos_strategy = 'L1'
                    cap_guard = ("AND (SELECT COUNT(*) FROM position WHERE status='open' "
                                 f"AND {_MAIN_SEG_FILTER}) < 20")
                    cur = conn.execute(
                        "INSERT INTO position (stock, code, strategy, status, budget, "
                        "topup_total, opened_at, cash, fifo_index, fifo_offset, "
                        "entry_mode, source) "
                        "SELECT ?,?,?, 'open', ?, 0, ?, 0, -1, 0, ?, ? "
                        "WHERE NOT EXISTS (SELECT 1 FROM position WHERE stock=? AND "
                        "status='open' AND COALESCE(strategy,'') != 'NEWS') "
                        + cap_guard,
                        (stock, code, pos_strategy, amount, now, entry_mode, source,
                         stock))
                    if cur.rowcount == 0:
                        raise ValueError(f"{stock} 已有 open 段（或段位已满），"
                                         f"需先 release 再开新段")
                    seg_id = cur.lastrowid
                    conn.execute(
                        "INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                        "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                        (now, 'allocate', stock, amount, free, free,
                         f"v11 信封（承诺，不动现金）entry_mode={entry_mode}"
                         + (f" rotation_out={rotation_out}" if rotation_out else ""),
                         source))
                    if rotation_out:
                        pair_note = (f"轮换伴配：入 {stock}（¥{amount:,.0f}） ↔ 换出 "
                                     f"{rotation_out}|rotation_out={rotation_out}")
                        conn.execute(
                            "INSERT INTO audit (timestamp, action, stock, amount, "
                            "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                            (now, 'rotation_out', rotation_out, None, free, free,
                             pair_note, source))
                        conn.execute(
                            "INSERT INTO audit (timestamp, action, stock, amount, "
                            "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                            (now, 'rotation_in', stock, None, free, free,
                             pair_note, source))
                    # 分配预算 → 档位自动升 L1（仅主池；消息组档位是槽，不动池 strategy）
                    conn.execute("UPDATE pool SET strategy='L1' WHERE stock=? AND pool_status='active'",
                                 (stock,))
                else:
                    # ---- NEWS 池旧路径（红线：v11 零改动）----
                    # M1.7/F4：资金认领=相对条件扣减（`free=free-? WHERE free>=?` + rowcount），
                    # 丢失更新在写入瞬间出局（旧 `SET free=绝对值` 会覆盖并发方的相对写）
                    cur = conn.execute(f"UPDATE {table} SET free=free-?, updated_at=? "
                                       "WHERE id=1 AND free>=?", (amount, now, amount))
                    if cur.rowcount == 0:
                        raise ValueError(f"{self._label(pool)}空闲不足：需 ¥{amount:,.0f}，"
                                         f"空闲 ¥{free:,.0f}")
                    new_free = free - amount
                    # M1.7/F4：段认领=条件 INSERT（同票活跃段互斥 + 主池 20 段位帽，
                    # 计数与写入同语句原子——并发同票第二个 allocate 在此出局）
                    seg_filter = "strategy='NEWS'"
                    cur = conn.execute(
                        "INSERT INTO position (stock, code, strategy, status, budget, "
                        "topup_total, opened_at, cash, fifo_index, fifo_offset) "
                        "SELECT ?,?, 'NEWS', 'open', ?, 0, ?, ?, -1, 0 "
                        f"WHERE NOT EXISTS (SELECT 1 FROM position WHERE stock=? AND "
                        f"status='open' AND {seg_filter})",
                        (stock, code, amount, now, amount, stock))
                    if cur.rowcount == 0:
                        raise ValueError(f"{stock} 已有 open 段（或段位已满），"
                                         f"需先 release 再开新段")
                    seg_id = cur.lastrowid
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                                 "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                                 (now, 'sleeve_allocate', stock, amount, free, new_free,
                                  reason, source))
                    # v9：段即账户——段吸收现金（cash=拨款），init 流水行键到段
                    conn.execute("INSERT INTO operations (account_id, seq, type, capital, timestamp, "
                                 "note) VALUES (?,0,'init',?,?,'初始化资金池（段直建）')",
                                 (seg_id, amount, now))
            # 跨包卖出 watchpoint（事务外：挂点失败不回滚入场——降级合同）
            if pool == 'main' and rotation_out:
                self._emit_rotation_exit(rotation_out, rotation_out_code, stock, reason)
            return True
        finally:
            conn.close()

    @staticmethod
    def _import_taskbus_db():
        """跨包 import task_bus.db（失败降级 None，调用方 audit 留痕）。

        参考既有 sys.path 插入模式：paper_trading_v2/ → ../../task-bus/scripts。"""
        try:
            from task_bus import db as taskbus_db
        except ImportError:
            import sys
            _here = os.path.dirname(os.path.realpath(__file__))
            _tb = os.path.realpath(os.path.join(_here, '..', '..', '..',
                                                'task-bus', 'scripts'))
            if os.path.isdir(os.path.join(_tb, 'task_bus')) and _tb not in sys.path:
                sys.path.insert(0, _tb)
            try:
                from task_bus import db as taskbus_db
            except ImportError:
                return None
        return taskbus_db

    @staticmethod
    def _emit_rotation_exit(rotation_out, code, inbound, reason):
        """轮换出池 → 直接写卖出 watchpoint（kv_store('watch_points')，2026-09-04）：

        ROTATION_EXIT 事件类型已退役（task_bus/db.py TYPES 白名单移除），轮换卖单
        改走 watchpoint sell 机制——{mode:'sell', price: 现价×0.99}，现价 ≥ 挂点触发
        （次日可成交限价，禁梦价），心跳 watch_scan E7 触发 → WATCH_ALERT(mode=sell)
        → C1 消费执行卖仓。原 taskbus_db.add('ROTATION_EXIT') 在白名单退役后会
        ValueError，故直接写 watchpoint，一处机制多处用。

        跨包 import 失败/写库失败降级：stdout 警告 + audit 'rotation_exit_pending'
        留痕，入场不回滚（卖单可晨审补挂）。现价 fetch 失败 → price=None 占位 +
        note 注明'晨审补价'（无价点不触发，由晨审 agent 补挂真实限价）。
        """
        taskbus_db = MasterPoolManager._import_taskbus_db()
        if taskbus_db is None:
            print("⚠️ task_bus 包不可 import（卖出 watchpoint 降级 audit 留痕，晨审补挂）")
            MasterPoolManager._audit_rotation_exit_pending(rotation_out, inbound,
                                                           "task_bus 不可达")
            return
        note = f"轮换出池换入{inbound}限价卖"
        price = None
        if code:
            try:
                price = MasterPoolManager._fetch_rotation_price(code)
            except Exception as e:
                print(f"⚠️ 轮换出池现价获取失败（{code}）：{e}——挂点无价，晨审补价")
        if price is None:
            note += "（晨审补价：现价获取失败，price 占位待补真实限价）"
        try:
            points = taskbus_db.kv_get('watch_points')
            if not isinstance(points, dict):
                points = {}
            # sell 触发价 = 现价×0.99（次日可成交限价，禁梦价）；fetch 失败 → None 占位
            sell_price = round(price * 0.99, 2) if price is not None else None
            pts = points.setdefault(rotation_out, [])
            pts.append({
                "code": code, "price": sell_price,
                "note": note, "mode": "sell", "amount": None, "min": None,
                "added_at": datetime.now().strftime("%m-%d %H:%M"),
            })
            taskbus_db.kv_set('watch_points', points)
            px_txt = (f"触发价 ¥{sell_price}（现价¥{price:.2f}×0.99 限价）"
                      if sell_price is not None else "⚠️无现价占位（晨审补价）")
            print(f"✅ 轮换出池 {rotation_out}({code}) 卖出 watchpoint 已挂：{px_txt}（换入 {inbound}）")
        except Exception as e:
            print(f"⚠️ 卖出 watchpoint 写入失败（入场不回滚，晨审补挂）：{e}")
            MasterPoolManager._audit_rotation_exit_pending(rotation_out, inbound,
                                                           f"watchpoint 写入失败：{e}")

    @staticmethod
    def _fetch_rotation_price(code):
        """轮换出池现价（fetch 失败抛异常由调用方降级）：先 paper_trading_v2
        StockPriceFetcher（同包无跨包问题），失败回退 taskbus 侧 ptrade2 CLI。
        """
        try:
            from paper_trading_v2.price_fetcher import StockPriceFetcher
            info = StockPriceFetcher().get_realtime_price(code)
            if info and info.current_price:
                return float(info.current_price)
        except Exception:
            pass
        # 回退：watch_scan 的 fetch_price（ptrade2 fetch-price，'当前价格: ¥X'）
        import sys
        _here = os.path.dirname(os.path.realpath(__file__))
        _tb = os.path.realpath(os.path.join(_here, '..', '..', '..',
                                            'task-bus', 'scripts'))
        if os.path.isdir(_tb) and _tb not in sys.path:
            sys.path.insert(0, _tb)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_watch_scan_price", os.path.join(_tb, "watch_scan.py"))
        if spec is None or spec.loader is None:
            raise ImportError(f"watch_scan.py 无法加载（{_tb}）")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)   # 模块级只定义函数/常量，无副作用
            return mod.fetch_price_any(code)
        finally:
            sys.modules.pop("_watch_scan_price", None)

    @staticmethod
    def _audit_rotation_exit_pending(stock, inbound, why):
        """降级留痕：audit 'rotation_exit_pending' 行（晨审按此补挂卖出 watchpoint）。"""
        try:
            conn = MasterPoolManager()._conn()
            now = datetime.now().isoformat()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO audit (timestamp, action, stock, amount, reason, "
                        "source) VALUES (?,?,?,?,?,?)",
                        (now, 'rotation_exit_pending', stock, None,
                         f"{why}，卖出 watchpoint 待晨审补挂（换入 {inbound}）",
                         'rotation_gate'))
            finally:
                conn.close()
        except Exception as e2:
            print(f"⚠️ 轮换出池降级 audit 留痕也失败（不阻断入场）：{e2}")

    # ---------- topup ----------

    def topup(self, stock, amount, reason, source="agent", pool=None):
        pool = pool or self.pool
        """段内注资（v11 信封加码：主池只加 budget/topup_total，**不搬段 cash**——
        机动权利非预支现金，钱仍在 pool，buy 时经 pool_grant 拨付）。同事务。

        v11：主池 free 不扣（预算=计划层）；≤30% 帽保留（floor 豁免是 buy 侧语义，
        topup buy（qty>0 再买）floor 豁免=机动资金本意）。
        pool='sleeve'：**旧行为原样**（从 sleeve_ledger 搬 cash 进段——红线不动）。

        闸门：消息组加仓锁死（灰度）——NEWS 段与迁移票（topup_locked）一律拒绝。
        """
        from paper_trading_v2.gate import enforce
        enforce(stock, 'topup', source=source)      # 闸门：news 加仓锁 / 迁移票加仓锁
        table = self._ledger(pool)
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            free = self._get_free(conn, pool)
            if free is None:
                raise ValueError(f"{self._label(pool)}未初始化")
            if amount <= 0:
                raise ValueError("注资金额必须 > 0")
            if pool == 'sleeve' and amount > free:
                raise ValueError(f"{self._label(pool)}空闲不足：需 ¥{amount:,.0f}，空闲 ¥{free:,.0f}")
            if pool == 'main':
                seg = conn.execute(
                    f"SELECT * FROM position WHERE stock=? AND status='open' AND {_MAIN_SEG_FILTER}",
                    (stock,)).fetchone()
                if not seg:
                    raise ValueError(f"{stock} 没有 open 段，需先 allocate")
                total = conn.execute(f"SELECT total FROM {table} WHERE id=1").fetchone()[0]
                if seg['budget'] + amount > 0.3 * total:
                    raise ValueError(f"单股累计分配超总池 30%：{seg['budget']+amount:,.0f} > 30%×{total:,.0f}")
            else:
                seg = conn.execute(
                    "SELECT * FROM position WHERE stock=? AND status='open' AND strategy='NEWS'",
                    (stock,)).fetchone()
                if not seg:
                    raise ValueError(f"{stock} 没有消息池 open 段，需先 sleeve-open")
            with conn:
                if pool == 'main' and self._is_v11_native(conn, seg, stock):
                    # v11 信封加码：free 不动；段预算=相对累加 + status 守卫（并发关段出局）
                    cur = conn.execute("UPDATE position SET budget=budget+?, "
                                       "topup_total=topup_total+? "
                                       "WHERE id=? AND status='open'",
                                       (amount, amount, seg['id']))
                    if cur.rowcount == 0:
                        raise ValueError(f"{stock} 段已被并发关闭，注资中止")
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                                 "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                                 (now, 'topup', stock, amount, free, free,
                                  f"v11 信封加码（机动权利，不动现金）：{reason}", source))
                else:
                    # ---- 旧物理路径（NEWS 池红线 + v11 前存量段/桥段：v11 零改动）----
                    # M1.7/F4：资金认领=相对条件扣减 + rowcount（丢失更新在写入瞬间出局）
                    cur = conn.execute(f"UPDATE {table} SET free=free-?, updated_at=? "
                                       "WHERE id=1 AND free>=?", (amount, now, amount))
                    if cur.rowcount == 0:
                        raise ValueError(f"{self._label(pool)}空闲不足：需 ¥{amount:,.0f}，"
                                         f"空闲 ¥{free:,.0f}")
                    new_free = free - amount
                    # 段预算/段现金同步=相对累加 + status 守卫（段已被并发关闭则出局回滚）
                    cur = conn.execute("UPDATE position SET budget=budget+?, topup_total=topup_total+?, "
                                       "cash=cash+? WHERE id=? AND status='open'",
                                       (amount, amount, amount, seg['id']))
                    if cur.rowcount == 0:
                        raise ValueError(f"{stock} 段已被并发关闭，注资中止")
                    conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                                 "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                                 (now, 'topup' if pool == 'main' else 'sleeve_topup',
                                  stock, amount, free, new_free, reason, source))
            return True
        finally:
            conn.close()

    # ---------- release ----------

    def release(self, stock, reason, source="agent", pool=None, archive=None):
        pool = pool or self.pool
        """关持仓段：空仓后把现值回 free，段归档。

        archive（默认读 env SLEEVE_ARCHIVE_ON_CLEAR，方案 2.6b）：
        - True  → 池行 archived 终态（不再"降回 L2"，清仓不自动回池）
        - False → 旧行为：档位自动降回 L2
        pool='sleeve'：关 NEWS 成员段，现值回 sleeve_ledger + 槽对账（realized/partial/closed）。
        """
        if archive is None:
            archive = os.environ.get('SLEEVE_ARCHIVE_ON_CLEAR', '') == '1'
        table = self._ledger(pool)
        conn = self._conn()
        now = datetime.now().isoformat()
        try:
            if pool == 'sleeve':
                seg = conn.execute(
                    "SELECT * FROM position WHERE stock=? AND status='open' AND strategy='NEWS'",
                    (stock,)).fetchone()
            else:
                seg = conn.execute(
                    f"SELECT * FROM position WHERE stock=? AND status='open' AND {_MAIN_SEG_FILTER}",
                    (stock,)).fetchone()
            if not seg:
                which = '消息池' if pool == 'sleeve' else ''
                raise ValueError(f"{stock} 没有{which} open 段".replace('  ', ' '))
            # 读段现金（可能触发 get_account 写回修正）在池写事务前完成，避免同库写锁冲突
            if pool == 'sleeve':
                from paper_trading_v2.sleeve_slots import (
                    news_account_id, account_remaining, settle_member_clear)
                aid = news_account_id(conn, stock)
                if aid is None:
                    raise ValueError(f"{stock} 无 NEWS open 段（非 sleeve 成员）")
                qty, _ = account_remaining(conn, aid)
                if qty > 0:
                    raise ValueError(f"{stock} 仍有持仓 {qty} 股，先清仓再 release")
                value = conn.execute("SELECT cash FROM position WHERE id=?",
                                     (aid,)).fetchone()[0] or 0.0
                result = settle_member_clear(conn, stock, value, reason=reason,
                                             source=source, archive=archive, now=now)
                if result is None:
                    raise ValueError(f"{stock} sleeve 成员结算失败")
                return True
            from paper_trading_v2.trading import PaperTrader
            trader = PaperTrader()
            acct = trader.get_account(stock)
            if acct is None:
                raise ValueError(f"{stock} 无可寻址段（账户已退役，v9 段即账户）")
            qty, _ = trader.get_remaining_position(acct)
            if qty > 0:
                raise ValueError(f"{stock} 仍有持仓 {qty} 股，先清仓再 release")
            value = acct.capital_pool.available
            # v11 信封段：卖出回款已随 pool_return 归池（残留 value≈0），
            # realized_pnl 真相=bump_segment_realized 逐笔累计（release 不得再用
            # value−budget 重写——旧物理模型公式在信封制下失真）。旧段/桥段公式原样。
            v11_native = self._is_v11_native(conn, seg, stock)
            realized_final = (seg['realized_pnl'] or 0.0) if v11_native \
                else value - seg['budget']
            with conn:
                # M1.7/F4：段认领=条件 UPDATE（`status='open'` 才能关段）——
                # 双 release / release×清仓竞态第二遍在此出局，杜绝双回款
                # v9：段现金/FIFO 一并清零（原"accounts 清零防双算显示"语义，段自承载）
                cur = conn.execute(
                    "UPDATE position SET status='closed', closed_at=?, close_value=?, "
                    "realized_pnl=?, cooldown_until=?, cash=0, fifo_index=-1, fifo_offset=0 "
                    "WHERE id=? AND status='open'",
                    (now, value, realized_final,
                     (datetime.now() + timedelta(days=7)).isoformat(), seg['id']))
                if cur.rowcount == 0:
                    raise ValueError(f"{stock} 段已被并发释放，不能重复 release")
                free = self._get_free(conn, pool)
                conn.execute(f"UPDATE {table} SET free=free+?, updated_at=? WHERE id=1",
                             (value, now))
                new_free = free + value
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (now, 'release', stock, value, free, new_free, reason, source))
                # 档案化：迁移票（event_key→migrated 槽）恒 archived 终态（2.6b v4.2 行：
                # 不复活 NEWS 旧槽、不降 L2 复活技术组候选）；其余走 flag（开=archived，关=降 L2 旧行为）
                migrated_ticket = False
                prow = conn.execute("SELECT event_key FROM pool WHERE stock=?",
                                    (stock,)).fetchone()
                if prow and prow['event_key']:
                    st = conn.execute("SELECT status FROM event_slots WHERE event_key=?",
                                      (prow['event_key'],)).fetchone()
                    migrated_ticket = bool(st and st['status'] == 'migrated')
                if archive or migrated_ticket:
                    conn.execute("UPDATE pool SET pool_status='archived', archived_at=? "
                                 "WHERE stock=?", (now, stock))
                else:
                    conn.execute("UPDATE pool SET strategy='L2' WHERE stock=? AND pool_status='active'",
                                 (stock,))
            return True
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def records(self, days=None, pool=None):
        """资金流水审计。pool='sleeve' 只看消息池动作（action LIKE 'sleeve_%'），其余=主池。"""
        conn = self._conn()
        try:
            q = "SELECT * FROM audit"
            if pool == 'sleeve':
                q += " WHERE action LIKE 'sleeve%'"
            elif pool == 'main':
                q += " WHERE action NOT LIKE 'sleeve%'"
            if days:
                since = (datetime.now() - timedelta(days=days)).isoformat()
                q += " AND timestamp>=?" if 'WHERE' in q else " WHERE timestamp>=?"
                q += " ORDER BY id"
                rows = conn.execute(q, (since,)).fetchall()
            else:
                q += " ORDER BY id"
                rows = conn.execute(q).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
