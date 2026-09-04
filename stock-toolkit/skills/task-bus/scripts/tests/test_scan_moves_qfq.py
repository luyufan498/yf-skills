"""scan_moves E10 除权折算隔离测试（2026-09-04，raw→qfq 等效消除除权跳空假摔）。

跑法（隔离，零生产库接触——事件表/收盘源全 mock，STOCK_TASKS_DB 指 /tmp 临时库）：
    cd stock-toolkit/skills/task-bus/scripts && python3 -m pytest tests/test_scan_moves_qfq.py -v
或无 pytest 时：python3 tests/test_scan_moves_qfq.py（内建 main 直跑）。

公式（见 watch_scan._qfq_equivalent_closes docstring 推导）：任意 bar 的 qfq 等效价
= raw(bar) / ∏factor(e ∈ (bar_d, today])；判定 today/(ten_ago 折算后)-1 == qfq 动量。
覆盖：跨除权窗口等效值、多除权连乘、无事件逐字透传、首事件退化 fail-closed
（跳过该票不动状态机）、首事件在窗口外正常折算、事件表读失败 fail-open（=P3 行为）、
输出文案逐字不变（sweet 阈值文案字节锁）。
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watch_scan

# 语义参照值：sh600000 2026-07-16 10派4.2 真实除权 factor（生产库实测）
F = 1.04724409


def _isolated(tmp, pairs, events, today="2026-09-04", stocks=(("折算股", "sh600000"),),
              price=9.25, pre_close=9.20):
    """隔离环境：/tmp 临时任务库 + mock 池/时段/批量价；closes 源=真 _qfq_equivalent_closes
    吃合成 pairs，事件表 mock 掉 paper_trading_v2.market_cache.read_exright_events
    （零生产库接触）。返回 patcher 列表供 stop。"""
    watch_scan.TASKS_DB = os.path.join(str(tmp), "tasks.db")
    watch_scan._ensure_task_table()
    watch_scan._PRICE_CACHE.clear()
    watch_scan._PRE_CLOSE_CACHE.clear()
    started = [
        patch.object(watch_scan, "pool_stocks", lambda: list(stocks)),
        patch.object(watch_scan, "in_trade_hours", lambda: True),
        patch.object(watch_scan, "fetch_price", lambda code: price),
        patch.object(watch_scan, "fetch_pre_close", lambda code: pre_close),
        patch.object(watch_scan, "_fetch_cached_closes",
                     lambda code: watch_scan._qfq_equivalent_closes(code, pairs, today)),
    ]
    import paper_trading_v2.market_cache as _pmc
    started.append(patch.object(_pmc, "read_exright_events", lambda code: list(events)))
    for p in started:
        p.start()
    return started


# ---------- 1. 跨除权窗口：ten_chg 等效 qfq 动量 ----------

def test_cross_exright_window_qfq_equivalent(tmp_path):
    # 11 个已收盘 bar（升序），除权日 07-16 落在 10 日窗口内：
    # raw ten_ago=9.05（07-09）被当 -13.6% 假摔 → 折算后应为 +7.04% 动量
    pairs = [("2026-07-08", 9.00), ("2026-07-09", 9.05), ("2026-07-10", 9.06),
             ("2026-07-13", 9.19), ("2026-07-14", 9.16), ("2026-07-15", 9.31),
             ("2026-07-16", 8.85), ("2026-07-17", 8.87), ("2026-07-20", 9.14),
             ("2026-07-21", 9.20), ("2026-07-22", 9.25)]
    events = [{"date": "2026-07-16", "factor": F,
               "note": f"ratio 0.954887 -> 1.000000; jump {F:.8f}; FHcontent=10派4.2元"}]
    _isolated(tmp_path, pairs, events)
    closes = watch_scan._fetch_cached_closes("sh600000")
    # 除权前 bar 全部 /F，除权日及之后保持原值
    assert abs(closes[0] - 9.00 / F) < 1e-9 and abs(closes[5] - 9.31 / F) < 1e-9
    assert closes[6:] == [8.85, 8.87, 9.14, 9.20, 9.25]
    # scan_moves 端到端：ten_ago=closes[-10]=9.05/F，today=9.25（实时价）
    alerts = watch_scan.scan_moves()
    ten_chg = round((9.25 / (9.05 / F) - 1) * 100, 2)
    assert ten_chg == 7.04, ten_chg   # 等效 qfq 动量（raw 口径为 2.21%）
    assert alerts == []               # normal→normal 无状态跃迁不输出


def test_multi_exright_chain(tmp_path):
    # 两次除权连乘：f1@07-03、f2@07-16；11 个 bar、稀疏排布保证 closes[-10]=07-01
    # （除权前），07-03 当日 bar 已按除权后价成交（只除 f2），f2 后透传。
    pairs = [("2026-07-01", 10.00), ("2026-07-02", 10.10), ("2026-07-03", 10.20),
             ("2026-07-06", 10.30), ("2026-07-10", 10.40), ("2026-07-15", 10.50),
             ("2026-07-16", 10.60), ("2026-07-17", 10.70), ("2026-07-20", 10.80),
             ("2026-07-21", 10.90), ("2026-07-22", 11.00)]
    f1, f2 = 1.20, 1.04724409
    events = [{"date": "2026-07-03", "factor": f1, "note": "jump 1.20000000"},
              {"date": "2026-07-16", "factor": f2, "note": "jump 1.04724409"}]
    _isolated(tmp_path, pairs, events)
    closes = watch_scan._fetch_cached_closes("sh600000")
    # 折算规则（真股实证 sh600000 07-16：raw=qfq=8.85，除权日当日 bar 不除自身
    # factor——除权效应已烘焙在当日 raw 价跳水里；只除"严格晚于该 bar"的事件链）：
    assert abs(closes[0] - 10.00 / (f1 * f2)) < 1e-9   # 早于 f1、f2 → 除 f1*f2
    assert abs(closes[1] - 10.10 / (f1 * f2)) < 1e-9
    assert abs(closes[2] - 10.20 / f2) < 1e-9          # 07-03 当日 bar：不除 f1 自身
    assert abs(closes[3] - 10.30 / f2) < 1e-9          # f1 后 f2 前：只除 f2
    assert closes[6:] == [10.60, 10.70, 10.80, 10.90, 11.00]   # f2 后透传


def test_no_event_passthrough_regression(tmp_path):
    """无事件：折算=逐字透传（P3 既有行为回归，含 round 防浮点语义）。"""
    pairs = [("2026-08-20", 9.11), ("2026-08-21", 9.05), ("2026-08-24", 9.22),
             ("2026-08-25", 9.08), ("2026-08-26", 9.21), ("2026-08-27", 9.07),
             ("2026-08-28", 9.00), ("2026-08-31", 9.16), ("2026-09-01", 9.35),
             ("2026-09-02", 9.28), ("2026-09-03", 9.27)]
    _isolated(tmp_path, pairs, [])
    closes = watch_scan._fetch_cached_closes("sh600000")
    assert closes == [c for _, c in pairs]
    # scan_moves ten_chg 与改造前公式逐字一致（today=9.25 vs 9.05 → 2.21%）
    watch_scan.scan_moves()
    ten_chg = round((9.25 / closes[-10] - 1) * 100, 2)
    assert ten_chg == 2.21


# ---------- 2. 首事件退化：保守跳过该票，状态机不动 ----------

def test_first_event_degenerate_skips_stock(tmp_path):
    # 事件表只有首事件（无 prev bar 退化，note 无 'jump '）且落入窗口 → None → 跳过
    pairs = [("2026-07-08", 9.00), ("2026-07-09", 9.05), ("2026-07-10", 9.06),
             ("2026-07-13", 9.19), ("2026-07-14", 9.16), ("2026-07-15", 9.31),
             ("2026-07-16", 8.85), ("2026-07-17", 8.87), ("2026-07-20", 9.14),
             ("2026-07-21", 9.20), ("2026-07-22", 9.25)]
    events = [{"date": "2026-07-16", "factor": 0.954887,
               "note": "FHcontent=10派4.2元"}]  # 首事件退化 factor=绝对 ratio
    _isolated(tmp_path, pairs, events)
    assert watch_scan._fetch_cached_closes("sh600000") is None
    assert watch_scan.scan_moves() == []
    st = watch_scan.load_state()
    assert "折算股" not in st.get("move_states", {}), "fail-closed 跳过不得写状态机"


def test_first_event_outside_window_ok(tmp_path):
    # 首事件（退化）在窗口外：其 factor 不被任何 bar 消费，窗口内事件正常折算
    pairs = [("2026-07-08", 9.00), ("2026-07-09", 9.05), ("2026-07-10", 9.06),
             ("2026-07-13", 9.19), ("2026-07-14", 9.16), ("2026-07-15", 9.31),
             ("2026-07-16", 8.85), ("2026-07-17", 8.87), ("2026-07-20", 9.14),
             ("2026-07-21", 9.20), ("2026-07-22", 9.25)]
    events = [{"date": "2026-06-01", "factor": 0.97, "note": "首事件"},
              {"date": "2026-07-16", "factor": F, "note": "jump 1.04724409"}]
    _isolated(tmp_path, pairs, events)
    closes = watch_scan._fetch_cached_closes("sh600000")
    assert closes is not None and abs(closes[0] - 9.00 / F) < 1e-9
    assert closes[6] == 8.85


def test_dirty_factor_fail_closed(tmp_path):
    # 窗口内 factor 非法（0/负/非数值）→ fail-closed 返回 None（不信脏数据）
    pairs = [("2026-07-08", 9.00), ("2026-07-09", 9.05), ("2026-07-10", 9.06),
             ("2026-07-13", 9.19), ("2026-07-14", 9.16), ("2026-07-15", 9.31),
             ("2026-07-16", 8.85), ("2026-07-17", 8.87), ("2026-07-20", 9.14),
             ("2026-07-21", 9.20), ("2026-07-22", 9.25)]
    for bad in (0.0, -1.2, None, "abc"):
        events = [{"date": "2026-07-16", "factor": bad, "note": "jump x"}]
        p = _isolated(tmp_path, pairs, events)
        try:
            assert watch_scan._fetch_cached_closes("sh600000") is None, bad
        finally:
            for x in p:
                x.stop()


def test_event_table_read_failure_fail_open(tmp_path):
    # 事件表读失败（基础设施故障）→ fail-open 维持 raw 序列（=P3 既有行为，不扩静默面）
    pairs = [("2026-07-08", 9.00), ("2026-07-09", 9.05), ("2026-07-10", 9.06),
             ("2026-07-13", 9.19), ("2026-07-14", 9.16), ("2026-07-15", 9.31),
             ("2026-07-16", 8.85), ("2026-07-17", 8.87), ("2026-07-20", 9.14),
             ("2026-07-21", 9.20), ("2026-07-22", 9.25)]
    import paper_trading_v2.market_cache as _pmc
    started = _isolated(tmp_path, pairs, [])
    started[-1].stop()
    boom = patch.object(_pmc, "read_exright_events",
                        lambda code: (_ for _ in ()).throw(RuntimeError("db locked")))
    boom.start()
    try:
        closes = watch_scan._fetch_cached_closes("sh600000")
        assert closes == [c for _, c in pairs], "读失败应 fail-open 透传 raw"
    finally:
        boom.stop()


# ---------- 3. 状态机/滞回/输出文案逐字不变（跨除权折算后进甜点区） ----------

def test_sweet_zone_text_byte_identical(tmp_path):
    # 折算后 ten_chg 恰跨 15% 甜点区；文案逐字断言（monitor 字节语义依赖）。
    # 窗口锚：恰好 10 根 bar → closes[-10]=07-08（除权前构造价）。
    pairs = [("2026-07-08", 8.3862), ("2026-07-09", 8.38), ("2026-07-10", 8.38),
             ("2026-07-13", 8.37), ("2026-07-14", 8.38), ("2026-07-15", 8.38),
             ("2026-07-16", 8.37), ("2026-07-17", 8.38), ("2026-07-20", 8.38),
             ("2026-07-21", 8.38)]
    # 精确构造：ten_ago_raw/F = 9.20/1.15 = 8.00（折算后恰 15.00%）
    ten_ago_raw = (9.20 / 1.15) * F    # = 8.377895…
    pairs[0] = ("2026-07-08", ten_ago_raw)
    events = [{"date": "2026-07-16", "factor": F,
               "note": f"ratio 0.954887 -> 1.000000; jump {F:.8f}"}]
    _isolated(tmp_path, pairs, events, price=9.20, pre_close=9.19)
    alerts = watch_scan.scan_moves()
    assert len(alerts) == 1, alerts
    assert alerts[0] == "⚡ 折算股(sh600000) 进入动量甜点区 近10日+15.0%", alerts
    st = watch_scan.load_state()
    assert st["move_states"]["折算股"] == "sweet"


def test_daymove_uses_day_chg_not_ten_path(tmp_path):
    """day_chg 路径回归：单日异动按 pre_close 官方除权口径，与 ten 折算无关。
    （day_ago=mock pre_close，不走 _fetch_cached_closes——结构上隔离，此处锁行为。）"""
    pairs = [("2026-08-20", 9.11), ("2026-08-21", 9.05), ("2026-08-24", 9.22),
             ("2026-08-25", 9.08), ("2026-08-26", 9.21), ("2026-08-27", 9.07),
             ("2026-08-28", 9.00), ("2026-08-31", 9.16), ("2026-09-01", 9.35),
             ("2026-09-02", 9.28), ("2026-09-03", 9.27)]
    _isolated(tmp_path, pairs, [], price=10.10, pre_close=9.40)  # day_chg=+7.45%≥7
    alerts = watch_scan.scan_moves()
    assert alerts == ["🔔 折算股(sh600000) 单日+7.5% 异动"], alerts  # :+.1f 四舍五入
    assert watch_scan.load_state()["move_states"]["折算股"] == "daymove"


if __name__ == "__main__":
    import shutil
    import tempfile

    failures = 0
    for name, fn in sorted((n, f) for n, f in list(globals().items())
                           if n.startswith("test_") and callable(f)):
        d = tempfile.mkdtemp(prefix="scan_qfq_")
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
