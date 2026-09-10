"""执行层 Phase 3 回归锁 · task-bus 侧（止盈腿扫描：涨破卖几何 / 影子不执行 / 逐票白名单）。

跑法（隔离；零生产库、零触网、零真实下单）：
    cd stock-toolkit/skills/task-bus/scripts && \
    <paper-trading venv>/bin/python3 -m pytest tests/test_v14_phase3.py -q

隔离装置复用 ``test_v14_phase2.py``（同一 DDL + env 逃生阀），只加止盈腿槽构造。
覆盖：
- ``tp:<code>#<leg>`` 槽的 code 取值（去腿号）与拍首预取（不预取 = 每拍取价失败 + 永不触发）。
- 几何：**现价 ≥ 止盈价才命中**（涨破卖）；现价低于止盈价 → 零输出零调用（写反即在此打红）。
- 影子期（shadow / 白名单外）：只留痕 + shadow_log(kind='tp_hit')，**零 ptrade2 调用**。
- 执行期（orders + 白名单内）：同拍直调 ``ptrade2 sell <名> --qty N --price P --event-id tp:<code>#N``。
- fail-closed：qty 不可判定 → 不调用，出行说明。
- 无 TTL：``order_ttl=NULL`` 不得出现 expired 行。
"""
import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_v14_phase2 import BIG, CODE, CODE2, STOCK, STOCK2, Iso, _scan, iso  # noqa: E402,F401

import watch_scan  # noqa: E402


class Iso3(Iso):
    """加一个止盈腿槽构造器（涨破卖几何：band=[触发价, 哨兵上沿]）。"""

    def tp_slot(self, code=CODE, leg=1, price=13.0, qty=100, status="pending_order",
                ttl=None, batch_id=20260910):
        key = f"tp:{code}#{leg}"
        c = sqlite3.connect(self.pool)
        c.execute(
            "INSERT OR REPLACE INTO event_slots (event_key, status, opened_at, budget, "
            "fill_status, band_min, band_max, anchor_price, order_ttl, band_out_count, "
            "placed_px, side, qty, group_key, batch_id, created_by, note) "
            "VALUES (?,?,'2026-09-10T09:31:00',0,'pending',?,?,?,?,0,?,'sell',?,?,?,"
            "'atr-auto',?)",
            (key, status, price, BIG, price, ttl, price, qty, f"{code}:tp", batch_id,
             f" [止盈腿#{leg}]"))
        c.commit()
        c.close()
        return key

    def tp_lines(self, out):
        return [l for l in out if "TP-ORDER" in l]


@pytest.fixture
def iso3(tmp_path):
    it = Iso3(tmp_path)
    keys = ("STOCK_ANALYSIS_WORKSPACE", "STOCK_TASKS_DB", "PTRADE2_EXEC_LAYER_FILE",
            "PTRADE2_TP_ORDERS", "PTRADE2_PROTECT_ORDERS")
    saved_env = {k: os.environ.get(k) for k in keys}
    saved = (watch_scan.WS, watch_scan.POOL_DB, watch_scan.TASKS_DB, dict(watch_scan._PRICE_CACHE))
    os.environ["STOCK_ANALYSIS_WORKSPACE"] = it.ws
    os.environ["STOCK_TASKS_DB"] = it.tasks
    os.environ["PTRADE2_EXEC_LAYER_FILE"] = os.path.join(it.ws, "exec_layer.json")
    os.environ.pop("PTRADE2_TP_ORDERS", None)
    os.environ.pop("PTRADE2_PROTECT_ORDERS", None)
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


def _cfg(ws, mode="orders", stocks=(STOCK,)):
    with open(os.path.join(ws, "exec_layer.json"), "w", encoding="utf-8") as f:
        json.dump({"tp_orders": {"mode": mode, "exec_stocks": list(stocks)}}, f)


# ------------------------------------------------------------------ 开关口径
def test_tp_mode_missing_file_is_off(iso3):
    assert watch_scan._tp_mode(STOCK) == "off"


def test_tp_mode_orders_only_for_whitelisted(iso3):
    _cfg(iso3.ws, "orders", (STOCK,))
    assert watch_scan._tp_mode(STOCK) == "orders"
    assert watch_scan._tp_mode(STOCK2) == "shadow", "白名单外必须降级 shadow（禁全局翻转）"


def test_tp_mode_env_escape_hatch(iso3, monkeypatch):
    _cfg(iso3.ws, "shadow")
    monkeypatch.setenv("PTRADE2_TP_ORDERS", "orders")
    assert watch_scan._tp_mode(STOCK) == "orders"


# ------------------------------------------------------------------ 几何（防写反）
def test_tp_slot_hits_only_on_break_up(iso3):
    """止盈腿是**涨破卖**：现价 12.0 < 止盈价 13.0 → 零输出零调用。"""
    _cfg(iso3.ws, "orders", (STOCK,))
    iso3.tp_slot(price=13.0, qty=100)
    calls = []
    out = _scan(calls, {CODE: 12.0})
    assert not iso3.tp_lines(out), f"未到止盈价不应有任何 TP-ORDER 行：{out}"
    assert calls == [], "未命中绝不许调用 ptrade2"


