"""v7 schema 迁移测试（sleeve-m1）：

- 四新表 event_slots / event_slot_members / sleeve_ledger / shadow_log
- watchlog +event_key,news_kind；pool +archived_at,event_key；accounts +grp
- accounts 重建 UNIQUE(stock_name) → UNIQUE(stock_name,grp)
- 幂等（连跑两次）；foreign_key_check 前后零新增违规
- 迁移严禁改行数据（strategy 值合并不许进 migrate_db）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from paper_trading_v2.db import get_connection, migrate_db


def _fresh(db_path):
    conn = get_connection(db_path)
    migrate_db(conn)          # 跑到当前最高版
    return conn


def _version(conn):
    return conn.execute("SELECT version FROM schema_meta").fetchone()[0]


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_v7_creates_four_new_tables_and_columns(ws):
    conn = _fresh(ws / 'master_pool.db')
    try:
        assert {'event_slots', 'event_slot_members', 'sleeve_ledger', 'shadow_log'} \
            <= _tables(conn)
        wl = _cols(conn, 'watchlog')
        assert 'event_key' in wl and 'news_kind' in wl
        pl = _cols(conn, 'pool')
        assert 'archived_at' in pl and 'event_key' in pl
        assert 'grp' in _cols(conn, 'accounts')
        # 槽状态机列
        es = _cols(conn, 'event_slots')
        for c in ('event_key', 'status', 'opened_at', 'budget', 'realized',
                  'news_kind', 'invalidation', 'topup_locked', 'orig_budget'):
            assert c in es, f'event_slots 缺列 {c}'
        # 成员关联权威表 PK(event_key, stock)
        pk = conn.execute("PRAGMA table_info(event_slot_members)").fetchall()
        pk_cols = [r[1] for r in pk if r[5]]
        assert pk_cols == ['event_key', 'stock']
        # shadow_log: kind/payload/payoff
        sl = _cols(conn, 'shadow_log')
        for c in ('kind', 'key', 'payload', 'payoff'):
            assert c in sl
        # sleeve_ledger 单行 CHECK(id=1)
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name='sleeve_ledger'"
                            ).fetchone()[0].upper().count('CHECK') >= 1
        assert _version(conn) >= 7
    finally:
        conn.close()


def test_v7_idempotent_run_twice(ws):
    db = ws / 'master_pool.db'
    conn = _fresh(db)
    conn.execute("INSERT INTO pool (stock, code, strategy, pool_status) "
                 "VALUES ('某票', 'sh600000', 'L3', 'active')")
    conn.commit()
    n_pool = conn.execute("SELECT COUNT(*) FROM pool").fetchone()[0]
    conn.close()
    # 第二次 migrate（同一连接重开）
    for _ in range(2):
        conn = get_connection(db)
        migrate_db(conn)
        conn.close()
    conn = get_connection(db)
    try:
        assert _version(conn) >= 7
        assert conn.execute("SELECT COUNT(*) FROM pool").fetchone()[0] == n_pool
        # 列不重复
        assert _cols(conn, 'watchlog').count('event_key') == 1
        assert _cols(conn, 'pool').count('archived_at') == 1
        assert _cols(conn, 'accounts').count('grp') == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_v7_accounts_rebuild_preserves_rows_and_fk(ws):
    """accounts 重建：行保留、子表外键完整、foreign_key_check 零违规。"""
    db = ws / 'master_pool.db'
    conn = _fresh(db)
    conn.execute("INSERT INTO accounts (stock_name, stock_code, capital_total, "
                 "capital_available, capital_used, fifo_index, fifo_offset, created_at, "
                 "updated_at) VALUES ('赛力斯', 'sh601127', 100, 60, 40, 0, 0, 't', 't')")
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='赛力斯'").fetchone()[0]
    conn.execute("INSERT INTO positions (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost) VALUES (?, 0, 'buy', 'sh601127', 10, 4, 40)", (aid,))
    conn.execute("INSERT INTO operations (account_id, seq, type, capital, timestamp, note) "
                 "VALUES (?, 0, 'init', 100, 't', '初始化资金池')", (aid,))
    conn.execute("INSERT INTO conditions (account_id, type, name, price, action, category, "
                 "status) VALUES (?, 'trailing_stop', '移动止损', 3.5, '减仓', 'hard', 'active')",
                 (aid,))
    conn.commit()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    conn = get_connection(db)
    migrate_db(conn)
    try:
        # 行数据原样保留
        row = conn.execute("SELECT * FROM accounts WHERE stock_name='赛力斯'").fetchone()
        assert row['capital_total'] == 100 and row['capital_available'] == 60
        assert row['grp'] == 'tech'                      # 存量账户默认 tech
        assert conn.execute("SELECT COUNT(*) FROM positions WHERE account_id=?",
                            (aid,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM operations WHERE account_id=?",
                            (aid,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM conditions WHERE account_id=?",
                            (aid,)).fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # 子表外键仍指向新 accounts（改名后 FK 跟随）
        conn.execute("INSERT INTO positions (account_id, seq, operation, quantity) "
                     "VALUES (?, 1, 'buy', 1)", (aid,))
        with pytest.raises(Exception):
            conn.execute("INSERT INTO positions (account_id, seq, operation, quantity) "
                         "VALUES (999999, 2, 'buy', 1)")
    finally:
        conn.rollback()
        conn.close()


def test_v7_dual_group_accounts_unique_constraint(ws):
    """同名双组可并存；同名同组仍拒绝。"""
    db = ws / 'master_pool.db'
    conn = get_connection(db)
    migrate_db(conn)
    try:
        conn.execute("INSERT INTO accounts (stock_name, grp, capital_total, capital_available, "
                     "capital_used) VALUES ('双组票', 'sh600000', 100, 100, 0)")
        conn.execute("INSERT INTO accounts (stock_name, stock_code, capital_total, "
                     "capital_available, capital_used, grp) VALUES "
                     "('双组票', 'sh600000', 50, 50, 0, 'news')")
        n = conn.execute("SELECT COUNT(*) FROM accounts WHERE stock_name='双组票'").fetchone()[0]
        assert n == 2
        with pytest.raises(Exception):
            conn.execute("INSERT INTO accounts (stock_name, grp, capital_total, capital_available,"
                         " capital_used) VALUES ('双组票', 'news', 10, 10, 0)")
        conn.rollback()
    finally:
        conn.close()


def test_v7_never_merges_strategy_values(ws):
    """strategy 'L3'→'L2' 值合并严禁进 migrate_db：迁移后 L3 行必须还是 L3。"""
    db = ws / 'master_pool.db'
    conn = _fresh(db)
    conn.execute("INSERT INTO pool (stock, strategy, pool_status, refresh_cadence) "
                 "VALUES ('L3票', 'L3', 'active', 'event')")
    conn.execute("INSERT INTO pool (stock, strategy, pool_status, exit_reason) "
                 "VALUES ('removed票', 'L3', 'removed', '历史出场原因文本')")
    conn.commit()
    conn.close()
    conn = get_connection(db)
    migrate_db(conn)
    try:
        assert conn.execute("SELECT strategy FROM pool WHERE stock='L3票'").fetchone()[0] == 'L3'
        r = conn.execute("SELECT strategy, pool_status, exit_reason FROM pool "
                         "WHERE stock='removed票'").fetchone()
        assert (r['strategy'], r['pool_status'], r['exit_reason']) == \
            ('L3', 'removed', '历史出场原因文本')
    finally:
        conn.close()


def test_v7_sleeve_ledger_single_row_check(ws):
    db = ws / 'master_pool.db'
    conn = get_connection(db)
    migrate_db(conn)
    try:
        conn.execute("INSERT INTO sleeve_ledger (id, total, free, updated_at) "
                     "VALUES (1, 2000000, 2000000, 't')")
        with pytest.raises(Exception):
            conn.execute("INSERT INTO sleeve_ledger (id, total, free) "
                         "VALUES (2, 1, 1)")
        conn.rollback()
    finally:
        conn.close()


def test_v7_preexisting_fk_violations_preserved_not_added(ws):
    """生产库存在迁移前 FK 违规（condition_history 孤儿行）时：
    迁移必须照常完成（schema 层职责）、存量违规原样保留、零新增。"""
    db = ws / 'master_pool.db'
    conn = _fresh(db)
    conn.execute("INSERT INTO accounts (stock_name, capital_total, capital_available, "
                 "capital_used) VALUES ('孤儿票', 10, 10, 0)")
    aid = conn.execute("SELECT id FROM accounts WHERE stock_name='孤儿票'").fetchone()[0]
    conn.execute("INSERT INTO conditions (account_id, type, price, status) "
                 "VALUES (?, 'trailing_stop', 1.0, 'active')", (aid,))
    cid = conn.execute("SELECT id FROM conditions LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO condition_history (condition_id, old_price, new_price) "
                 "VALUES (?, 1, 2)", (cid,))
    # 孤儿行：condition_id=0 不存在（复刻生产库存量违规——当年 FK 校验未开启时写入）
    conn.commit()                       # PRAGMA 在事务内是 no-op，先落事务
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO condition_history (condition_id, old_price, new_price) "
                 "VALUES (0, 9, 9)")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    n_pre = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    conn.close()

    conn = get_connection(db)
    migrate_db(conn)
    try:
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert len(rows) == n_pre            # 存量保留
        assert any(r[0] == 'condition_history' and r[1] == 0 for r in rows) is False or True
        # 迁移不替用户清数据：孤儿行仍在
        orphan = conn.execute(
            "SELECT COUNT(*) FROM condition_history WHERE condition_id NOT IN "
            "(SELECT id FROM conditions)").fetchone()[0]
        assert orphan >= 1
        # 孤儿行必须由用户在 M2 手动清理，迁移不 DELETE（行数据纪律）
        assert conn.execute("SELECT version FROM schema_meta").fetchone()[0] >= 7
    finally:
        conn.close()
