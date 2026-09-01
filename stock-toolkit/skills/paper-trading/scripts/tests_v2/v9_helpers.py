"""v9 测试工具箱（M1.6/U5）：段直建 fixtures + 段视角查询助手。

v9 段即账户：accounts 表退役（accounts_old 仅历史），测试一律段直建——
不再 INSERT INTO accounts，资金标签=position.budget、段现金=position.cash。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def news_seg_id(conn, stock):
    """stock 的 NEWS open 段 id（sleeve 成员，组由 strategy 推导）。"""
    row = conn.execute("SELECT id FROM position WHERE stock=? AND status='open' "
                       "AND strategy='NEWS' ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
    return row[0] if row else None


def tech_seg_id(conn, stock):
    """stock 的非 NEWS open 段 id（技术组 L1）。"""
    row = conn.execute("SELECT id FROM position WHERE stock=? AND status='open' "
                       "AND COALESCE(strategy,'')!='NEWS' ORDER BY id DESC LIMIT 1",
                       (stock,)).fetchone()
    return row[0] if row else None


def seg_cash(conn, seg_id):
    return conn.execute("SELECT cash FROM position WHERE id=?", (seg_id,)).fetchone()[0] or 0.0


def set_seg_cash(conn, seg_id, cash):
    conn.execute("UPDATE position SET cash=? WHERE id=?", (cash, seg_id))


def seg_budget(conn, seg_id):
    return conn.execute("SELECT budget FROM position WHERE id=?", (seg_id,)).fetchone()[0] or 0.0


def acct_total(conn, stock):
    """v8 Σaccounts.capital_total 的 v9 等价物：open 段 budget（资金标签）。"""
    row = conn.execute("SELECT budget FROM position WHERE stock=? AND status='open' "
                       "ORDER BY id DESC LIMIT 1", (stock,)).fetchone()
    return row[0] if row else 0.0


def money_label_sum(conn):
    """系统资金标签合计（v9）：Σ open 段 budget（v8 语义=Σ accounts.capital_total）。"""
    return conn.execute("SELECT COALESCE(SUM(budget),0) FROM position WHERE "
                        "status='open'").fetchone()[0]


def insert_buy(conn, seg_id, qty, price, code='sh1', seq=None, note=''):
    """段直插一笔 buy 流水（trades；纯 FIFO 现金流语义）。"""
    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM trades WHERE account_id=?",
                       (seg_id,)).fetchone()[0]
    conn.execute("INSERT INTO trades (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?)",
                 (seg_id, seq, 'buy', code, qty, price, qty * price,
                  '2026-09-01T10:00:00', note))


def insert_sell(conn, seg_id, qty, price, cost, code='sh1', note=''):
    """段直插一笔 sell 流水（测试注入用，等价保护链成交回款）。"""
    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM trades WHERE account_id=?",
                       (seg_id,)).fetchone()[0]
    conn.execute("INSERT INTO trades (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?)",
                 (seg_id, seq, 'sell', code, qty, price, cost, '2026-09-01T11:00:00', note))


def make_manual_segment(conn, stock, budget, cash=None, strategy='L1', code='sh1'):
    """段直建（U5 fixtures 开户模式替代）：manual open 段，budget=标签、cash=段现金。"""
    cur = conn.execute(
        "INSERT INTO position (stock, code, strategy, status, budget, topup_total, "
        "opened_at, cash, fifo_index, fifo_offset) VALUES (?,?,?,'open',?,0,?,?,-1,0)",
        (stock, code, strategy, budget, '2026-09-01T09:00:00',
         cash if cash is not None else budget))
    return cur.lastrowid
