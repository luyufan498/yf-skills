"""任务书 F1 检验 ① 回归锁：幂等键注入 kill 测试（防双卖）。

跑法（唯一可用解释器 = paper-trading venv）：
    cd stock-toolkit/skills/paper-trading/scripts && .venv/bin/python3 -m pytest tests_v2/test_v13_checks.py -q

场景（任务书 F1 ①）：`sell_stock(stock, qty, event_id=E)` 执行到"已写 trades 但事件未标记"
时进程被杀（真子进程 + os._exit(1)）→ 父进程重放（模拟 recover 重试）同一 event_id=E。
断言：trades 只有 1 行该 event_id；第二次调用被拒（ValueError）；audit 有 idempotent_reject
留痕；事件（task_events）仍未被标记（pending）。附加：event_id=''（空键）保持旧行为（允许重复）。

隔离：tmp workspace（ws fixture）+ 子进程 env 传 STOCK_ANALYSIS_WORKSPACE/STOCK_TASKS_DB，
行情打桩、零触网、生产库零写入。
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest  # noqa: E402


CHILD_SCRIPT = r'''
import os, sys, sqlite3, json
from datetime import datetime
from unittest.mock import patch

from paper_trading_v2.trading import PaperTrader
from paper_trading_v2.models import Account, CapitalPool, StockInfo

WS = os.environ["STOCK_ANALYSIS_WORKSPACE"]
TASKS = os.environ["STOCK_TASKS_DB"]

# 驱动事件（未标记）：模拟 C1 直调时事件已写入、exec 结果尚未回写
conn = sqlite3.connect(TASKS)
conn.executescript("""
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 3,
    source TEXT, entity TEXT, payload TEXT, creator TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    claimed_at TEXT, done_at TEXT, note TEXT, handled_at TEXT, handled_by TEXT);
CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
""")
cur = conn.execute("INSERT INTO task_events (type, entity, source, priority, payload, creator) "
                   "VALUES ('WATCH_ALERT', 'kill股', 'heartbeat-scan', 1, ?, 'analysis-watch')",
                   (json.dumps({"mode": "trade", "direction": "sell",
                                "exec": {"result": "pending", "code": None}}),))
ev_id = cur.lastrowid
conn.commit()
conn.close()

trader = PaperTrader()
trader.storage.save_account(Account(
    stock_name="kill股", stock_code="sh600100",
    capital_pool=CapitalPool(total=500000.0, available=500000.0, used=0.0)))

now = datetime.now()
quote = StockInfo(code="sh600100", name="kill股", current_price=100.0, pre_close=100.0,
                  high=100.5, low=99.5, volume="100000",
                  date=now.strftime("%Y-%m-%d"), time=now.strftime("%H:%M:%S"),
                  source="tencent")

with patch("paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price",
           return_value=quote), \
     patch("paper_trading_v2.trading.PaperTrader._sync_conditions_after_buy",
           lambda self, *a, **k: None):
    trader.buy_stock("kill股", quantity=1000)
    trader.sell_stock("kill股", quantity=400, event_id="E-kill-1")
# ---- 已写 trades，但事件未标记（exec 未回写）→ 进程被杀（硬退出，不清理）----
os._exit(1)
'''


def _db(ws):
    return ws / 'master_pool.db'


def _conn(ws):
    c = sqlite3.connect(str(_db(ws)))
    c.row_factory = sqlite3.Row
    return c


def _quote(px=100.0):
    from paper_trading_v2.models import StockInfo
    now = datetime.now()
    return StockInfo(code='sh600100', name='kill股', current_price=px, pre_close=100.0,
                     high=px + 0.5, low=px - 0.5, volume='100000',
                     date=now.strftime('%Y-%m-%d'), time=now.strftime('%H:%M:%S'),
                     source='tencent')


def test_check1_kill_then_replay_never_double_sells(ws):
    """真子进程写完 trades 后 os._exit(1)；重放同 event_id 必须被拒（防双卖）。"""
    from paper_trading_v2.trading import PaperTrader

    env = dict(os.environ)
    env.update({
        'STOCK_ANALYSIS_WORKSPACE': str(ws),
        'STOCK_TASKS_DB': str(ws / 'tasks.db'),
        'PTRADE2_ALLOW_CODE_MISMATCH': '1',
    })
    r = subprocess.run([sys.executable, '-c', CHILD_SCRIPT], capture_output=True,
                       text=True, env=env, timeout=180)
    assert r.returncode == 1, f"子进程应被 kill（exit 1），实得 {r.returncode}: {r.stderr[-400:]}"

    # 前置：trades 已有该 event_id 的成交行（这就是"已写 trades"）
    conn = _conn(ws)
    n_before = conn.execute("SELECT COUNT(*) FROM trades WHERE event_id='E-kill-1'").fetchone()[0]
    conn.close()
    assert n_before == 1, f"kill 前应已落 1 行 trades，实得 {n_before}"

    # 重放（模拟 recover 重试 / agent 重启后重发同请求）
    trader = PaperTrader()
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote()):
        with pytest.raises(ValueError, match='已成交|幂等'):
            trader.sell_stock('kill股', quantity=400, event_id='E-kill-1')

    conn = _conn(ws)
    n_after = conn.execute("SELECT COUNT(*) FROM trades WHERE event_id='E-kill-1'").fetchone()[0]
    audit = conn.execute(
        "SELECT COUNT(*) FROM audit WHERE action='idempotent_reject' "
        "AND reason LIKE '%E-kill-1%'").fetchone()[0]
    conn.close()
    assert n_after == 1, f"同 event_id 不得双卖，实得 {n_after} 行"
    assert audit == 1, "拒绝必须 audit 留痕（idempotent_reject）"

    # 驱动事件仍未被标记（执行方被 kill，exec 未回写；handled_at 是消费方标记同样为空）
    tconn = sqlite3.connect(str(ws / 'tasks.db'))
    tconn.row_factory = sqlite3.Row
    ev = tconn.execute("SELECT status, handled_at, payload FROM task_events "
                       "WHERE type='WATCH_ALERT' ORDER BY id DESC LIMIT 1").fetchone()
    tconn.close()
    assert ev is not None, "驱动事件应在（kill 前已写入）"
    assert ev['status'] == 'pending', f"事件应保持未标记（pending），实得 {ev['status']}"
    assert ev['handled_at'] is None, "执行方不得写 handled_at"


def test_check1_empty_event_id_keeps_legacy_behavior(ws):
    """event_id=''（空键）不启用幂等查重：同请求重复执行不被拒（旧行为零变化）。"""
    from paper_trading_v2.trading import PaperTrader
    from paper_trading_v2.models import Account, CapitalPool

    trader = PaperTrader()
    trader.storage.save_account(Account(
        stock_name='空键股', stock_code='sh600100',
        capital_pool=CapitalPool(total=500000.0, available=500000.0, used=0.0)))
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote()), \
         patch('paper_trading_v2.trading.PaperTrader._sync_conditions_after_buy',
               lambda self, *a, **k: None):
        trader.buy_stock('空键股', quantity=100, event_id='')
        trader.buy_stock('空键股', quantity=100, event_id='')
    conn = _conn(ws)
    seg = conn.execute("SELECT id FROM position WHERE stock='空键股' ORDER BY id DESC "
                       "LIMIT 1").fetchone()[0]
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=? AND operation='buy'",
                     (seg,)).fetchone()[0]
    audit = conn.execute("SELECT COUNT(*) FROM audit WHERE action='idempotent_reject' "
                         "AND stock='空键股'").fetchone()[0]
    conn.close()
    assert n == 2, f"空键应允许重复成交（不误伤），实得 {n} 行"
    assert audit == 0, "空键不得触发幂等拒绝留痕"
