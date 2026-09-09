"""触发即失效标记精确化测试（2026-09-09 晚审 TRIGGERED-STALE 根因修复）。

旧行为：任一卖出 WATCH_ALERT 把该段**全部** active 硬条件标 triggered（含现价根本
没碰到的止盈阶梯线）→ sync_take_profit_ladder 幂等跳过 → 阶梯永久失效。
新行为：清仓类整段全标（旧语义），其余只标"本次价格已突破的线"+本次触发线，并写
modified_at + condition_history 留痕。

跑法：cd stock-toolkit/skills/task-bus/scripts && python3 -m pytest tests/test_trigger_family.py -v
（POOL_DB 指 /tmp 临时库，零生产库接触）
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watch_scan  # noqa: E402

DDL = [
    """CREATE TABLE conditions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, cond_key TEXT,
        is_event INTEGER DEFAULT 0, cond_uid TEXT, type TEXT, name TEXT, price REAL,
        action TEXT, category TEXT, expiry_date TEXT, status TEXT,
        auto_link_cost INTEGER DEFAULT 0, peak_price REAL, created_at TEXT,
        modified_at TEXT, seq INTEGER)""",
    """CREATE TABLE condition_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, condition_id INTEGER, old_price REAL,
        new_price REAL, reason TEXT, timestamp TEXT, level TEXT, override_triggers TEXT)""",
]

# 恒申 9/4 14:53 重建后的条件集（全部 active），触发线=移动止损 7.60
ROWS = [
    (21, 'take_profit_2', '止盈条件2', 12.0, '次日卖出1/3（收盘确认触发）', 'hard'),
    (21, 'take_profit_1', '止盈条件1', 10.4, '次日卖出1/3（收盘确认触发）', 'hard'),
    (21, 'cost_protection', '成本保护', 7.88, '成本保护-5%', 'hard'),
    (21, 'trailing_stop', '移动止损(9/4重建)', 7.6, '亏损预警档-5%→减仓20%', 'hard'),
    (21, 'trailing_stop', '亏损保护', 7.36, '亏损止损档-8%→减仓50%', 'hard'),
    (21, 'trailing_stop', '亏损保护', 6.8, '亏损清仓档-15%→清仓(兜底)', 'hard'),
]


def _mk_pool(tmp_path, rows=ROWS):
    db = os.path.join(str(tmp_path), 'master_pool.db')
    c = sqlite3.connect(db)
    for d in DDL:
        c.execute(d)
    for i, (aid, typ, name, price, action, cat) in enumerate(rows):
        c.execute("INSERT INTO conditions (account_id, type, name, price, action, category, "
                  "status, created_at, modified_at, seq) VALUES (?,?,?,?,?,?,'active',"
                  "'2026-09-04T14:53:40','2026-09-04T14:53:40',?)",
                  (aid, typ, name, price, action, cat, i))
    c.commit()
    c.close()
    watch_scan.POOL_DB = db
    return db


def _states(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        return {r['name'] + '|' + str(r['price']): r['status']
                for r in c.execute("SELECT name, price, status FROM conditions")}
    finally:
        c.close()


def _trig_id(db, price):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT id FROM conditions WHERE price=?", (price,)).fetchone()[0]
    finally:
        c.close()


def test_partial_exit_marks_only_breached_lines(tmp_path):
    db = _mk_pool(tmp_path)
    watch_scan._mark_triggered_family(_trig_id(db, 7.6), '移动止损(9/4重建)', 'sell', 7.54, False)
    st = _states(db)
    assert st['移动止损(9/4重建)|7.6'] == 'triggered'      # 触发线
    assert st['成本保护|7.88'] == 'triggered'              # 现价 7.54 已跌破
    assert st['止盈条件1|10.4'] == 'active'                # 未触及 → 阶梯保住（旧版被误标）
    assert st['止盈条件2|12.0'] == 'active'
    assert st['亏损保护|7.36'] == 'active'
    assert st['亏损保护|6.8'] == 'active'


def test_partial_exit_writes_history_and_modified_at(tmp_path):
    db = _mk_pool(tmp_path)
    watch_scan._mark_triggered_family(_trig_id(db, 7.6), '移动止损(9/4重建)', 'sell', 7.54, False)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        hist = [dict(r) for r in c.execute("SELECT * FROM condition_history")]
        assert len(hist) == 2, hist
        assert all(h['reason'].startswith('触发即失效') for h in hist)
        assert all(h['level'] == 'auto' for h in hist)
        mods = [r[0] for r in c.execute(
            "SELECT modified_at FROM conditions WHERE status='triggered'")]
        assert all(m and m > '2026-09-09' for m in mods), mods
    finally:
        c.close()


def test_full_exit_marks_everything(tmp_path):
    """清仓类触发保留旧语义：整段全标（仓位将归零，防重复下单）。"""
    rows = list(ROWS) + [(21, 'trailing_stop', '清仓', 6.5, '亏损清仓档-15%→清仓', 'hard')]
    db = _mk_pool(tmp_path, rows)
    watch_scan._mark_triggered_family(_trig_id(db, 6.5), '清仓', 'sell', 6.4, False)
    st = _states(db)
    assert all(v == 'triggered' for v in st.values()), st


def test_tp_only_marks_single_line(tmp_path):
    db = _mk_pool(tmp_path)
    watch_scan._mark_triggered_family(_trig_id(db, 10.4), '止盈条件1', 'sell', 10.5, True)
    st = _states(db)
    assert st['止盈条件1|10.4'] == 'triggered'
    assert st['止盈条件2|12.0'] == 'active'
    assert st['成本保护|7.88'] == 'active'
    assert st['移动止损(9/4重建)|7.6'] == 'active'


def test_buy_direction_keeps_old_filter(tmp_path):
    rows = [(21, 'trailing_stop', '建仓点A', 7.0, '建仓点下沿-建仓30%', 'hard'),
            (21, 'trailing_stop', '建仓点B', 6.5, '建仓点中沿-建仓20%', 'hard'),
            (21, 'cost_protection', '成本保护', 7.88, '成本保护', 'hard')]
    db = _mk_pool(tmp_path, rows)
    watch_scan._mark_triggered_family(_trig_id(db, 7.0), '建仓点A', 'buy', 7.0, False)
    st = _states(db)
    assert st['建仓点A|7.0'] == 'triggered'
    assert st['建仓点B|6.5'] == 'triggered'      # buy 过滤按 action 关键词（旧语义）
    assert st['成本保护|7.88'] == 'active'


def test_missing_price_degrades_to_single(tmp_path, capsys):
    db = _mk_pool(tmp_path)
    watch_scan._mark_triggered_family(_trig_id(db, 7.6), '移动止损(9/4重建)', 'sell', None, False)
    st = _states(db)
    assert st['移动止损(9/4重建)|7.6'] == 'triggered'
    assert st['成本保护|7.88'] == 'active'       # 降级只标本条
    assert '降级' in capsys.readouterr().err


def test_unknown_cond_id_is_noop(tmp_path):
    db = _mk_pool(tmp_path)
    watch_scan._mark_triggered_family(999999, '不存在', 'sell', 7.5, False)
    assert all(v == 'active' for v in _states(db).values())
