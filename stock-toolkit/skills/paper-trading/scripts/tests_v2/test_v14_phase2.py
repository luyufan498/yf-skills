"""执行层 Phase 2 回归锁 · paper-trading 侧（系统兜底单：无 TTL + 自带槽 + 抬升只改价 + D5/D3）。

跑法（隔离；零生产库/零触网）：
    cd stock-toolkit/skills/paper-trading/scripts && \
    .venv/bin/python3 -m pytest tests_v2/test_v14_phase2.py -q

对应方案：plans/2026-09-10_1306-phase2-protect-orders.md（§0.1 无 TTL / §2 边界 / §4 决策 / §5 测试）。

覆盖：
- ``_protect_qty``：比例语义在生成期算成数字；``成本保护-12%`` 里的 % 是**价格**不是数量
  （不得当成"只卖 12%"）；无法判定的 action（``执行``）→ None（fail-closed 不生成）。
- ``place_protect``：自带槽 ``protect:<code>``、``order_ttl=NULL``、band=[线,9.9e9]、
  side='sell'、created_by='atr-auto'、group_key='<code>:protect'；
  同键二次调用 = 抬升（只改价、batch_id 不变）；成交/弃单后 = re-arm（新批次）；
  脏输入（qty 0/-1/1.5/True、line≤0、kind 非法）一律拒。
- ``_ensure_protect_order``：D5 只落更紧的那条线；mode=off 不生成；action 不可判定不生成。
- ``exec_layer.protect_mode``：缺文件=off；mode=orders 时白名单外降级 shadow。
"""
import os
import sqlite3
from types import SimpleNamespace

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


# ------------------------------------------------------------ D3 数量数字
def test_protect_qty_full_position():
    from paper_trading_v2.conditions_cmd import _protect_qty
    assert _protect_qty('清仓', 3000) == 3000
    assert _protect_qty('亏损清仓-清仓100%(9/4重建active)', 3000) == 3000
    assert _protect_qty('恢复期线(现价4.86×0.95)：再破=清仓剩余3018股', 3000) == 3000


def test_protect_qty_partial():
    from paper_trading_v2.conditions_cmd import _protect_qty
    assert _protect_qty('亏损止损-减仓50%', 3000) == 1500
    assert _protect_qty('亏损预警-5%档:减仓20%(成本250.60×0.95=238.07)', 3000) == 600
    assert _protect_qty('卖出 25%', 999) == 249          # 向下取整，宁少不多


def test_protect_qty_price_percent_is_not_quantity():
    """`成本保护-12%` 里的 12% 是价格偏移——不得被当成"只卖 12%"。"""
    from paper_trading_v2.conditions_cmd import _protect_qty
    assert _protect_qty('价值反转仓成本保护-12%', 3000) is None
    assert _protect_qty('消息仓成本保护-5%', 3000) is None


def test_protect_qty_unparseable_is_fail_closed():
    from paper_trading_v2.conditions_cmd import _protect_qty
    assert _protect_qty('执行', 3000) is None
    assert _protect_qty('移动止损-2.5ATR', 3000) is None
    assert _protect_qty('', 3000) is None
    assert _protect_qty('清仓', 0) is None


# ------------------------------------------------------------ place_protect
def test_place_protect_creates_own_slot_without_ttl(ws):
    from paper_trading_v2.sleeve_order import SleeveOrder
    r = SleeveOrder().place_protect('sh600000', 10.5, 300, 'cost', reason='unit')
    assert r['action'] == 'placed' and r['order_ttl'] is None
    s = _slot(ws, 'protect:sh600000')
    assert s is not None, "系统兜底单必须有自带槽（不占消息槽）"
    assert s['status'] == 'pending_order'
    assert s['side'] == 'sell'
    assert s['qty'] == 300
    assert s['band_min'] == 0.0 and s['band_max'] == 10.5   # 跌破型：[哨兵下沿 0, _保护线_]
    assert s['order_ttl'] is None, "兜底单不设 TTL（配 TTL 会打出 11:31~次日 09:31 保护空洞）"
    assert s['created_by'] == 'atr-auto'
    assert s['group_key'] == 'sh600000:protect'
    assert s['fill_status'] == 'pending'
    assert 'protect_placed' in _shadow_kinds(ws)


def test_place_protect_raise_is_same_key_and_keeps_batch(ws):
    """D6 抬升只改价：同键 UPDATE，不产新槽、不改 batch_id。"""
    from paper_trading_v2.sleeve_order import SleeveOrder
    o = SleeveOrder()
    o.place_protect('sh600000', 10.0, 300, 'cost', reason='init', batch_id=20260910)
    r = o.place_protect('sh600000', 10.6, 300, 'trail', reason='raise')
    assert r['action'] == 'raised' and r['batch_id'] == 20260910
    c = _conn(ws)
    try:
        n, band, lo = c.execute("SELECT COUNT(*), MAX(band_max), MIN(band_min) FROM event_slots "
                                "WHERE event_key='protect:sh600000'").fetchone()
    finally:
        c.close()
    assert n == 1, "抬升不得产生第二张活跃保护单（D5：单键单行）"
    assert band == 10.6 and lo == 0.0, "跌破卖几何：[0, _保护线_]"
    assert 'protect_raised' in _shadow_kinds(ws)


