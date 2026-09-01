"""sleeve 槽与影子账共享工具（sleeve-m1，方案 2.2/2.7/3.7）

槽状态机（唯一状态机，方案 3.7）：open / partial / migrated / closed / archived
- 槽占用（20 事件坑计数）= status ∈ (open, partial) 的行数
  （方案 2.2 的占用元组含 migrated，但 2.3"事件坑随之释放（槽数-1）"与 2.5"活跃计数"
   更具体——按 2.3 执行：migrated 不占坑。偏差已记录于 M1 报告。）
- 成员部分退出（TP1 卖 1/3）→ 槽 partial 仍占坑；全部成员持仓清零 → closed 释放
- budget 对账：event_slots.realized 累计成员已实现盈亏；未实现=成员持仓市值

影子账（shadow_log，9 类 + gate_violation）：永不回流生产字段（转正=新决策）。
"""
import json
import sqlite3
from datetime import datetime

# 活跃槽状态（占坑口径）
SLOT_ACTIVE = ('open', 'partial')

NEWS_KINDS = ('price_cycle', 'policy', 'earnings', 'company_event',
              'tech_catalyst', 'sentiment', 'other')


def now_iso() -> str:
    return datetime.now().isoformat()


def shadow_write(conn: sqlite3.Connection, kind: str, key, payload: dict,
                 payoff=None, created_at=None):
    """写一条影子账（payload JSON 契约见 paper-trading skill「影子账 9 类」）。"""
    conn.execute(
        "INSERT INTO shadow_log (kind, key, payload, payoff, created_at) "
        "VALUES (?,?,?,?,?)",
        (kind, str(key) if key is not None else None,
         json.dumps(payload, ensure_ascii=False), payoff, created_at or now_iso()))


def active_slot_count(conn) -> int:
    """20 事件坑计数器 = event_slots 活跃行数（与主池 20 段位上限并存互不侵占）。"""
    return conn.execute(
        "SELECT COUNT(*) FROM event_slots WHERE status IN (?,?)",
        SLOT_ACTIVE).fetchone()[0]


def get_slot(conn, event_key):
    return conn.execute("SELECT * FROM event_slots WHERE event_key=?",
                        (event_key,)).fetchone()


def member_slot(conn, stock, include_migrated=False):
    """stock → (slot_row, member_row)。按 opened_at 最新优先。"""
    statuses = ('open', 'partial', 'migrated') if include_migrated else ('open', 'partial')
    ph = ','.join('?' * len(statuses))
    slot = conn.execute(
        f"SELECT s.* FROM event_slots s JOIN event_slot_members m ON m.event_key=s.event_key "
        f"WHERE m.stock=? AND s.status IN ({ph}) ORDER BY s.opened_at DESC LIMIT 1",
        (stock,) + statuses).fetchone()
    if not slot:
        return None, None
    member = conn.execute("SELECT * FROM event_slot_members WHERE event_key=? AND stock=?",
                          (slot['event_key'], stock)).fetchone()
    return slot, member


def account_remaining(conn, account_id):
    """FIFO 剩余 (qty, cost)——感知除权除息（与 trading.get_remaining_position 同算法，
    直接读 positions 表行，供 sleeve 事务内使用，不走 PaperTrader 网络路径）。"""
    rows = conn.execute("SELECT operation, quantity, total_cost FROM positions "
                        "WHERE account_id=? ORDER BY seq", (account_id,)).fetchall()
    queue = []
    for r in rows:
        op = r['operation']
        qty = r['quantity'] or 0
        cost = r['total_cost'] or 0
        if op == 'buy':
            queue.append([float(qty), cost / qty if qty else 0.0])
        elif op == 'sell':
            while qty > 0 and queue:
                if queue[0][0] <= qty:
                    qty -= queue[0][0]
                    queue.pop(0)
                else:
                    queue[0][0] -= qty
                    qty = 0
        elif op == 'exright_bonus':
            if queue:
                total = sum(i[0] for i in queue)
                if total:
                    ratio = 1 + (qty / total)
                    for i in queue:
                        i[0] *= ratio
                        i[1] /= ratio
        elif op == 'exright_dividend':
            if queue:
                total = sum(i[0] for i in queue)
                if total:
                    dps = abs(cost) / total
                    for i in queue:
                        i[1] -= dps
    total_qty = int(sum(i[0] for i in queue))
    total_cost = max(0.0, sum(i[0] * i[1] for i in queue))
    if total_qty == 0:
        total_cost = 0.0
    return total_qty, total_cost