def test_tp_slot_hit_at_or_above_price(iso3):
    _cfg(iso3.ws, "orders", (STOCK,))
    iso3.tp_slot(price=13.0, qty=100)
    calls = []
    out = _scan(calls, {CODE: 13.0})          # 现价 = 止盈价（边界含）
    lines = iso3.tp_lines(out)
    assert lines, f"到价应命中：{out}"
    assert len(calls) == 1
    # 展示的"止盈价"必须取**带下沿**（涨破卖）——取上沿会渲染成哨兵极值 9.9e9，
    # 是几何写反的可见症状（变异实测：只改 line 取值此断言打红，其余用例全绿）
    assert "止盈价¥13.00" in lines[0], f"几何取错边：{lines[0]}"


# ------------------------------------------------------------------ 影子期铁律
def test_tp_shadow_mode_never_calls_ptrade2(iso3):
    _cfg(iso3.ws, "shadow")
    iso3.tp_slot(price=13.0, qty=100)
    calls = []
    out = _scan(calls, {CODE: 13.5})
    assert iso3.tp_lines(out), "影子期也必须出行（供对账）"
    assert "影子期" in "".join(iso3.tp_lines(out))
    assert calls == [], "影子期零 ptrade2 调用"
    assert "tp_hit" in iso3.shadow_kinds()


def test_tp_whitelist_outside_traces_only(iso3):
    _cfg(iso3.ws, "orders", (STOCK2,))        # 白名单里是别的票
    iso3.tp_slot(price=13.0, qty=100)
    calls = []
    out = _scan(calls, {CODE: 13.5})
    assert calls == [], "白名单外不得执行"
    assert "影子期" in "".join(iso3.tp_lines(out))


# ------------------------------------------------------------------ 执行期
def test_tp_orders_mode_calls_sell_with_slot_key(iso3):
    _cfg(iso3.ws, "orders", (STOCK,))
    iso3.tp_slot(price=13.0, qty=100)
    calls = []
    out = _scan(calls, {CODE: 13.5})
    assert len(calls) == 1
    assert calls[0] == ["sell", STOCK, "--qty", "100", "--price", "13.50",
                        "--event-id", "tp:sh600000#1"], calls[0]
    assert "tp_exec" in iso3.shadow_kinds()


def test_tp_fail_closed_when_qty_missing(iso3):
    _cfg(iso3.ws, "orders", (STOCK,))
    c = sqlite3.connect(iso3.pool)
    iso3.tp_slot(price=13.0, qty=100)
    c.execute("UPDATE event_slots SET qty=NULL WHERE event_key='tp:sh600000#1'")
    c.commit()
    c.close()
    calls = []
    out = _scan(calls, {CODE: 13.5})
    assert calls == [], "qty 不可判定必须 fail-closed（宁可不卖不卖错）"
    assert "执行被拒" in "".join(iso3.tp_lines(out))


# ------------------------------------------------------------------ TTL / 预取
def test_tp_slot_without_ttl_never_expires(iso3):
    _cfg(iso3.ws, "orders", (STOCK,))
    iso3.tp_slot(price=13.0, qty=100, ttl=None)
    out = _scan([], {CODE: 12.0})             # 未命中，看是否被判过期
    assert not [l for l in out if "expired" in l], f"无 TTL 不得过期：{out}"


def test_collect_price_scope_codes_includes_tp_legs(iso3):
    """止盈腿槽必须进拍首预取（否则每拍取价失败 → 幽灵唤醒 + 永不触发）。"""
    iso3.tp_slot(code=CODE, leg=1, price=13.0, qty=100)
    iso3.tp_slot(code=CODE, leg=2, price=15.0, qty=100)
    codes = watch_scan._collect_price_scope_codes()
    assert CODE in codes, f"止盈腿 code 未进预取：{sorted(codes)}"


# ------------------------------------------------- 非正价闸门（2026-09-10 实测暴露）
@pytest.mark.parametrize("bad", [0, 0.0, -1.5, "x", True])
def test_non_positive_price_fail_closed_for_protect(iso3, bad):
    """取价 0/负 → 兜底单不得"命中"。

    band=[0, 线] 的几何下 `band_min <= px` 恒成立：行情源给 0（占位值/解析失败/停牌兜底）
    会让**全部**保护单同时命中并按 ¥0 卖出（模拟实测：26 张同时触发）。
    """
    iso3.protect_slot(line=10.0, qty=300)     # 现价 0 会落在 band=[0,10] 内
    calls = []
    out = _scan(calls, {CODE: bad})
    assert calls == [], f"非正价 {bad!r} 不得触发任何 ptrade2 调用"
    assert not iso3.exec_lines(out), f"非正价不得出命中行：{out}"
    assert [l for l in out if "取价异常" in l], f"应出行说明（可诊断）：{out}"


@pytest.mark.parametrize("bad", [0, -3])
def test_non_positive_price_fail_closed_for_tp(iso3, bad):
    _cfg(iso3.ws, "orders", (STOCK,))
    iso3.tp_slot(price=13.0, qty=100)
    calls = []
    out = _scan(calls, {CODE: bad})
    assert calls == [] and not iso3.tp_lines(out)