def test_place_protect_rearm_after_fill_uses_new_batch(ws):
    from paper_trading_v2.sleeve_order import SleeveOrder
    o = SleeveOrder()
    o.place_protect('sh600000', 10.0, 300, 'trail', reason='init', batch_id=20260910)
    c = _conn(ws)
    try:
        with c:
            c.execute("UPDATE event_slots SET status='open', fill_status='filled' "
                      "WHERE event_key='protect:sh600000'")
    finally:
        c.close()
    r = o.place_protect('sh600000', 9.8, 150, 'trail', reason='next day', batch_id=20260911)
    assert r['action'] == 'rearmed'
    s = _slot(ws, 'protect:sh600000')
    assert s['status'] == 'pending_order' and s['fill_status'] == 'pending'
    assert s['batch_id'] == 20260911 and s['qty'] == 150
    assert 'protect_rearmed' in _shadow_kinds(ws)


@pytest.mark.parametrize('qty', [0, -5, 1.5, True, None, 'abc'])
def test_place_protect_rejects_dirty_qty(ws, qty):
    from paper_trading_v2.sleeve_order import SleeveOrder
    with pytest.raises(ValueError):
        SleeveOrder().place_protect('sh600000', 10.0, qty, 'cost')
    assert _slot(ws, 'protect:sh600000') is None, "脏输入不得落库"


@pytest.mark.parametrize('line,kind', [(0, 'cost'), (-1, 'cost'), ('x', 'cost'), (10.0, 'zzz')])
def test_place_protect_rejects_dirty_inputs(ws, line, kind):
    from paper_trading_v2.sleeve_order import SleeveOrder
    with pytest.raises(ValueError):
        SleeveOrder().place_protect('sh600000', line, 100, kind)
    assert _slot(ws, 'protect:sh600000') is None


# ------------------------------------------------------------ D5 生成期消解
def _cond(price, action, status='active'):
    return SimpleNamespace(price=price, action=action, status=status)


def test_ensure_protect_order_picks_tighter_line(ws, monkeypatch):
    from paper_trading_v2 import exec_layer
    from paper_trading_v2.conditions_cmd import _ensure_protect_order
    monkeypatch.setattr(exec_layer, 'protect_mode', lambda name=None: 'shadow')
    entry = {}
    r = _ensure_protect_order('测试票', 'sh600000', 3000,
                              _cond(10.00, '清仓'), _cond(10.30, '清仓'), entry)
    assert r is not None and r['line'] == 10.30, "只落更紧的那条线（D5 用高者）"
    s = _slot(ws, 'protect:sh600000')
    assert s['qty'] == 3000 and s['band_max'] == 10.30 and s['band_min'] == 0.0


def test_ensure_protect_order_off_writes_nothing(ws, monkeypatch):
    from paper_trading_v2 import exec_layer
    from paper_trading_v2.conditions_cmd import _ensure_protect_order
    monkeypatch.setattr(exec_layer, 'protect_mode', lambda name=None: 'off')
    entry = {}
    assert _ensure_protect_order('测试票', 'sh600000', 3000,
                                 _cond(10.0, '清仓'), None, entry) is None
    assert _slot(ws, 'protect:sh600000') is None
    assert 'protect_skipped' not in entry, 'off 是「未启用」，不是异常，不留 skipped'


def test_ensure_protect_order_skips_unparseable_action(ws, monkeypatch):
    from paper_trading_v2 import exec_layer
    from paper_trading_v2.conditions_cmd import _ensure_protect_order
    monkeypatch.setattr(exec_layer, 'protect_mode', lambda name=None: 'shadow')
    entry = {}
    assert _ensure_protect_order('测试票', 'sh600000', 3000,
                                 _cond(10.0, '执行'), None, entry) is None
    assert _slot(ws, 'protect:sh600000') is None
    assert 'fail-closed' in entry['protect_skipped']


def test_ensure_protect_order_ignores_inactive_line(ws, monkeypatch):
    from paper_trading_v2 import exec_layer
    from paper_trading_v2.conditions_cmd import _ensure_protect_order
    monkeypatch.setattr(exec_layer, 'protect_mode', lambda name=None: 'shadow')
    entry = {}
    r = _ensure_protect_order('测试票', 'sh600000', 3000,
                              _cond(10.0, '清仓', status='triggered'), None, entry)
    assert r is None and '无 active 保护线' in entry['protect_skipped']


# ------------------------------------------------------------ 开关（exec_layer）
def test_protect_mode_defaults_off_without_file(ws):
    from paper_trading_v2.exec_layer import protect_mode, config_path
    assert config_path() == os.path.join(str(ws), 'exec_layer.json')
    assert protect_mode('任意票') == 'off'


def test_protect_mode_reads_file_and_whitelist(ws):
    from paper_trading_v2.exec_layer import protect_mode
    (ws / 'exec_layer.json').write_text(
        '{"protect_orders": {"mode": "orders", "exec_stocks": ["中芯国际"]}}',
        encoding='utf-8')
    assert protect_mode('中芯国际') == 'orders'
    assert protect_mode('其它票') == 'shadow', "白名单外只留痕（禁止全局翻转）"


def test_protect_mode_shadow_and_env_override(ws, monkeypatch):
    from paper_trading_v2.exec_layer import protect_mode
    (ws / 'exec_layer.json').write_text('{"protect_orders": {"mode": "shadow"}}',
                                        encoding='utf-8')
    assert protect_mode('任意票') == 'shadow'
    monkeypatch.setenv('PTRADE2_PROTECT_ORDERS', 'off')
    assert protect_mode('任意票') == 'off', "env 逃生阀覆盖文件"


def test_protect_mode_invalid_falls_back_off(ws):
    from paper_trading_v2.exec_layer import protect_mode
    (ws / 'exec_layer.json').write_text('{"protect_orders": {"mode": "weird"}}',
                                        encoding='utf-8')
    assert protect_mode('任意票') == 'off'
    (ws / 'exec_layer.json').write_text('not json', encoding='utf-8')
    assert protect_mode('任意票') == 'off'
