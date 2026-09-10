"""执行层 Phase 3 回归锁 · paper-trading 侧（止盈阶梯 → 挂单：涨破卖几何 / 覆盖式重挂 / 不复活）。

跑法（隔离；零生产库/零触网）：
    cd stock-toolkit/skills/paper-trading/scripts && \
    .venv/bin/python3 -m pytest tests_v2/test_v14_phase3.py -q

覆盖：
- ``place_take_profit``：自带槽 ``tp:<code>#<leg>``、``order_ttl=NULL``、
  **涨破卖几何 band=[触发价, 9.9e9]**（写反=下跌时卖出，故单独锁一条反例）、
  side='sell'、group_key='<code>:tp'、created_by='atr-auto'；
  同键二次调用=刷新（只改价量，batch 不变）；**已成交腿不复活**（除非显式新批次）。
- ``_tp_qty``：剩余仓位 1/3；<3 股或非法输入 → None（fail-closed）。
- ``_ensure_tp_orders``：覆盖式重挂——(价,量) 一致 → unchanged（零写入）；
  变了 → 旧槽 superseded + 新槽 pending；开关 off → 不生成。
"""
import json
import os

import pytest


def _conn(ws):
    from paper_trading_v2.db import get_connection, migrate_db
    c = get_connection(ws / 'master_pool.db')
    migrate_db(c)
    return c


def _slot(ws, key):
    c = _conn(ws)
    try:
        return c.execute("SELECT * FROM event_slots WHERE event_key=?", (key,)).fetchone()
    finally:
        c.close()


def _shadow_kinds(ws):
    c = _conn(ws)
    try:
        return [r[0] for r in c.execute("SELECT kind FROM shadow_log ORDER BY id")]
    finally:
        c.close()


def _set_tp_mode(ws, mode='orders', stocks=('中芯国际',)):
    (ws / 'exec_layer.json').write_text(
        json.dumps({'tp_orders': {'mode': mode, 'exec_stocks': list(stocks)}}),
        encoding='utf-8')


# ------------------------------------------------------------------ 槽与几何
def test_place_take_profit_geometry_is_break_up(ws):
    """涨破卖几何：band=[触发价, 哨兵上沿]——进带即"现价 ≥ 止盈价"。"""
    from paper_trading_v2.sleeve_order import SleeveOrder, BAND_HI_SENTINEL
    r = SleeveOrder().place_take_profit('sh600000', 1, 13.0, 100, reason='unit')
    assert r['action'] == 'placed' and r['order_ttl'] is None
    s = _slot(ws, 'tp:sh600000#1')
    assert s is not None, "止盈腿必须有自带槽（不占消息槽）"
    assert s['status'] == 'pending_order' and s['side'] == 'sell'
    assert s['qty'] == 100
    assert s['band_min'] == 13.0, "止盈触发价必须落在**下沿**（涨破卖）"
    assert s['band_max'] == BAND_HI_SENTINEL
    assert s['order_ttl'] is None, "止盈腿不设 TTL（配 TTL 会打出止盈空洞）"
    assert s['group_key'] == 'sh600000:tp' and s['created_by'] == 'atr-auto'
    assert s['fill_status'] == 'pending'
    assert 'tp_placed' in _shadow_kinds(ws)


def test_place_take_profit_two_legs_are_distinct_slots(ws):
    from paper_trading_v2.sleeve_order import SleeveOrder
    o = SleeveOrder()
    o.place_take_profit('sh600000', 1, 13.0, 100)
    o.place_take_profit('sh600000', 2, 15.0, 100)
    c = _conn(ws)
    try:
        rows = c.execute("SELECT event_key, band_min FROM event_slots WHERE group_key='sh600000:tp' "
                         "ORDER BY event_key").fetchall()
    finally:
        c.close()
    assert [r['event_key'] for r in rows] == ['tp:sh600000#1', 'tp:sh600000#2']
    assert [r['band_min'] for r in rows] == [13.0, 15.0], "两档价必须保序分离（腿2 > 腿1）"


def test_place_take_profit_refresh_keeps_batch_and_single_row(ws):
    from paper_trading_v2.sleeve_order import SleeveOrder
    o = SleeveOrder()
    o.place_take_profit('sh600000', 1, 13.0, 100, batch_id=20260910)
    r = o.place_take_profit('sh600000', 1, 13.6, 90, reason='加仓后重算')
    assert r['action'] == 'raised' and r['batch_id'] == 20260910
    c = _conn(ws)
    try:
        n, price, qty = c.execute("SELECT COUNT(*), MAX(band_min), MAX(qty) FROM event_slots "
                                  "WHERE event_key='tp:sh600000#1'").fetchone()
    finally:
        c.close()
    assert n == 1, "刷新不得产生第二条同腿槽"
    assert price == 13.6 and qty == 90


def test_place_take_profit_never_revives_filled_leg(ws):
    """A1 红线：已成交腿不复活——除非调用方显式给新批次。"""
    from paper_trading_v2.sleeve_order import SleeveOrder
    o = SleeveOrder()
    o.place_take_profit('sh600000', 1, 13.0, 100, batch_id=20260910)
    c = _conn(ws)
    try:
        with c:
            c.execute("UPDATE event_slots SET status='open', fill_status='filled' "
                      "WHERE event_key='tp:sh600000#1'")
    finally:
        c.close()
    r = o.place_take_profit('sh600000', 1, 12.0, 100, reason='次日重挂')
    assert r['action'] == 'skip_filled'
    s = _slot(ws, 'tp:sh600000#1')
    assert s['fill_status'] == 'filled' and s['status'] == 'open', "已兑现的腿不得被重挂成 pending"
    r2 = o.place_take_profit('sh600000', 1, 12.0, 100, reason='新一轮', batch_id=20260911)
    assert r2['action'] == 'rearmed'
    assert _slot(ws, 'tp:sh600000#1')['fill_status'] == 'pending'