def news_account_id(conn, stock):
    row = conn.execute("SELECT id FROM accounts WHERE stock_name=? AND grp='news' "
                       "ORDER BY id LIMIT 1", (stock,)).fetchone()
    return row[0] if row else None


def tech_account_id(conn, stock):
    row = conn.execute("SELECT id FROM accounts WHERE stock_name=? AND grp='tech' "
                       "ORDER BY id LIMIT 1", (stock,)).fetchone()
    return row[0] if row else None


def slot_lifecycle(conn, event_key, now=None):
    """成员全清 → closed（坑释放）；部分退出 → partial 仍占坑。返回新状态。"""
    now = now or now_iso()
    slot = get_slot(conn, event_key)
    if not slot:
        return None
    members = conn.execute("SELECT stock, exited_at, migrated_at FROM event_slot_members "
                           "WHERE event_key=?", (event_key,)).fetchall()
    live = [m for m in members if not m['exited_at'] and not m['migrated_at']]
    still_holding = []
    for m in live:
        aid = news_account_id(conn, m['stock'])
        if aid is not None:
            q, _ = account_remaining(conn, aid)
            if q > 0:
                still_holding.append(m['stock'])
    if members and not still_holding:
        new_status = 'closed'
        conn.execute("UPDATE event_slots SET status='closed', closed_at=? WHERE event_key=?",
                     (now, event_key))
    elif still_holding and len(still_holding) < len(members):
        new_status = 'partial'
        conn.execute("UPDATE event_slots SET status='partial' WHERE event_key=?", (event_key,))
    else:
        new_status = slot['status']
    return new_status


def settle_member_clear(conn, stock, value, reason='', source='sleeve', archive=False,
                        now=None):
    """sleeve 成员清仓结算（_auto_release_on_clear news 分支 / sleeve release 共用）。

    - 资金回 sleeve_ledger（不进主池——资金路由是正确性问题，不受 flag 影响）
    - 成员 position(strategy='NEWS') 段 closed；账户清零
    - 槽对账：realized += （回款 − 成员段预算）；全清 → 槽 closed
    - archive=True（SLEEVE_ARCHIVE_ON_CLEAR=1）→ 池行 archived 终态
      （同票双组保护：仅当该股无主池 open 段时才归档池行）
    返回 dict；非 sleeve 成员返回 None。
    """
    now = now or now_iso()
    aid = news_account_id(conn, stock)
    if aid is None:
        return None
    seg = conn.execute("SELECT * FROM position WHERE stock=? AND status='open' "
                       "AND strategy='NEWS' ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
    if not seg:
        return None
    slot, member = member_slot(conn, stock)
    # 成员账户未清仓 → 不结算（调用方先核验 qty==0）
    qty, _ = account_remaining(conn, aid)
    if qty > 0:
        return None
    member_budget = seg['budget'] or 0.0
    realized = value - member_budget
    with conn:
        # M1.7/F3：段认领=条件 UPDATE（status 守卫）——双结算/重放第二遍在此出局
        cur = conn.execute("UPDATE position SET status='closed', closed_at=?, close_value=?, "
                           "realized_pnl=? WHERE id=? AND status='open'",
                           (now, value, realized, seg['id']))
        if cur.rowcount == 0:
            return None
        conn.execute("UPDATE sleeve_ledger SET free=free+?, updated_at=? WHERE id=1",
                     (value, now))
        conn.execute("UPDATE accounts SET capital_total=0, capital_available=0, capital_used=0,"
                     " updated_at=? WHERE id=?", (now, aid))
        conn.execute("INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                     "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                     (now, 'sleeve_release', stock, value, None, None,
                     reason or 'sleeve 成员清仓回款', source))
        if slot:
            conn.execute("UPDATE event_slots SET realized=COALESCE(realized,0)+? "
                         "WHERE event_key=?", (realized, slot['event_key']))
            if member:
                conn.execute("UPDATE event_slot_members SET exited_at=? WHERE event_key=? "
                             "AND stock=?", (now, slot['event_key'], stock))
            slot_lifecycle(conn, slot['event_key'], now)
        if archive:
            # 池行档案化（同票双组保护：主池仍有 open 段则不动池行）
            tech_open = conn.execute(
                "SELECT COUNT(*) FROM position WHERE stock=? AND status='open' "
                "AND COALESCE(strategy,'')!='NEWS'", (stock,)).fetchone()[0]
            if not tech_open:
                conn.execute("UPDATE pool SET pool_status='archived', archived_at=? "
                             "WHERE stock=?", (now, stock))
    return {"stock": stock, "value": value, "realized": realized,
            "event_key": slot['event_key'] if slot else None}
