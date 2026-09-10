"""执行层 Phase 4 回归锁 · task-bus 侧（conditions 卖出口径收窄为**追踪器**）。

跑法（隔离；零生产库、零触网、零真实下单）：
    cd stock-toolkit/skills/task-bus/scripts && \
    <paper-trading venv>/bin/python3 -m pytest tests/test_v14_phase4.py -q

口径（2026-09-10 用户裁定：全部完成后统一审计）：
- 卖出执行**只由挂单承载**（protect:*/tp:*）；conditions 穿越只写事件 + 出行给 agent；
- 缺省 = tracker（不直调）；回滚 = exec_layer.json → conditions_sell.mode='executor'
  或 env PTRADE2_COND_SELL=executor；
- **买入类条件不受影响**（仍走 executor 直调路径）。
"""
import json
import os
import sqlite3
import sys
from unittest.mock import patch

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_v14_phase2 import CODE, STOCK, Iso, iso  # noqa: E402,F401
from test_v14_phase3 import Iso3  # noqa: E402  （止盈腿槽构造器 tp_slot 在 Iso3）

import watch_scan  # noqa: E402


class Iso4(Iso3):
    def __init__(self, root):
        super().__init__(root)
        # 生产 task_events 的列比 phase2 夹具多，且 status 需默认 'pending'
        # （_write_alert 只写 type/entity/source/priority/payload/creator，随后按
        #  status='pending' 回取事件 id——夹具里 status 为 NULL 会让所有用例走成
        #  "creator 为空" 的 fail-closed 分支）。
        c = sqlite3.connect(self.tasks)
        c.execute("DROP TABLE IF EXISTS task_events")
        c.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, "
                  "entity TEXT, source TEXT, priority INTEGER, payload TEXT, "
                  "status TEXT DEFAULT 'pending', creator TEXT, created_at TEXT, handled_at TEXT)")
        c.commit()
        c.close()
        # conditions 侧：_mark_triggered_family 要 modified_at + condition_history
        c = sqlite3.connect(self.pool)
        for col in ("modified_at", "created_by"):
            try:
                c.execute(f"ALTER TABLE conditions ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        c.execute("CREATE TABLE IF NOT EXISTS condition_history (id INTEGER PRIMARY KEY "
                  "AUTOINCREMENT, condition_id INTEGER, old_price REAL, new_price REAL, "
                  "reason TEXT, timestamp TEXT, level TEXT, override_triggers TEXT)")
        c.commit()
        c.close()

    def cond(self, ctype="cost_protection", price=10.0, action="亏损止损-清仓",
             name="止损", is_event=0, status="active", account_id=1):
        c = sqlite3.connect(self.pool)
        c.execute("INSERT INTO conditions (account_id, type, name, price, action, category, "
                  "status, is_event, created_by) VALUES (?,?,?,?,?,'hard',?,?,'atr-auto')",
                  (account_id, ctype, name, price, action, status, is_event))
        c.commit()
        c.close()


@pytest.fixture
def iso4(tmp_path):
    it = Iso4(tmp_path)
    keys = ("STOCK_ANALYSIS_WORKSPACE", "STOCK_TASKS_DB", "PTRADE2_EXEC_LAYER_FILE",
            "PTRADE2_COND_SELL")
    saved_env = {k: os.environ.get(k) for k in keys}
    saved = (watch_scan.WS, watch_scan.POOL_DB, watch_scan.TASKS_DB, dict(watch_scan._PRICE_CACHE))
    os.environ["STOCK_ANALYSIS_WORKSPACE"] = it.ws
    os.environ["STOCK_TASKS_DB"] = it.tasks
    os.environ["PTRADE2_EXEC_LAYER_FILE"] = os.path.join(it.ws, "exec_layer.json")
    os.environ.pop("PTRADE2_COND_SELL", None)
    watch_scan.WS = it.ws
    watch_scan.POOL_DB, watch_scan.TASKS_DB = it.pool, it.tasks
    watch_scan._PRICE_CACHE.clear()
    yield it
    watch_scan.WS = saved[0]
    watch_scan.POOL_DB, watch_scan.TASKS_DB = saved[1], saved[2]
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRICE_CACHE.update(saved[3])
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _trig(calls, px):
    def stub(*a, **k):
        calls.append([str(x) for x in a])
        return "✅ 已卖出"
    with patch.object(watch_scan, "in_trade_hours", lambda: True), \
         patch.object(watch_scan, "fetch_price", lambda code: px), \
         patch.object(watch_scan, "ptrade2", stub):
        return watch_scan.check_price_triggers()


def test_conditions_sell_mode_default_is_tracker(iso4):
    assert watch_scan._conditions_sell_mode() == "tracker", "缺省必须是追踪器（卖出只由挂单承载）"


def test_conditions_sell_mode_env_rollback(iso4, monkeypatch):
    monkeypatch.setenv("PTRADE2_COND_SELL", "executor")
    assert watch_scan._conditions_sell_mode() == "executor"


def test_sell_condition_hit_does_not_call_ptrade2(iso4):
    """跌破保护线：写事件 + 出行，**零 ptrade2 调用**（旧行为是 --all 直调清仓）。"""
    iso4.cond(ctype="cost_protection", price=10.0, action="亏损止损-清仓")
    calls = []
    out = _trig(calls, 9.5)                    # 现价 ≤ 条件价 → 命中
    assert calls == [], f"追踪器口径下不得直调卖出：{calls}"
    assert any("追踪器口径" in l for l in out), f"应出行说明：{out}"


def test_sell_condition_hit_writes_event(iso4):
    iso4.cond(ctype="trailing_stop", price=10.0, action="移动止损-清仓")
    _trig([], 9.0)
    c = sqlite3.connect(iso4.tasks)
    try:
        n = c.execute("SELECT COUNT(*) FROM task_events WHERE type='WATCH_ALERT' "
                      "AND entity=? AND status='pending'", (STOCK,)).fetchone()[0]
    finally:
        c.close()
    assert n >= 1, "命中必须留事件（追踪器=出事件，不执行）"


def test_sell_condition_executor_rollback_still_direct_calls(iso4, monkeypatch):
    """回滚开关：conditions_sell.mode=executor → 恢复同拍直调（--all）。"""
    monkeypatch.setenv("PTRADE2_COND_SELL", "executor")
    iso4.cond(ctype="cost_protection", price=10.0, action="亏损止损-清仓")
    calls = []
    _trig(calls, 9.5)
    assert len(calls) == 1 and calls[0][0] == "sell" and "--all" in calls[0], calls


def test_buy_condition_still_direct_calls(iso4):
    """买入类条件不受 Phase 4 影响（仍走 executor 路径）。"""
    iso4.cond(ctype="add_position", price=10.0, action="加仓买入", name="加仓")
    calls = []
    _trig(calls, 9.5)                           # 跌破型买入条件：现价 ≤ 条件价 → 命中
    assert len(calls) == 1 and calls[0][0] == "buy", calls


def test_config_file_controls_mode(iso4):
    with open(os.path.join(iso4.ws, "exec_layer.json"), "w", encoding="utf-8") as f:
        json.dump({"conditions_sell": {"mode": "executor"}}, f)
    assert watch_scan._conditions_sell_mode() == "executor"
    with open(os.path.join(iso4.ws, "exec_layer.json"), "w", encoding="utf-8") as f:
        json.dump({"conditions_sell": {"mode": "垃圾值"}}, f)
    assert watch_scan._conditions_sell_mode() == "tracker", "非法值必须回落 fail-closed 追踪器"


# ------------------------------------- 配置丢失的静默保护失效（Phase 4 补）
def test_missing_config_with_pending_slots_alarms(iso4):
    """exec_layer.json 读不到（mode=off）但库里有待命系统挂单 → 必须出声。

    不对称陷阱：挂单缺 key = off（只留痕不执行），conditions 侧缺同一份 key = tracker
    （也不直调）→ 两边都不卖，且旧实现 `mode=='off' → return []` 让这条链路完全静默。
    """
    iso4.protect_slot()
    iso4.tp_slot()
    with patch.object(watch_scan, "in_trade_hours", lambda: True):
        out = watch_scan.check_protect_freshness()
    assert any("PROTECT-CONFIG" in l for l in out), out


def test_no_config_and_no_slots_is_silent(iso4):
    with patch.object(watch_scan, "in_trade_hours", lambda: True):
        assert watch_scan.check_protect_freshness() == []