@pytest.mark.parametrize('leg', [0, 3, 'x', None])
def test_place_take_profit_rejects_bad_leg(ws, leg):
    from paper_trading_v2.sleeve_order import SleeveOrder
    with pytest.raises(ValueError):
        SleeveOrder().place_take_profit('sh600000', leg, 13.0, 100)


@pytest.mark.parametrize('price', [0, -1.5, 'x', None])
def test_place_take_profit_rejects_bad_price(ws, price):
    from paper_trading_v2.sleeve_order import SleeveOrder
    with pytest.raises(ValueError):
        SleeveOrder().place_take_profit('sh600000', 1, price, 100)


@pytest.mark.parametrize('qty', [0, -5, 1.5, True, None, '2/3'])
def test_place_take_profit_rejects_bad_qty(ws, qty):
    """比例语义必须在生成期数字化：机械层只收正整数股数（1.5 不得静默截断成 1）。"""
    from paper_trading_v2.sleeve_order import SleeveOrder
    with pytest.raises(ValueError):
        SleeveOrder().place_take_profit('sh600000', 1, 13.0, qty)


# ------------------------------------------------------------ 数量数字化（1/3）
def test_tp_qty_is_one_third():
    from paper_trading_v2.conditions_cmd import _tp_qty
    assert _tp_qty(300) == 100
    assert _tp_qty(301) == 100        # 向下取整，宁少不多
    assert _tp_qty(5) == 1
    assert _tp_qty(2) is None         # 1/3 不足 1 股 → fail-closed 不挂
    assert _tp_qty('x') is None


def test_tp_legs_price_ladder_ordered():
    from paper_trading_v2.conditions_cmd import _tp_legs
    legs = _tp_legs(10.0)
    assert legs == [(1, 13.0), (2, 15.0)], "+30%/+50% 两档（腿2 必须严格高于腿1）"


# ------------------------------------------------------ 覆盖式重挂（D2）
def test_ensure_tp_orders_unchanged_is_zero_write(ws):
    _set_tp_mode(ws)
    from paper_trading_v2.conditions_cmd import _ensure_tp_orders
    from paper_trading_v2.sleeve_order import SleeveOrder
    SleeveOrder().place_take_profit('sh600000', 1, 13.0, 100)
    SleeveOrder().place_take_profit('sh600000', 2, 15.0, 100)
    entry = {}
    got = _ensure_tp_orders('中芯国际', 'sh600000', 300, 10.0, entry)
    assert [g['action'] for g in got] == ['unchanged', 'unchanged'], \
        "价量一致时必须 no-op（不写库、不产事件）"
    assert _shadow_kinds(ws) == ['tp_placed', 'tp_placed'], "unchanged 不得新增留痕"


def test_ensure_tp_orders_supersedes_stale_slot(ws):
    """覆盖式重挂：价变了 → 旧槽 superseded + 新槽 pending（撤+挂成对）。"""
    _set_tp_mode(ws)
    from paper_trading_v2.conditions_cmd import _ensure_tp_orders
    from paper_trading_v2.sleeve_order import SleeveOrder
    SleeveOrder().place_take_profit('sh600000', 1, 11.0, 100, batch_id=20260910)
    SleeveOrder().place_take_profit('sh600000', 2, 12.0, 100, batch_id=20260910)
    got = _ensure_tp_orders('中芯国际', 'sh600000', 300, 10.0, {})
    assert [g['line'] for g in got] == [13.0, 15.0]
    c = _conn(ws)
    try:
        rows = dict(c.execute("SELECT event_key, status FROM event_slots "
                              "WHERE group_key='sh600000:tp'").fetchall())
    finally:
        c.close()
    assert rows == {'tp:sh600000#1': 'pending_order', 'tp:sh600000#2': 'pending_order'}, \
        "重挂后两腿都应是新的 pending 槽（同组不得留旧可执行腿）"


def test_ensure_tp_orders_off_writes_nothing(ws):
    _set_tp_mode(ws, mode='off')
    from paper_trading_v2.conditions_cmd import _ensure_tp_orders
    assert _ensure_tp_orders('中芯国际', 'sh600000', 300, 10.0, {}) is None
    c = _conn(ws)
    try:
        assert c.execute("SELECT COUNT(*) FROM event_slots WHERE event_key LIKE 'tp:%'"
                         ).fetchone()[0] == 0
    finally:
        c.close()


def test_ensure_tp_orders_whitelist_outside_downgrades_to_shadow(ws):
    """白名单外只留痕：tp_mode 降级 shadow → 仍生成槽（可对账），但不执行（扫描侧把关）。"""
    from paper_trading_v2.exec_layer import tp_mode
    _set_tp_mode(ws, stocks=('别的票',))
    assert tp_mode('中芯国际') == 'shadow'
    assert tp_mode('别的票') == 'orders'


def test_ensure_tp_orders_skips_bad_avg_cost(ws):
    _set_tp_mode(ws)
    from paper_trading_v2.conditions_cmd import _ensure_tp_orders
    entry = {}
    assert _ensure_tp_orders('中芯国际', 'sh600000', 300, 0, entry) is None
    assert '均价' in entry['tp_skipped']
