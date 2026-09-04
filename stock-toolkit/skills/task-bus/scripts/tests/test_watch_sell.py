"""check_watch_points sell 触发隔离测试（2026-09-04，ROTATION_EXIT 退役修复配套）。

跑法（隔离，零生产库接触——STOCK_TASKS_DB 指 /tmp 临时库）：
    cd stock-toolkit/skills/task-bus/scripts && python3 -m pytest tests/test_watch_sell.py -v
或无 pytest 时：python3 tests/test_watch_sell.py（内建 main 直跑）。

策略：mock watch_scan.fetch_price（E7 唯一取价入口）+ pool_stocks/in_trade_hours，
直接调 check_watch_points 断言：sell 单值/区间触发方向、buy/eval 回归不变、
触发即移除、WATCH_ALERT payload 契约（mode/direction）、CLI --mode sell 冒烟。
"""
import json
import os
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watch_scan


class T:
    """临时任务库（STOCK_TASKS_DB 隔离），每用例重建。"""

    def __init__(self, tmp):
        self.db = os.path.join(str(tmp), "tasks.db")
        os.makedirs(str(tmp), exist_ok=True)
        watch_scan.TASKS_DB = self.db          # watch_scan 读模块级 TASKS_DB
        watch_scan._ensure_task_table()
        watch_scan._PRICE_CACHE.clear()

    def kv(self, points: dict):
        watch_scan._kv_set("watch_points", points)

    def kv_now(self) -> dict:
        return watch_scan._kv_get("watch_points")

    def alerts(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM task_events WHERE type='WATCH_ALERT' ORDER BY id")]
        finally:
            conn.close()


def _set_price(mapping):
    """mock 取价三件套（fetch_price=E7 唯一取价入口）。"""
    watch_scan._PRICE_CACHE.clear()
    patch.object(watch_scan, "fetch_price", lambda code: mapping.get(code)).start()
    patch.object(watch_scan, "pool_stocks", lambda: []).start()
    patch.object(watch_scan, "in_trade_hours", lambda: True).start()


# ---------- sell：单值触发（现价 ≥ price） ----------

def test_sell_single_triggers_at_or_above(tmp_path):
    t = T(tmp_path)
    t.kv({"换出甲": [{"code": "sh600000", "price": 12.0, "note": "轮换出池限价卖",
                      "mode": "sell", "amount": None, "min": None, "added_at": "09-04 10:00"}]})
    _set_price({"sh600000": 12.30})          # ≥ 12.0 → 触发
    alerts = check(t)
    assert len(alerts) == 1 and "卖出点触发" in alerts[0] and "换出甲" in alerts[0], alerts
    ev = t.alerts()
    assert len(ev) == 1, f"WATCH_ALERT 应恰好 1 条，实得 {len(ev)}"
    p = json.loads(ev[0]["payload"])
    assert p["mode"] == "sell" and p["direction"] == "sell", p
    assert p["trigger_price"] == 12.0 and p["current_price"] == 12.30, p
    assert "换出甲" not in t.kv_now(), "触发后 watchpoint 应被移除（触发即失效）"
    # 现价低于触发价 → 不触发不移除
    t.kv({"换出乙": [{"code": "sh600001", "price": 12.0, "note": "", "mode": "sell",
                      "amount": None, "min": None, "added_at": "09-04 10:00"}]})
    _set_price({"sh600001": 11.99})
    assert check(t) == []
    assert "换出乙" in t.kv_now()
    # 边界：现价 == 触发价 → 触发（≥ 语义）
    t.kv({"换出丙": [{"code": "sh600002", "price": 12.0, "note": "", "mode": "sell",
                      "amount": None, "min": None, "added_at": "09-04 10:00"}]})
    _set_price({"sh600002": 12.0})
    assert len(check(t)) == 1, "现价==触发价应触发（≥）"


# ---------- sell：区间（price=下沿触发价，min=上沿封顶，须 price < min） ----------

def test_sell_range_band(tmp_path):
    t = T(tmp_path)
    t.kv({"带内卖": [{"code": "sz000001", "price": 12.0, "note": "带内限价卖",
                      "mode": "sell", "amount": None, "min": 12.5, "added_at": "09-04 10:00"}]})
    _set_price({"sz000001": 12.2})           # 12.0 ≤ 12.2 ≤ 12.5 → 触发
    alerts = check(t)
    assert len(alerts) == 1 and "带内" in alerts[0], alerts
    p = json.loads(t.alerts()[0]["payload"])
    assert p["mode"] == "sell" and p["direction"] == "sell"
    # 高于上沿封顶（12.6 > min=12.5）→ 不触发（防追价脱靶）
    t.kv({"带外高": [{"code": "sz000002", "price": 12.0, "note": "", "mode": "sell",
                      "amount": None, "min": 12.5, "added_at": "09-04 10:00"}]})
    _set_price({"sz000002": 12.6})
    assert check(t) == [] and "带外高" in t.kv_now()
    # 低于下沿（11.9 < price=12.0）→ 不触发
    t.kv({"带外低": [{"code": "sz000003", "price": 12.0, "note": "", "mode": "sell",
                      "amount": None, "min": 12.5, "added_at": "09-04 10:00"}]})
    _set_price({"sz000003": 11.9})
    assert check(t) == []


# ---------- 回归：buy/eval 行为逐字节不变 ----------

def test_buy_eval_regression(tmp_path):
    t = T(tmp_path)
    t.kv({
        "观察股": [{"code": "sh600100", "price": 24.0, "note": "买点下沿", "mode": "eval",
                    "amount": None, "min": None, "added_at": "09-04 10:00"}],
        "建仓股": [{"code": "sh600101", "price": 24.5, "note": "建仓10%", "mode": "buy",
                    "amount": 200000, "min": None, "added_at": "09-04 10:00"}],
        "区间股": [{"code": "sh600102", "price": 135.0, "note": "区间建仓", "mode": "buy",
                    "amount": 200000, "min": 130.0, "added_at": "09-04 10:00"}],
    })
    _set_price({"sh600100": 23.9, "sh600101": 24.5, "sh600102": 132.0})
    alerts = check(t)
    assert len(alerts) == 3, f"buy/eval 回归：应 3 触发，实得 {len(alerts)}: {alerts}"
    evs = {e["entity"]: json.loads(e["payload"]) for e in t.alerts()}
    assert (evs["观察股"]["mode"], evs["观察股"]["direction"]) == ("eval", "eval")
    assert (evs["建仓股"]["mode"], evs["建仓股"]["direction"]) == ("buy", "buy")
    assert evs["建仓股"]["budget"] == 200000
    # buy 区间语义不变：min ≤ 现价 ≤ price（130 ≤ 132 ≤ 135 命中；<130 或 >135 不触发）
    kv = t.kv_now()
    assert "观察股" not in kv and "建仓股" not in kv and "区间股" not in kv, "触发即移除（回归）"


def test_buy_eval_direction_unchanged_nonhit(tmp_path):
    """回归锁·非命中侧：buy/eval 现价 > price 不触发（sell 方向相反的证据）。"""
    t = T(tmp_path)
    t.kv({
        "不触发观察": [{"code": "sh600110", "price": 24.0, "note": "", "mode": "eval",
                        "amount": None, "min": None, "added_at": "09-04 10:00"}],
        "不触发建仓": [{"code": "sh600111", "price": 24.5, "note": "", "mode": "buy",
                        "amount": 200000, "min": None, "added_at": "09-04 10:00"}],
    })
    _set_price({"sh600110": 24.1, "sh600111": 24.6})   # 均 > price → 不触发
    assert check(t) == []
    assert set(t.kv_now()) == {"不触发观察", "不触发建仓"}


# ---------- 混合：同实体 sell 触发不影响未触发的相邻点 ----------

def test_sell_and_eval_coexist(tmp_path):
    """同实体多价格点：第一个未命中点不消费后续点（for-break 语义，回归自 legacy）。"""
    t = T(tmp_path)
    t.kv({"双点股": [
        {"code": "sh600200", "price": 15.0, "note": "回踩复检", "mode": "eval",
         "amount": None, "min": None, "added_at": "09-04 10:00"},
        {"code": "sh600200", "price": 20.0, "note": "轮换出池限价卖", "mode": "sell",
         "amount": None, "min": None, "added_at": "09-04 10:00"},
    ]})
    _set_price({"sh600200": 20.5})
    # legacy 语义：逐点连检，命中即 break；未命中继续下一 p（sys.settrace 已核）。
    # eval(15.0) 未命中 → 继续检 sell(20.0) → 命中触发。pop 是整实体移除——
    # 同实体的未触发 eval 点随触发一并消失（与 buy/eval 触发时行为一致，回归）。
    alerts = check(t)
    assert len(alerts) == 1 and "卖出点触发" in alerts[0], alerts
    assert "双点股" not in t.kv_now(), "sell 触发同 buy/eval：整实体 pop（触发即失效）"
    ev = t.alerts()
    assert json.loads(ev[0]["payload"])["direction"] == "sell"


# ---------- CLI 冒烟（typer CliRunner，/tmp 库） ----------

def test_cli_watchpoint_sell_smoke(tmp_path):
    watch_scan.TASKS_DB = os.path.join(str(tmp_path), "cli_tasks.db")
    import task_bus.db as tdb
    tdb.ENV = "STOCK_TASKS_DB"
    os.environ["STOCK_TASKS_DB"] = os.path.join(str(tmp_path), "cli_tasks.db")
    from typer.testing import CliRunner
    from task_bus.cli import app
    runner = CliRunner()
    r = runner.invoke(app, ["watchpoint", "add", "冒烟股", "--price", "12.0",
                            "--mode", "sell", "--code", "sh600871",
                            "--note", "轮换出池限价卖"])
    assert r.exit_code == 0, r.output
    assert "卖出" in r.output and "现价 ≥ 触发时" in r.output, r.output
    # 非法 mode 被拒
    r2 = runner.invoke(app, ["watchpoint", "add", "冒烟股", "--price", "12.0", "--mode", "foo"])
    assert r2.exit_code == 1 and "eval / buy / sell" in r2.output, r2.output
    # sell 区间校验：min 必须 > price（price=下沿、min=上沿）
    r3 = runner.invoke(app, ["watchpoint", "add", "冒烟股", "--price", "12.5",
                             "--min", "12.0", "--mode", "sell"])
    assert r3.exit_code == 1 and "0 < price < min" in r3.output, r3.output
    # list 展示 sell 标签与带内区间
    runner.invoke(app, ["watchpoint", "add", "带内股", "--price", "12.0",
                        "--min", "12.5", "--mode", "sell", "--code", "sh600872"])
    r4 = runner.invoke(app, ["watchpoint", "list"])
    assert r4.exit_code == 0 and "💰卖" in r4.output and "带内卖出" in r4.output, r4.output
    # kv 落库核验（/tmp 库，非生产）
    points = tdb.kv_get("watch_points")
    assert points["冒烟股"][0]["mode"] == "sell" and points["冒烟股"][0]["price"] == 12.0
    assert points["带内股"][0]["min"] == 12.5


def check(t):
    return watch_scan.check_watch_points()


if __name__ == "__main__":
    import shutil
    import tempfile

    failures = 0
    for name, fn in sorted((n, f) for n, f in list(globals().items())
                           if n.startswith("test_") and callable(f)):
        d = tempfile.mkdtemp(prefix="watch_sell_")
        try:
            fn(d)
            print(f"✅ {name}")
        except Exception as e:
            failures += 1
            print(f"❌ {name}: {e}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    if failures:
        raise SystemExit(f"{failures} 个用例失败")
    print("ALL PASS")
