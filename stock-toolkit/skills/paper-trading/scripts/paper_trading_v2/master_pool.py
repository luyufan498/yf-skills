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

# 主池段统计/段位上限的排除口径：sleeve 成员段（strategy='NEWS'）归消息池，不侵占主池
_MAIN_SEG_FILTER = "COALESCE(strategy,'') != 'NEWS'"


class MasterPoolManager:
    """总池账本：total 固定，free 被 allocate/topup/release 驱动，审计留痕。

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
        try:
            with conn:
                row = conn.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()
                if row:
                    raise ValueError(f"{self._label(pool)}已初始化 total={row['total']}，"
                                     f"需删除数据库重置")
                conn.execute(f"INSERT INTO {table} (id, total, free, updated_at) "
                             "VALUES (1, ?, ?, ?)",
                             (total, total, datetime.now().isoformat()))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (datetime.now().isoformat(), 'init', None, total, 0, total,
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
                realized = ledger['free'] + occupied - ledger['total']
                return {
                    "total": ledger['total'], "free": ledger['free'],
                    "occupied": occupied,
                    "usage_rate": occupied / ledger['total'] if ledger['total'] else 0,
                    "realized_pnl": realized,
                    "open_segments": open_count,
                }
            # sleeve：槽视角对账（占用=活跃槽预算；realized=Σ槽 realized）
            occupied = conn.execute(
                "SELECT COALESCE(SUM(budget),0) s FROM event_slots WHERE status IN (?,?)",
                ('open', 'partial')).fetchone()['s']
            realized = conn.execute(
                "SELECT COALESCE(SUM(realized),0) s FROM event_slots").fetchone()['s']
            member_segments = conn.execute(
                "SELECT COUNT(*) c FROM position WHERE status='open' AND strategy='NEWS'"
            ).fetchone()['c']
            active_slots = conn.execute(
                "SELECT COUNT(*) c FROM event_slots WHERE status IN (?,?)",
                ('open', 'partial')).fetchone()['c']
            pending = conn.execute(
                "SELECT COUNT(*) c FROM event_slots WHERE fill_status='pending' "
                "AND status IN (?,?)", ('open', 'partial')).fetchone()['c']
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

    def allocate(self, stock, amount, reason, source="agent", code=None, pool=None,
                 grp=None):
        """开持仓段：从 free 拨 budget，建/重置账户，记 audit。账户与池同事务。

        pool='main'（默认）：主池 allocate，行为与改造前一致（账户 grp='tech'）。
        pool='sleeve'：消息池成员拨款——账户 grp='news'、段 strategy='NEWS'、
        不改池档位（消息组 L1 实体是事件槽，非池档位）。
        sleeve-open（多成员原子开槽）走 sleeve_open.SleeveOpener，不经本方法。
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
            if amount > free:
                raise ValueError(f"{self._label(pool)}空闲不足：需 ¥{amount:,.0f}，空闲 ¥{free:,.0f}")
            already_open = conn.execute(
                "SELECT id FROM position WHERE stock=? AND status='open' "
                + ("AND strategy='NEWS'" if pool == 'sleeve' else f"AND {_MAIN_SEG_FILTER}"),
                (stock,)).fetchone()
            if already_open:
                raise ValueError(f"{stock} 已有 open 段，需先 release 再重新 allocate")
            if pool == 'main':
                total = conn.execute(f"SELECT total FROM {table} WHERE id=1").fetchone()[0]
                if amount > 0.3 * total:
                    raise ValueError(f"单股分配超过总池 30%：¥{amount:,.0f} > 30%×{total:,.0f}")
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
                if grp is None:
                    grp = 'news'
            with conn:
                new_free = free - amount
                conn.execute(f"UPDATE {table} SET free=?, updated_at=? WHERE id=1",
                             (new_free, now))
                pos_strategy = 'NEWS' if pool == 'sleeve' else 'L1'
                conn.execute("INSERT INTO position (stock, code, strategy, status, budget, "
                             "topup_total, opened_at) VALUES (?,?,?,'open',?,0,?)",
                             (stock, code, pos_strategy, amount, now))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (now, 'allocate' if pool == 'main' else 'sleeve_allocate',
                              stock, amount, free, new_free, reason, source))
                # ---- 账户：同一事务直接 SQL ----
                acct = conn.execute("SELECT id FROM accounts WHERE stock_name=? AND grp=?",
                                    (stock, grp or 'tech')).fetchone()
                if acct:
                    aid = acct[0]
                    # 归档旧段操作（保留历史），再重置账户
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
                    conn.execute(
                        "UPDATE accounts SET stock_code=COALESCE(?, stock_code), capital_total=?, "
                        "capital_available=?, capital_used=0, fifo_index=-1, fifo_offset=0, "
                        "updated_at=? WHERE id=?",
                        (code, amount, amount, now, aid))
                else:
                    cur = conn.execute(
                        "INSERT INTO accounts (stock_name, stock_code, capital_total, "
                        "capital_available, capital_used, fifo_index, fifo_offset, grp, "
                        "created_at, updated_at) VALUES (?,?,?,?,0,-1,0,?,?,?)",
                        (stock, code, amount, amount, grp or 'tech', now, now))
                    aid = cur.lastrowid
                conn.execute("INSERT INTO operations (account_id, seq, type, capital, timestamp, "
                             "note) VALUES (?,0,'init',?,?,'初始化资金池')",
                             (aid, amount, now))
                # 分配预算 → 档位自动升 L1（仅主池；消息组档位是槽，不动池 strategy）
                if pool == 'main':
                    conn.execute("UPDATE pool SET strategy='L1' WHERE stock=? AND pool_status='active'",
                                 (stock,))
            return True
        finally:
            conn.close()

    # ---------- topup ----------

    def topup(self, stock, amount, reason, source="agent", pool=None):
        pool = pool or self.pool
        """段内注资：从 free 拨差额进账户，同步加 total/available。同事务。

        闸门：消息组加仓锁死（灰度）——grp=news 账户与迁移票（topup_locked）一律拒绝。
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
            if amount > free:
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
            acct = conn.execute("SELECT id FROM accounts WHERE stock_name=? AND grp=?",
                                (stock, 'news' if pool == 'sleeve' else 'tech')).fetchone()
            if not acct:
                raise ValueError(f"账户 {stock} 不存在")
            aid = acct[0]
            with conn:
                conn.execute("UPDATE position SET budget=budget+?, topup_total=topup_total+? "
                             "WHERE id=?", (amount, amount, seg['id']))
                new_free = free - amount
                conn.execute(f"UPDATE {table} SET free=?, updated_at=? WHERE id=1",
                             (new_free, now))
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, "
                             "free_before, free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (now, 'topup' if pool == 'main' else 'sleeve_topup',
                              stock, amount, free, new_free, reason, source))
                conn.execute("UPDATE accounts SET capital_total=capital_total+?, "
                             "capital_available=capital_available+?, updated_at=? WHERE id=?",
                             (amount, amount, now, aid))
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
            # 读账户（可能触发写回）在池写事务前完成，避免同库写锁冲突
            if pool == 'sleeve':
                from paper_trading_v2.sleeve_slots import (
                    news_account_id, account_remaining, settle_member_clear)
                aid = news_account_id(conn, stock)
                if aid is None:
                    raise ValueError(f"账户 {stock}（grp=news）不存在")
                qty, _ = account_remaining(conn, aid)
                if qty > 0:
                    raise ValueError(f"{stock} 仍有持仓 {qty} 股，先清仓再 release")
                value = conn.execute("SELECT capital_available FROM accounts WHERE id=?",
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
                raise ValueError(f"账户 {stock} 不存在")
            qty, _ = trader.get_remaining_position(acct)
            if qty > 0:
                raise ValueError(f"{stock} 仍有持仓 {qty} 股，先清仓再 release")
            value = acct.capital_pool.available
            free = self._get_free(conn, pool)
            new_free = free + value
            with conn:
                conn.execute(f"UPDATE {table} SET free=?, updated_at=? WHERE id=1",
                             (new_free, now))
                realized = value - seg['budget']
                conn.execute("UPDATE position SET status='closed', closed_at=?, close_value=?, "
                             "realized_pnl=?, cooldown_until=? WHERE id=?",
                             (now, value, realized,
                              (datetime.now() + timedelta(days=7)).isoformat(), seg['id']))
                # 2026-08-27 修复：段关闭 → accounts 表清零（资金已回 free 池，
                # 避免详情页残留"段资金 ¥50 万/可用 ¥45 万"双算显示）
                conn.execute("UPDATE accounts SET capital_total=0, capital_available=0, "
                             "capital_used=0, updated_at=? WHERE grp='tech' AND stock_name=?",
                             (now, stock))
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
