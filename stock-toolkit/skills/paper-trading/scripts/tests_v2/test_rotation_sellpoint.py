"""_emit_rotation_exit 改写 watchpoint 后的功能验证（tmp 工作区，零生产库接触）。

跑法：paper-trading scripts/.venv/bin/python tests_v2/test_rotation_sellpoint.py
覆盖：
1. allocate --rotation-out → kv_store watch_points 挂 {mode:'sell', price=现价×0.99}
2. 现价 fetch 失败 → price=None 占位 + note 含'晨审补价'（入场不回滚）
3. 回归：test_v11_pool_model.py::test_a3 改造后语义（不再有 ROTATION_EXIT 事件）
"""
import json
import os
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from paper_trading_v2.master_pool import MasterPoolManager


class _PI:
    def __init__(self, p):
        self.current_price = p
        self.pre_close = p


def _mk_env(tmp):
    """tmp 工作区 + 10M 主池 + 换出票 open 段（成本 8.4M>2/3 门，逼 rotation 伴配）。"""
    db_path = os.path.join(tmp, 'master_pool.db')
    m = MasterPoolManager(db_path)
    m.init_pool(10_000_000)
    conn = m._conn()
    seg = conn.execute(
        "INSERT INTO position (stock, code, strategy, status, budget, topup_total, "
        "opened_at, cash, fifo_index, fifo_offset) VALUES ('换出甲','sh1','L1','open',"
        "3000000,0,'2026-09-01T09:00',0,-1,0)").lastrowid
    conn.execute("INSERT INTO trades (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost, timestamp, note) VALUES (?,0,'buy','sh1',840000,10.0,"
                 "8400000,'2026-09-01T10:00','fixture')", (seg,))
    conn.execute("UPDATE pool_ledger SET free=free-3000000 WHERE id=1")
    # 换入票 strategy=L1（走冷却/段位分支不影响），直配
    conn.commit()
    conn.close()
    return m


def test_rotation_out_writes_sell_watchpoint(tmp_path):
    tasks_db = os.path.join(str(tmp_path), 'tasks.db')
    os.environ['STOCK_TASKS_DB'] = tasks_db
    m = _mk_env(str(tmp_path))
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_PI(10.0)):
        m.allocate('换入票', 400_000, reason='轮换验证',
                   entry_mode='rotation', rotation_out='换出甲')
    # 任务库 kv_store：sell 点落库
    tconn = sqlite3.connect(tasks_db)
    row = tconn.execute("SELECT value FROM kv_store WHERE key='watch_points'").fetchone()
    tconn.close()
    assert row, "watch_points kv 未写"
    pts = json.loads(row[0])
    assert '换出甲' in pts, pts
    p = pts['换出甲'][0]
    assert p['mode'] == 'sell', p
    assert p['price'] == 9.9, f"price 应=现价10×0.99=9.9，实得 {p['price']}"
    assert '轮换出池换入换入票限价卖' in p['note'], p
    assert p['code'] == 'sh1'
    # 不再有 ROTATION_EXIT 事件
    tconn = sqlite3.connect(tasks_db)
    ev = tconn.execute("SELECT COUNT(*) FROM task_events WHERE type='ROTATION_EXIT'").fetchone()[0]
    tconn.close()
    assert ev == 0, f"ROTATION_EXIT 应绝迹，实得 {ev}"
    print(f"✅ sell watchpoint 已挂: {json.dumps(p, ensure_ascii=False)}")


def test_rotation_out_price_fetch_fail_degrades(tmp_path):
    tasks_db = os.path.join(str(tmp_path), 'tasks.db')
    os.environ['STOCK_TASKS_DB'] = tasks_db
    m = _mk_env(str(tmp_path))
    # fetch 失败（返回 None / 抛异常都测）→ price=None 占位 + 晨审补价 note，入场不回滚
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=None):
        m.allocate('换入票2', 400_000, reason='轮换降级验证',
                   entry_mode='rotation', rotation_out='换出甲')
    tconn = sqlite3.connect(tasks_db)
    row = tconn.execute("SELECT value FROM kv_store WHERE key='watch_points'").fetchone()
    ev = tconn.execute("SELECT COUNT(*) FROM task_events WHERE type='ROTATION_EXIT'").fetchone()[0]
    tconn.close()
    pts = json.loads(row[0])
    p = pts['换出甲'][0]
    assert p['mode'] == 'sell' and p['price'] is None, p
    assert '晨审补价' in p['note'], p
    assert ev == 0
    # 段照常入场（降级合同）
    conn = m._conn()
    seg = conn.execute("SELECT status FROM position WHERE stock='换入票2' AND status='open'").fetchone()
    conn.close()
    assert seg is not None, "fetch 失败不得阻断入场"
    print(f"✅ 降级路径: price=None 占位 + 晨审补价 note，入场不受阻")


def test_no_rotation_out_no_watchpoint(tmp_path):
    """normal 入场不挂卖出点（回归）。"""
    tasks_db = os.path.join(str(tmp_path), 'tasks.db')
    os.environ['STOCK_TASKS_DB'] = tasks_db
    m = _mk_env(str(tmp_path))
    # 直改 free 造 normal 放行空间（真实占用 <2/3 需成本 <6.67M——上面 fixture 8.4M 会拦）
    # 直接换：把换出段成本降为 5M（<2/3×10M）
    conn = m._conn()
    conn.execute("DELETE FROM trades WHERE stock_code='sh1'")
    conn.execute("INSERT INTO trades (account_id, seq, operation, stock_code, quantity, "
                 "price, total_cost, timestamp, note) SELECT id,0,'buy','sh1',500000,10.0,"
                 "5000000,'2026-09-01T10:00','fixture' FROM position WHERE stock='换出甲'")
    conn.execute("UPDATE pool_ledger SET free=free+3400000 WHERE id=1")
    conn.commit()
    conn.close()
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_PI(10.0)):
        m.allocate('普通票', 300_000, reason='普通入场')
    assert not os.path.exists(tasks_db), "normal 入场不应创建 watchpoint kv（零写任务库）"
    print("✅ normal 入场零 watchpoint（回归）")


if __name__ == '__main__':
    import shutil
    import tempfile
    fails = 0
    for fn in [test_rotation_out_writes_sell_watchpoint,
               test_rotation_out_price_fetch_fail_degrades,
               test_no_rotation_out_no_watchpoint]:
        d = tempfile.mkdtemp(prefix='rot_sell_')
        try:
            fn(d)
        except Exception as e:
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"❌ {fn.__name__}: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    raise SystemExit(1 if fails else 0)
