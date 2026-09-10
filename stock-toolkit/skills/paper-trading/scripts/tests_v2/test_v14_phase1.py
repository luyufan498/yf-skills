"""执行层 Phase 1 回归锁 · paper-trading 侧（v14 schema + CLI + fail-closed 守卫）。

跑法（隔离；零生产库/零触网）：
    cd stock-toolkit/skills/paper-trading/scripts && \
    .venv/bin/python3 -m pytest tests_v2/test_v14_phase1.py -q

对应方案：plans/2026-09-10_110047-execution-layer-orders-decoupling.md（附录 A4/A6 + A7）。

覆盖：
- schema v14：event_slots 加 side/qty/group_key/batch_id（加列不改 PK，幂等）；
  存量行 side='buy'、其余 NULL（旧槽行为不变）。
- place：--side sell --qty N 落库；卖单缺 qty → 拒（比例语义必须先算成数字）。
- 单边带：--rel ge/le --target X → 落库为**哨兵极值**（[X,9.9e9] / [0,X]），
  **不写 NULL**（NULL 是"未挂单/断链"哨兵）。
- 旧路径：不带新参数 → band=[0.95,1.05]×anchor、side='buy'（逐字节兼容）。
- fill 守卫：side='sell' 的槽走 sleeve-order-fill → 拒（防止盈单被当买入建段）。
- expire 守卫：--reason group_closed 需 group_key（无 key → 拒；有 key → pending_rejudge）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _conn(ws):
    from paper_trading_v2.db import get_connection, migrate_db
    c = get_connection(ws / 'master_pool.db')
    migrate_db(c)
    return c


@pytest.fixture(autouse=True)
def _init_pools(ws):
    """消息池/主池初始化（sleeve-open 需要池里有资金）+ 行情密闭（零触网）。"""
    from unittest.mock import patch
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(ws / 'master_pool.db')
    m.init_pool(10000000)
    m.init_pool(2000000, pool='sleeve')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=None):
        yield


def _run(*args):
    from paper_trading_v2.cli import app
    return runner.invoke(app, [str(a) for a in args])


def _slot(ws, key):
    c = _conn(ws)
    try:
        return c.execute("SELECT * FROM event_slots WHERE event_key=?", (key,)).fetchone()
    finally:
        c.close()


def _open_slot(**kw):
    r = _run('sleeve-open', kw.get('stock', '测试票'), '--budget', kw.get('budget', 100000),
             '--event-key', kw['key'], '--code', kw.get('code', 'sh600000'))
    assert r.exit_code == 0, r.output


def _ttl():
    from paper_trading_v2.sleeve_order import next_session_close
    return next_session_close().isoformat(timespec='seconds')


# ---------------------------------------------------------------- schema v14
def test_v14_schema_columns_and_legacy_defaults(ws):
    """四列到位；存量行 side='buy'、qty/group_key/batch_id=NULL（旧槽行为不变）。"""
    from paper_trading_v2.db import SCHEMA_VERSION
    c = _conn(ws)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(event_slots)").fetchall()}
        assert {'side', 'qty', 'group_key', 'batch_id'} <= cols, f"缺列: {cols}"
        assert c.execute("SELECT version FROM schema_meta").fetchone()[0] == SCHEMA_VERSION
        with c:
            c.execute("INSERT INTO event_slots (event_key, status, opened_at, budget) "
                      "VALUES ('LEGACY#14','open','2026-09-01T09:00:00',1000)")
        row = c.execute("SELECT side, qty, group_key, batch_id FROM event_slots "
                        "WHERE event_key='LEGACY#14'").fetchone()
        assert row[0] == 'buy', f"存量行 side 必须默认 buy，实得 {row[0]!r}"
        assert row[1] is None and row[2] is None and row[3] is None
    finally:
        c.close()


# ------------------------------------------------------- place：卖单 + 单边带
def test_v14_place_sell_ge_one_sided_band_sentinel(ws):
    """--side sell --qty 333 --rel ge --target 15 → 带 [15, 9.9e9]（哨兵极值，非 NULL）。"""
    _open_slot(key='ND#930')
    r = _run('sleeve-order-place', 'ND#930', '--anchor', '12.0', '--ttl', _ttl(),
             '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15')
    assert r.exit_code == 0, r.output
    s = _slot(ws, 'ND#930')
    assert s['side'] == 'sell' and s['qty'] == 333
    assert s['band_min'] == pytest.approx(15.0)
    assert s['band_max'] >= 9.9e9, "≥X 型上沿必须是哨兵极值（不写 NULL）"
    assert '≥15' in r.output, f"展示层应美化回单边语义：{r.output}"


def test_v14_place_sell_le_one_sided_band_sentinel(ws):
    """--rel le --target 8 → 带 [0, 8]。"""
    _open_slot(key='ND#931')
    r = _run('sleeve-order-place', 'ND#931', '--anchor', '9.5', '--ttl', _ttl(),
             '--side', 'sell', '--qty', '500', '--rel', 'le', '--target', '8')
    assert r.exit_code == 0, r.output
    s = _slot(ws, 'ND#931')
    assert s['band_min'] == pytest.approx(0.0) and s['band_max'] == pytest.approx(8.0)
    assert '≤8' in r.output, f"展示层应美化：{r.output}"


def test_v14_place_sell_requires_qty(ws):
    """卖单缺 qty → fail-closed 拒绝（比例语义必须先算成数字）。"""
    _open_slot(key='ND#932')
    r = _run('sleeve-order-place', 'ND#932', '--anchor', '12.0', '--ttl', _ttl(),
             '--side', 'sell', '--rel', 'ge', '--target', '15')
    assert r.exit_code == 1, f"缺 qty 必须拒绝，实得 exit={r.exit_code}: {r.output}"
    assert 'qty' in r.output
    assert _slot(ws, 'ND#932')['status'] == 'open', "拒绝后槽必须不动"


def test_v14_place_group_key_and_batch_id_persisted(ws):
    """group_key/batch_id 落库（供"仓位耗尽→失效同组"与豁免新批次）。"""
    _open_slot(key='ND#933')
    r = _run('sleeve-order-place', 'ND#933', '--anchor', '12.0', '--ttl', _ttl(),
             '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15',
             '--group-key', '测试票:tp', '--batch-id', '7')
    assert r.exit_code == 0, r.output
    s = _slot(ws, 'ND#933')
    assert s['group_key'] == '测试票:tp' and s['batch_id'] == 7


def test_v14_place_legacy_default_band_unchanged(ws):
    """不带新参数 → 旧语义 [0.95,1.05]×anchor、side='buy'（逐字节兼容）。"""
    _open_slot(key='ND#934')
    r = _run('sleeve-order-place', 'ND#934', '--anchor', '10.0', '--ttl', _ttl())
    assert r.exit_code == 0, r.output
    s = _slot(ws, 'ND#934')
    assert s['band_min'] == pytest.approx(9.5) and s['band_max'] == pytest.approx(10.5)
    assert s['side'] == 'buy' and s['qty'] is None


# ------------------------------------------------------------- fail-closed 守卫
def test_v14_fill_rejects_sell_slot(ws):
    """side='sell' 槽走 sleeve-order-fill → 拒（否则止盈单会被执行成加仓）。"""
    _open_slot(key='ND#935')
    assert _run('sleeve-order-place', 'ND#935', '--anchor', '12.0', '--ttl', _ttl(),
                '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15'
                ).exit_code == 0
    r = _run('sleeve-order-fill', 'ND#935', '--price', '16.0')
    assert r.exit_code == 1, f"卖单不得走 fill，实得：{r.output}"
    assert 'sell' in r.output and 'fill' in r.output
    assert _slot(ws, 'ND#935')['status'] == 'pending_order', "被拒后槽态不得变化"


def test_v14_expire_group_closed_requires_group_key(ws):
    """group_closed 弃单守卫（v14 原版 + v14 补丁）：无 group_key → 拒；
    有 key 但**组内无成交证据** → 拒（补丁加的库内核验）；有 key 且组内确有已成交槽 → pending_rejudge。"""
    _open_slot(key='ND#936')
    assert _run('sleeve-order-place', 'ND#936', '--anchor', '12.0', '--ttl', _ttl(),
                '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15'
                ).exit_code == 0
    r = _run('sleeve-order-expire', 'ND#936', '--reason', 'group_closed')
    assert r.exit_code == 1, f"无 group_key 不得用 group_closed：{r.output}"
    assert _slot(ws, 'ND#936')['status'] == 'pending_order'

    _open_slot(key='ND#937', stock='测试票二')     # 同票不可两个活跃槽 → 换标的
    assert _run('sleeve-order-place', 'ND#937', '--anchor', '12.0', '--ttl', _ttl(),
                '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15',
                '--group-key', '测试票:tp').exit_code == 0
    # v14 补丁：仅"有 group_key"不够 —— 组内必须存在 fill_status='filled' 的兄弟槽
    r = _run('sleeve-order-expire', 'ND#937', '--reason', 'group_closed')
    assert r.exit_code == 1, f"组内无成交证据不得用 group_closed：{r.output}"
    assert _slot(ws, 'ND#937')['status'] == 'pending_order'

    # 造一个同组已成交兄弟槽（= 发起方判定的"成交存在"证据），守卫放行
    c = _conn(ws)
    try:
        with c:
            c.execute("INSERT INTO event_slots (event_key, status, opened_at, budget, "
                      "fill_status, side, qty, group_key, batch_id) VALUES "
                      "('ND#937F','open','2026-09-10T09:30:00',100000,'filled','sell',"
                      "333,'测试票:tp',1)")
    finally:
        c.close()
    r = _run('sleeve-order-expire', 'ND#937', '--reason', 'group_closed')
    assert r.exit_code == 0, r.output
    assert _slot(ws, 'ND#937')['status'] == 'pending_rejudge'


# ------------------------------------------------- v14 补丁：校验与守卫补口
def test_v14_target_without_rel_rejected(ws):
    """补丁：只给 --target 不配 --rel → 拒（原先静默忽略并按默认 ±5% 双边带落库）。"""
    _open_slot(key='ND#940')
    r = _run('sleeve-order-place', 'ND#940', '--anchor', '12.0', '--ttl', _ttl(),
             '--target', '15')
    assert r.exit_code == 1, f"只给 --target 必须拒：{r.output}"
    assert '--rel' in r.output
    s = _slot(ws, 'ND#940')
    assert s['status'] == 'open' and s['band_min'] is None, "被拒后槽必须不动"


def test_v14_rel_conflicts_with_explicit_band(ws):
    """补丁：--rel 与显式 --band-min/--band-max 互斥（原先 rel 静默覆盖显式带）。"""
    _open_slot(key='ND#941')
    r = _run('sleeve-order-place', 'ND#941', '--anchor', '12.0', '--ttl', _ttl(),
             '--rel', 'ge', '--target', '15', '--band-min', '5', '--band-max', '8')
    assert r.exit_code == 1, f"混给必须拒：{r.output}"
    assert _slot(ws, 'ND#941')['status'] == 'open'


def test_v14_place_sell_rejects_fractional_qty_via_api(ws):
    """补丁：API 层卖单 qty 非整数（1.5 / True）→ 拒（原先 int() 把 1.5 静默截成 1）。"""
    from paper_trading_v2.sleeve_order import SleeveOrder, next_session_close
    _open_slot(key='ND#942')
    ttl = next_session_close().isoformat(timespec='seconds')
    for bad in (1.5, True):
        try:
            SleeveOrder().place('ND#942', 12.0, ttl, side='sell', qty=bad, band_min=15.0)
            raise AssertionError(f"qty={bad!r} 必须被拒")
        except ValueError as e:
            assert 'qty' in str(e), e
    assert _slot(ws, 'ND#942')['status'] == 'open'


def test_v14_expire_band_break_rejects_sell_slot(ws):
    """补丁：卖单不走 band_break（单边带反向走远由短 TTL 承担，与 watch_scan 口径一致）。"""
    _open_slot(key='ND#943')
    assert _run('sleeve-order-place', 'ND#943', '--anchor', '12.0', '--ttl', _ttl(),
                '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15'
                ).exit_code == 0
    r = _run('sleeve-order-expire', 'ND#943', '--reason', 'band_break')
    assert r.exit_code == 1, f"卖单不得 band_break：{r.output}"
    assert _slot(ws, 'ND#943')['status'] == 'pending_order'


def test_v14_rejudge_keep_rejects_nonpositive_anchor_for_sell(ws):
    """补丁：卖单 rejudge --keep --anchor 0/负数 → 拒（原先跳过漂移检查后可写脏锚）。"""
    _open_slot(key='ND#944')
    assert _run('sleeve-order-place', 'ND#944', '--anchor', '12.0', '--ttl', _ttl(),
                '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15',
                '--group-key', '测试票九:tp').exit_code == 0
    c = _conn(ws)
    try:
        with c:
            c.execute("UPDATE event_slots SET order_ttl='2020-01-01T15:00:00' "
                      "WHERE event_key='ND#944'")
    finally:
        c.close()
    assert _run('sleeve-order-expire', 'ND#944', '--reason', 'expired').exit_code == 0
    for bad in ('0', '-5'):
        r = _run('sleeve-order-rejudge', 'ND#944', '--keep', '--anchor', bad)
        assert r.exit_code == 1, f"--anchor {bad} 必须拒：{r.output}"
        assert _slot(ws, 'ND#944')['status'] == 'pending_rejudge'


def test_v14_fill_pending_guard_blocks_sell_slot(ws):
    """补丁：守卫下沉——直连 SleeveOpener.fill_pending(statuses=pending_order) 也不许把
    卖单槽当买入建段（原先守卫只在 SleeveOrder.fill 一层）。"""
    from paper_trading_v2.sleeve_open import SleeveOpener
    _open_slot(key='ND#945')
    assert _run('sleeve-order-place', 'ND#945', '--anchor', '12.0', '--ttl', _ttl(),
                '--side', 'sell', '--qty', '333', '--rel', 'ge', '--target', '15'
                ).exit_code == 0
    res = SleeveOpener().fill_pending(event_key='ND#945',
                                      open_prices={'测试票': 13.0}, skip_conditions=True,
                                      statuses=('pending_order',), only_unordered=False)
    assert res and not res[0]['filled'], f"卖单槽不得成交：{res}"
    assert 'sell' in str(res[0]['skipped']), f"应留痕跳过原因：{res}"
    assert _slot(ws, 'ND#945')['fill_status'] == 'pending', "槽态不得变化"


def test_v14_expire_unknown_reason_still_rejected(ws):
    """白名单外的 reason 依旧拒绝（未被 v14 扩宽）。"""
    _open_slot(key='ND#938')
    assert _run('sleeve-order-place', 'ND#938', '--anchor', '12.0', '--ttl', _ttl()
                ).exit_code == 0
    r = _run('sleeve-order-expire', 'ND#938', '--reason', 'group_closed_typo')
    assert r.exit_code == 1
    assert _slot(ws, 'ND#938')['status'] == 'pending_order'
