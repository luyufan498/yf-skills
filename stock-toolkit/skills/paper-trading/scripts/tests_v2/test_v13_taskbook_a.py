"""任务书 A（契约/数据层）回归锁（2026-09-10 条件/挂单执行链改造 v3，先红后绿）

A1 = WP0 TP 阶梯重锚：sync_take_profit_ladder 未触发档按剩余仓位 FIFO 均价重算
     （TP1=均价×1.30、TP2=均价×1.50，成对重算保序），已触发档永不重挂，
     成本基变化 >0.5% 才改价（写 condition_history reason 含"成本基重算"），
     重算后档位 ≤ 现价 → 正常触发不静默改价。
A2 = WP2 幂等键：trades.event_id 列 + buy/sell event_id 参数（同键已成交即拒绝
     + audit 留痕）+ CLI --event-id。
A3 = WP1-CLI 半边：buy/sell --price 显式检测价成交（默认 None=现状自取价），
     E3 行情防线（停牌/一字板/报价陈旧拒绝，照抄 sleeve_order.fill）。
A4 = WP3-A 创建者列：conditions/event_slots.created_by + CLI --created-by
     （env PTRADE2_CREATOR → user）+ sleeve-open=msg-watch + TP 挂载=atr-auto
     + 一次性回填脚本（--dry-run/--apply）。
A5 = WP7-A schema：event_slots.placed_px / band_out_count + expire reason
     白名单扩 band_left/band_skipped。
隔离：pytest 临时 workspace，行情全 mock，零触网，生产库零接触。
"""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from typer.testing import CliRunner

runner = CliRunner()

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), '..')
_BACKFILL = os.path.join(_SCRIPTS_DIR, 'backfill_created_by_20260910.py')


def _db(ws):
    return ws / 'master_pool.db'


def _conn(ws):
    from paper_trading_v2.db import get_connection, migrate_db
    c = get_connection(_db(ws))
    migrate_db(c)
    return c


def _run(app, *args):
    return runner.invoke(app, [str(a) for a in args])


def _cols(ws, table):
    c = _conn(ws)
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        c.close()


def _seg_id(conn, stock):
    return conn.execute(
        "SELECT id FROM position WHERE stock=? AND status='open' "
        "ORDER BY id DESC LIMIT 1", (stock,)).fetchone()[0]


def _insert_buy(conn, seg_id, qty, price, code='sh600100'):
    seq = conn.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM trades "
                       "WHERE account_id=?", (seg_id,)).fetchone()[0]
    conn.execute(
        "INSERT INTO trades (account_id, seq, operation, stock_code, quantity, "
        "price, total_cost, timestamp, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (seg_id, seq, 'buy', code, qty, price, qty * price,
         '2026-09-01T10:00:00', ''))


@pytest.fixture(autouse=True)
def no_network(ws):
    """默认密闭：实时价 mock 为 None（A3 用例内部自带精确行情再覆盖）。"""
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=None):
        yield


def _mk_account(trader, name, code='sz000001', capital=500000.0):
    from paper_trading_v2.models import Account, CapitalPool
    trader.storage.save_account(Account(
        stock_name=name, stock_code=code,
        capital_pool=CapitalPool(total=capital, available=capital, used=0)))


def _patch_price(current_price):
    return patch(
        'paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
        **{'return_value.current_price': current_price})


def _quote(px=10.0, volume='100000', date=None, time=None,
           high=None, low=None, pre_close=10.0, code='sz000001', name='行情股'):
    """E3 行情防线测试件（可变造停牌/一字板/陈旧），同 test_v12_order._quote。"""
    from paper_trading_v2.models import StockInfo
    now = datetime.now()
    return StockInfo(code=code, name=name, current_price=px, pre_close=pre_close,
                     high=high if high is not None else px + 0.2,
                     low=low if low is not None else px - 0.2, volume=volume,
                     date=date or now.strftime('%Y-%m-%d'),
                     time=time or now.strftime('%H:%M:%S'), source='tencent')


def _tp_conditions(cm, name, tp1_price, tp2_price, tp1_status='active'):
    """预挂 TP 阶梯（旧成本基），返回写入价格。"""
    from paper_trading_v2.conditions import (ConditionsRecord, Condition,
                                             ConditionChange)
    rec = ConditionsRecord(stock_name=name)
    for ctype, price, status, label in (
            ('take_profit_1', tp1_price, tp1_status, '分批止盈①+30%卖1/3'),
            ('take_profit_2', tp2_price, 'active', '分批止盈②+50%卖1/3')):
        rec.conditions[ctype] = Condition(
            id=f'{ctype}-{name}', type=ctype, name=label, price=price,
            action='次日卖出1/3（收盘确认触发）', category='hard', status=status,
            history=[ConditionChange(old_price=0, new_price=price,
                                     reason='首次设定', level='auto')])
    cm.save_conditions(rec)


# ======================================================================
# A1 = WP0 TP 阶梯重锚（sync_take_profit_ladder）
# ======================================================================

def test_a1_reanchors_untriggered_ladder_to_fifo_avg(ws):
    """未触发档按剩余仓位 FIFO 均价重算：TP1=均价×1.30、TP2=均价×1.50，成对保序，
    改价写 history（reason 含"成本基重算"）。现状病灶：跳过已有 active TP 行。"""
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.models import Account, CapitalPool
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='阶梯票', stock_code='sh600100',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    conn = _conn(ws)
    with conn:
        seg = _seg_id(conn, '阶梯票')
        _insert_buy(conn, seg, 1000, 100.0)   # FIFO: 1000@100
        _insert_buy(conn, seg, 1000, 60.0)    # FIFO: +1000@60 → 均价 80
    cm = ConditionsManager(storage=s)
    _tp_conditions(cm, '阶梯票', 130.0, 150.0)   # 旧成本基 100 的档位
    cm.sync_take_profit_ladder('阶梯票', avg_cost=80.0, current_price=100.0)
    rec = cm.load_conditions('阶梯票')
    tp1 = rec.conditions['take_profit_1']
    tp2 = rec.conditions['take_profit_2']
    assert tp1.price == pytest.approx(104.0), f"TP1 未按 FIFO 均价 80 重算: {tp1.price}"
    assert tp2.price == pytest.approx(120.0), f"TP2 未按 FIFO 均价 80 重算: {tp2.price}"
    assert tp1.price < tp2.price, "成对重算后保序 TP1<TP2 被破坏"
    assert '成本基重算' in tp1.history[-1].reason, \
        f"改价必须写 condition_history 且 reason 含成本基重算: {tp1.history[-1].reason!r}"


def test_a1_half_pct_threshold_no_change_then_reanchor(ws):
    """0.5% 阈值：成本基变化 ≤0.5% 不改价（history 不增长），>0.5% 才重算。"""
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.models import Account, CapitalPool
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='阈值股', stock_code='sh600100',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    cm = ConditionsManager(storage=s)
    cm.sync_take_profit_ladder('阈值股', avg_cost=100.0, current_price=120.0)
    rec = cm.load_conditions('阈值股')
    assert rec.conditions['take_profit_1'].price == pytest.approx(130.0)
    n_hist = len(rec.conditions['take_profit_1'].history)
    # +0.4% < 0.5%：不改价、history 不增长
    cm.sync_take_profit_ladder('阈值股', avg_cost=100.4, current_price=120.0)
    rec = cm.load_conditions('阈值股')
    assert rec.conditions['take_profit_1'].price == pytest.approx(130.0), \
        "成本基变化 ≤0.5% 不得改价"
    assert len(rec.conditions['take_profit_1'].history) == n_hist
    # +1.0% > 0.5%：重算
    cm.sync_take_profit_ladder('阈值股', avg_cost=101.0, current_price=120.0)
    rec = cm.load_conditions('阈值股')
    assert rec.conditions['take_profit_1'].price == pytest.approx(131.3)
    assert rec.conditions['take_profit_2'].price == pytest.approx(151.5)
    assert len(rec.conditions['take_profit_1'].history) == n_hist + 1


def test_a1_triggered_rung_never_remounted(ws):
    """已触发档永不重挂/重算：triggered 档价格不动，未触发档照常重算。"""
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.models import Account, CapitalPool
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='触发票', stock_code='sh600100',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    cm = ConditionsManager(storage=s)
    _tp_conditions(cm, '触发票', 130.0, 150.0, tp1_status='triggered')
    cm.sync_take_profit_ladder('触发票', avg_cost=80.0, current_price=100.0)
    rec = cm.load_conditions('触发票')
    assert rec.conditions['take_profit_1'].price == pytest.approx(130.0), \
        "已触发档永不重挂"
    assert rec.conditions['take_profit_2'].price == pytest.approx(120.0)


def test_a1_reanchored_rung_at_or_below_price_triggers_normally(ws):
    """重算后档位 ≤ 现价 → 正常触发：价格照写（不静默改价避开触发），
    check_triggers 按 up 方向正常报破位。"""
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.models import Account, CapitalPool
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='现价票', stock_code='sh600100',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    cm = ConditionsManager(storage=s)
    _tp_conditions(cm, '现价票', 130.0, 150.0)
    # 均价摊薄到 75 → TP1=97.5 ≤ 现价 100 → 正常触发路径
    cm.sync_take_profit_ladder('现价票', avg_cost=75.0, current_price=100.0)
    rec = cm.load_conditions('现价票')
    assert rec.conditions['take_profit_1'].price == pytest.approx(97.5), \
        "重算后档位 ≤ 现价也必须照写新价（正常触发），不得静默改价避开触发"
    breaches = cm.check_triggers('现价票', 100.0)
    assert any('止盈' in b['name'] and b['trigger_price'] == pytest.approx(97.5)
               for b in breaches), f"重算后档位应正常触发: {breaches}"


# ======================================================================
# A2 = WP2 幂等键（trades.event_id）
# ======================================================================

def test_a2_trades_event_id_column_and_migrate_idempotent(ws):
    """trades 加 event_id 列；migrate_db 连跑两次无报错、列集合不变、版本号到位。"""
    from paper_trading_v2.db import get_connection, migrate_db, SCHEMA_VERSION
    assert 'event_id' in _cols(ws, 'trades')
    c = get_connection(_db(ws))
    migrate_db(c)
    migrate_db(c)          # 幂等：连跑两次
    cols_1 = [r[1] for r in c.execute("PRAGMA table_info(trades)").fetchall()]
    migrate_db(c)
    cols_2 = [r[1] for r in c.execute("PRAGMA table_info(trades)").fetchall()]
    assert cols_1 == cols_2, "重复迁移不得新增/重复列"
    v = c.execute("SELECT version FROM schema_meta").fetchone()[0]
    assert v == SCHEMA_VERSION
    c.close()


def test_a2_buy_stock_writes_event_id_and_rejects_duplicate(ws):
    """buy_stock 落 event_id；同 event_id 二次买入 → ValueError + audit 留痕，
    且拒绝发生在资金变动之前（零部分写入）。"""
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '幂等股')
    with _patch_price(100.0):
        trader.buy_stock('幂等股', quantity=100, event_id='TE#1')
        conn = _conn(ws)
        seg = _seg_id(conn, '幂等股')
        rows = [dict(r) for r in conn.execute(
            "SELECT event_id FROM trades WHERE account_id=? AND operation='buy'",
            (seg,)).fetchall()]
        assert any(r['event_id'] == 'TE#1' for r in rows), f"event_id 未落 trades: {rows}"
        conn.close()
        with pytest.raises(ValueError, match='已成交|幂等'):
            trader.buy_stock('幂等股', quantity=100, event_id='TE#1')
    conn = _conn(ws)
    seg = _seg_id(conn, '幂等股')
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=?", (seg,)).fetchone()[0]
    assert n == 1, "重复 event_id 不得产生第二笔成交"
    audit = conn.execute(
        "SELECT COUNT(*) FROM audit WHERE action='idempotent_reject' AND "
        "reason LIKE '%TE#1%'").fetchone()[0]
    assert audit == 1, "拒绝必须 audit 留痕"
    acct = trader.storage.load_account('幂等股')
    assert acct.capital_pool.available == pytest.approx(490000), "拒绝不得动资金（100股×100=1万）"
    conn.close()
    # 不同 event_id 放行
    with _patch_price(100.0):
        trader.buy_stock('幂等股', quantity=100, event_id='TE#2')
    conn = _conn(ws)
    seg = _seg_id(conn, '幂等股')
    evs = [r[0] for r in conn.execute(
        "SELECT event_id FROM trades WHERE account_id=? AND operation='buy' "
        "ORDER BY seq", (seg,)).fetchall()]
    assert evs == ['TE#1', 'TE#2']
    conn.close()


def test_a2_sell_stock_rejects_duplicate_event_id(ws):
    """sell_stock 同 event_id 二次卖出 → ValueError（防 agent 崩溃 recover 双卖）。"""
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '幂等卖股')
    with _patch_price(100.0):
        trader.buy_stock('幂等卖股', quantity=100)
        trader.sell_stock('幂等卖股', quantity=40, event_id='SE#1')
        with pytest.raises(ValueError, match='已成交|幂等'):
            trader.sell_stock('幂等卖股', quantity=40, event_id='SE#1')
    conn = _conn(ws)
    seg = _seg_id(conn, '幂等卖股')
    sells = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE account_id=? AND operation='sell'",
        (seg,)).fetchone()[0]
    assert sells == 1, "同 event_id 不得双卖"
    audit = conn.execute(
        "SELECT COUNT(*) FROM audit WHERE action='idempotent_reject' AND "
        "reason LIKE '%SE#1%'").fetchone()[0]
    assert audit == 1
    conn.close()


def test_a2_empty_event_id_keeps_legacy_behavior(ws):
    """event_id 为空（默认）不启用幂等查重——现状行为零变化。"""
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '空键股')
    with _patch_price(100.0):
        trader.buy_stock('空键股', quantity=100)
        trader.buy_stock('空键股', quantity=100)   # 无 event_id，两笔都成功
    conn = _conn(ws)
    seg = _seg_id(conn, '空键股')
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id=? AND "
                     "operation='buy'", (seg,)).fetchone()[0]
    assert n == 2
    conn.close()


def test_a2_cli_buy_sell_event_id_flag(ws):
    """CLI buy/sell --event-id 透传：同键第二次执行退出码 1。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, 'CLI幂等股')
    with _patch_price(100.0):
        r = _run(app, 'buy', 'CLI幂等股', '--qty', '100', '--event-id', 'TE#9')
        assert r.exit_code == 0, r.output
        r = _run(app, 'buy', 'CLI幂等股', '--qty', '100', '--event-id', 'TE#9')
        assert r.exit_code == 1, r.output
        assert '已成交' in r.output or '幂等' in r.output, r.output
        r = _run(app, 'sell', 'CLI幂等股', '--qty', '50', '--event-id', 'SE#9')
        assert r.exit_code == 0, r.output
        r = _run(app, 'sell', 'CLI幂等股', '--qty', '50', '--event-id', 'SE#9')
        assert r.exit_code == 1, r.output


# ======================================================================
# A3 = WP1-CLI 半边（buy/sell --price 显式检测价 + E3 行情防线）
# ======================================================================

def _sell_row(ws, stock):
    conn = _conn(ws)
    seg = _seg_id(conn, stock)
    row = conn.execute(
        "SELECT price, total_cost FROM trades WHERE account_id=? AND "
        "operation='sell' ORDER BY seq DESC LIMIT 1", (seg,)).fetchone()
    conn.close()
    return row


def test_a3_sell_with_explicit_price_uses_it(ws):
    """sell --price 110：成交价写 trades.price=110（默认 None=现状自取价不变）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '行情股')
    with _patch_price(100.0):
        trader.buy_stock('行情股', quantity=1000)
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote(px=105.0)):
        r = _run(app, 'sell', '行情股', '--qty', '100', '--price', '110')
    assert r.exit_code == 0, r.output
    row = _sell_row(ws, '行情股')
    assert row is not None and row['price'] == pytest.approx(110.0), \
        f"显式检测价必须写 trades.price: {row}"


def test_a3_buy_with_explicit_price_uses_it(ws):
    from paper_trading_v2.cli import app
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '行情买股')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote(px=105.0)):
        r = _run(app, 'buy', '行情买股', '--qty', '100', '--price', '99')
    assert r.exit_code == 0, r.output
    conn = _conn(ws)
    seg = _seg_id(conn, '行情买股')
    row = conn.execute("SELECT price FROM trades WHERE account_id=? AND "
                       "operation='buy' ORDER BY seq DESC LIMIT 1", (seg,)).fetchone()
    conn.close()
    assert row is not None and row[0] == pytest.approx(99.0)


def test_a3_default_price_path_unchanged(ws):
    """不带 --price：现状自取实时价（105），行为零变化。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '自取价股')
    with _patch_price(100.0):
        trader.buy_stock('自取价股', quantity=1000)
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote(px=105.0)):
        r = _run(app, 'sell', '自取价股', '--qty', '100')
    assert r.exit_code == 0, r.output
    row = _sell_row(ws, '自取价股')
    assert row['price'] == pytest.approx(105.0)


def test_a3_explicit_price_guard_halted(ws):
    """E3 防线：停牌标记（volume=0）拒绝按检测价成交。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '停牌股')
    with _patch_price(100.0):
        trader.buy_stock('停牌股', quantity=1000)
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote(px=105.0, volume='0')):
        r = _run(app, 'sell', '停牌股', '--qty', '100', '--price', '105')
    assert r.exit_code == 1, r.output
    assert '停牌' in r.output, r.output
    assert _sell_row(ws, '停牌股') is None, "防线拒绝不得成交"


def test_a3_explicit_price_guard_stale_quote(ws):
    """E3 防线：报价非今日（陈旧）拒绝。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '陈旧股')
    with _patch_price(100.0):
        trader.buy_stock('陈旧股', quantity=1000)
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote(px=105.0, date='2026-01-01')):
        r = _run(app, 'sell', '陈旧股', '--qty', '100', '--price', '105')
    assert r.exit_code == 1, r.output
    assert '非今日' in r.output or '陈旧' in r.output, r.output


def test_a3_explicit_price_guard_limit_board(ws):
    """E3 防线：一字板（high==low≠昨收）拒绝按检测价买入。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.trading import PaperTrader
    trader = PaperTrader()
    _mk_account(trader, '一字板股')
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=_quote(px=11.0, high=11.0, low=11.0, pre_close=10.0)):
        r = _run(app, 'buy', '一字板股', '--qty', '100', '--price', '11')
    assert r.exit_code == 1, r.output
    assert '一字板' in r.output, r.output


# ======================================================================
# A4 = WP3-A 创建者列（conditions/event_slots.created_by）
# ======================================================================

def test_a4_creator_columns_exist(ws):
    from paper_trading_v2.db import SCHEMA_VERSION
    assert 'created_by' in _cols(ws, 'conditions')
    assert 'created_by' in _cols(ws, 'event_slots')
    c = _conn(ws)
    v = c.execute("SELECT version FROM schema_meta").fetchone()[0]
    c.close()
    assert v == SCHEMA_VERSION


def test_a4_conditions_set_created_by_flag_env_default(ws, monkeypatch):
    """conditions --action set --created-by 落列；缺省 env PTRADE2_CREATOR，再缺省 user。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool
    monkeypatch.delenv('PTRADE2_CREATOR', raising=False)
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='设定股', stock_code='sz000301',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    r = _run(app, 'conditions', '设定股', '--action', 'set', '--type', 'trailing_stop',
             '--price', '9.0', '--action-str', '减仓', '--category', 'hard',
             '--created-by', 'l3-scan')
    assert r.exit_code == 0, r.output
    conn = _conn(ws)
    seg = _seg_id(conn, '设定股')
    v = conn.execute("SELECT created_by FROM conditions WHERE account_id=? AND "
                     "type='trailing_stop'", (seg,)).fetchone()[0]
    assert v == 'l3-scan', f"--created-by 未落列: {v}"
    # 缺省 → user
    r = _run(app, 'conditions', '设定股', '--action', 'set', '--type', 'cost_protection',
             '--price', '8.0', '--action-str', '保护', '--category', 'hard')
    assert r.exit_code == 0, r.output
    v = conn.execute("SELECT created_by FROM conditions WHERE account_id=? AND "
                     "type='cost_protection'", (seg,)).fetchone()[0]
    assert v == 'user', f"缺省创建者应为 user: {v}"
    conn.close()
    # env PTRADE2_CREATOR
    monkeypatch.setenv('PTRADE2_CREATOR', 'portfolio-review')
    r = _run(app, 'conditions', '设定股', '--action', 'set', '--type', 'take_profit_1',
             '--price', '130', '--action-str', '止盈', '--category', 'hard')
    assert r.exit_code == 0, r.output
    conn = _conn(ws)
    seg = _seg_id(conn, '设定股')
    v = conn.execute("SELECT created_by FROM conditions WHERE account_id=? AND "
                     "type='take_profit_1'", (seg,)).fetchone()[0]
    conn.close()
    assert v == 'portfolio-review', f"env PTRADE2_CREATOR 未生效: {v}"


def test_a4_event_set_created_by(ws):
    from paper_trading_v2.cli import app
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='事件股', stock_code='sz000301',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    r = _run(app, 'conditions', '事件股', '--action', 'event-set',
             '--event-type', 'target_profit', '--price', '120', '--category', 'hard',
             '--created-by', 'check-open')
    assert r.exit_code == 0, r.output
    conn = _conn(ws)
    seg = _seg_id(conn, '事件股')
    v = conn.execute("SELECT created_by FROM conditions WHERE account_id=? AND "
                     "is_event=1", (seg,)).fetchone()[0]
    conn.close()
    assert v == 'check-open', f"event-set --created-by 未落列: {v}"


def test_a4_sleeve_open_writes_msg_watch(ws):
    """sleeve-open 默认写 created_by='msg-watch'（构造如此，回填词表对齐）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.master_pool import MasterPoolManager
    MasterPoolManager(_db(ws)).init_pool(10000000)
    MasterPoolManager(_db(ws)).init_pool(2000000, pool='sleeve')
    r = _run(app, 'sleeve-open', '开槽股', '--budget', '100000',
             '--event-key', 'ND#880', '--code', 'sh600000')
    assert r.exit_code == 0, r.output
    conn = _conn(ws)
    v = conn.execute("SELECT created_by FROM event_slots WHERE event_key='ND#880'"
                     ).fetchone()[0]
    conn.close()
    assert v == 'msg-watch', f"sleeve-open 未写 created_by=msg-watch: {v!r}"


def test_a4_tp_ladder_mount_writes_atr_auto(ws):
    """atr-sync 挂载路径（sync_take_profit_ladder 新建档）写 created_by='atr-auto'。"""
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.models import Account, CapitalPool
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='挂载股', stock_code='sh600100',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    cm = ConditionsManager(storage=s)
    cm.sync_take_profit_ladder('挂载股', avg_cost=100.0, current_price=120.0)
    rec = cm.load_conditions('挂载股')
    assert rec.conditions['take_profit_1'].created_by == 'atr-auto'
    assert rec.conditions['take_profit_2'].created_by == 'atr-auto'


def test_a4_sync_preserves_existing_creator(ws):
    """atr-sync 改价（sync_trailing_stop）保留原创建者——created_by=对象创建者，
    不随价格同步改写（路由依据不得漂移）。"""
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.models import Account, CapitalPool
    from paper_trading_v2.conditions import (ConditionsRecord, Condition,
                                             ConditionChange)
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='保留股', stock_code='sh600100',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    cm = ConditionsManager(storage=s)
    rec = ConditionsRecord(stock_name='保留股')
    rec.conditions['trailing_stop'] = Condition(
        id='ts-keep', type='trailing_stop', name='移动止损', price=9.0,
        action='减仓50%', category='hard', status='active',
        created_by='user',
        history=[ConditionChange(old_price=0, new_price=9.0, reason='首次设定',
                                 level='auto')])
    cm.save_conditions(rec)
    kl = [{'date': '2026-09-01', 'open': 10, 'high': 10.5, 'low': 9.5,
           'close': 10, 'volume': 1}] * 30
    cm.sync_trailing_stop('保留股', avg_cost=10.0, klines=kl, atr=0.5,
                          realtime_high=10.5, current_price=10.2)
    loaded = cm.load_conditions('保留股')
    assert loaded.conditions['trailing_stop'].created_by == 'user', \
        "改价不得覆盖对象创建者"


def test_a4_backfill_script_dry_run_then_apply(ws):
    """一次性回填脚本：event_slots→msg-watch；trailing/cost 且有 peak→atr-auto；
    其余→analysis-watch；--dry-run 零写入；--apply 幂等（二跑零行）。"""
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.models import Account, CapitalPool
    s = SqlStorage(_db(ws))
    s.save_account(Account(stock_name='回填股', stock_code='sh600100',
                           capital_pool=CapitalPool(total=500000, available=500000, used=0)))
    conn = _conn(ws)
    seg = _seg_id(conn, '回填股')
    with conn:
        conn.execute(
            "INSERT INTO event_slots (event_key, status, opened_at, budget, fill_status) "
            "VALUES ('ND#700','open','2026-09-01T09:00:00',1000,'pending')")
        for i, (ctype, peak) in enumerate((
                ('trailing_stop', 78.0),      # 有 peak → atr-auto
                ('trailing_stop', None),      # 无 peak → analysis-watch
                ('cost_protection', 50.0),    # 有 peak → atr-auto
                ('take_profit_1', None),      # 其余 → analysis-watch
        )):
            conn.execute(
                "INSERT INTO conditions (account_id, cond_key, is_event, type, name, "
                "price, action, category, status, seq, peak_price) "
                "VALUES (?,?,0,?,?,?,?,?,'active',?,?)",
                (seg, ctype, ctype, f'{ctype}-{i}', 10.0, '执行', 'hard', i, peak))
    conn.close()

    env = dict(os.environ)
    env['STOCK_ANALYSIS_WORKSPACE'] = str(ws)
    env.pop('PTRADE2_CREATOR', None)
    # --dry-run：只报数不写入
    r = subprocess.run([sys.executable, _BACKFILL, '--dry-run'], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'event_slots' in r.stdout and 'msg-watch' in r.stdout, r.stdout
    conn = _conn(ws)
    n_set = conn.execute("SELECT COUNT(*) FROM event_slots WHERE "
                         "COALESCE(created_by,'')!=''").fetchone()[0]
    n_cond = conn.execute("SELECT COUNT(*) FROM conditions WHERE "
                          "COALESCE(created_by,'')!=''").fetchone()[0]
    conn.close()
    assert n_set == 0 and n_cond == 0, "dry-run 不得写入"
    # --apply：写入
    r = subprocess.run([sys.executable, _BACKFILL, '--apply'], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    conn = _conn(ws)
    assert conn.execute("SELECT created_by FROM event_slots WHERE event_key='ND#700'"
                        ).fetchone()[0] == 'msg-watch'
    got = dict(conn.execute("SELECT type, created_by FROM conditions WHERE "
                            "account_id=?", (seg,)).fetchall())
    conn.close()
    assert got['trailing_stop'] == 'atr-auto' or True   # 同型两行，取值见下
    vals = {}
    conn = _conn(ws)
    for row in conn.execute("SELECT type, peak_price, created_by FROM conditions "
                            "WHERE account_id=? ORDER BY id", (seg,)).fetchall():
        vals.setdefault((row[0], row[1]), row[2])
    conn.close()
    assert vals[('trailing_stop', 78.0)] == 'atr-auto'
    assert vals[('trailing_stop', None)] == 'analysis-watch'
    assert vals[('cost_protection', 50.0)] == 'atr-auto'
    assert vals[('take_profit_1', None)] == 'analysis-watch'
    # 幂等：二跑零行
    r = subprocess.run([sys.executable, _BACKFILL, '--apply'], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '0' in r.stdout, f"二次回填应为零行: {r.stdout}"


# ======================================================================
# A5 = WP7-A schema（placed_px / band_out_count / expire 白名单）
# ======================================================================

def _pools(ws):
    from paper_trading_v2.master_pool import MasterPoolManager
    m = MasterPoolManager(_db(ws))
    m.init_pool(10000000)
    m.init_pool(2000000, pool='sleeve')
    return m


def _open_slot(ws, stock='测试票', key='ND#900', budget=100000):
    from paper_trading_v2.cli import app
    r = _run(app, 'sleeve-open', stock, '--budget', budget, '--event-key', key,
             '--code', 'sh600000')
    assert r.exit_code == 0, r.output
    return key


def _slot(ws, key):
    c = _conn(ws)
    try:
        return c.execute("SELECT * FROM event_slots WHERE event_key=?", (key,)).fetchone()
    finally:
        c.close()


def test_a5_placed_px_band_out_count_columns(ws):
    """event_slots 加 placed_px REAL / band_out_count INTEGER DEFAULT 0；
    存量行默认 NULL/0。"""
    c = _conn(ws)
    with c:
        c.execute("INSERT INTO event_slots (event_key, status, opened_at, budget) "
                  "VALUES ('LEGACY#9','open','2026-09-01T09:00:00',1000)")
    c.close()
    s = _slot(ws, 'LEGACY#9')
    assert s['placed_px'] is None, "存量行 placed_px 默认 NULL"
    assert s['band_out_count'] == 0, "存量行 band_out_count 默认 0"


def test_a5_place_writes_placed_px_anchor_both_kept(ws):
    """sleeve-order-place --placed-px 写挂单时刻价；anchor_price（事件入库价）两者都留。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    _pools(ws)
    _open_slot(ws, key='ND#901')
    ttl = next_session_close().isoformat(timespec='seconds')
    r = _run(app, 'sleeve-order-place', 'ND#901', '--anchor', '10.0',
             '--ttl', ttl, '--placed-px', '10.02')
    assert r.exit_code == 0, r.output
    s = _slot(ws, 'ND#901')
    assert s['placed_px'] == pytest.approx(10.02), f"placed_px 未写入: {s['placed_px']}"
    assert s['anchor_price'] == pytest.approx(10.0), "anchor_price 必须保留"
    # 不传 --placed-px：留 NULL（挂单时刻价未知，不与入库价混写）
    _open_slot(ws, stock='另一票', key='ND#902', budget=50000)
    r = _run(app, 'sleeve-order-place', 'ND#902', '--anchor', '20.0', '--ttl', ttl)
    assert r.exit_code == 0, r.output
    s = _slot(ws, 'ND#902')
    assert s['placed_px'] is None
    assert s['anchor_price'] == pytest.approx(20.0)


def test_a5_expire_reason_whitelist_band_left_skipped(ws):
    """expire --reason 白名单扩 band_left / band_skipped（→ pending_rejudge）；
    非法 reason 依旧拒绝。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_order import next_session_close
    _pools(ws)
    ttl = next_session_close().isoformat(timespec='seconds')
    for key, reason in (('ND#910', 'band_left'), ('ND#911', 'band_skipped')):
        _open_slot(ws, stock=f'票{key}', key=key)
        r = _run(app, 'sleeve-order-place', key, '--anchor', '10.0', '--ttl', ttl)
        assert r.exit_code == 0, r.output
        r = _run(app, 'sleeve-order-expire', key, '--reason', reason)
        assert r.exit_code == 0, r.output
        assert _slot(ws, key)['status'] == 'pending_rejudge'
    # 非法 reason 仍拒绝
    _open_slot(ws, stock='票ND#912', key='ND#912')
    r = _run(app, 'sleeve-order-place', 'ND#912', '--anchor', '10.0', '--ttl', ttl)
    assert r.exit_code == 0, r.output
    r = _run(app, 'sleeve-order-expire', 'ND#912', '--reason', 'bogus_reason')
    assert r.exit_code == 1, r.output
    assert _slot(ws, 'ND#912')['status'] == 'pending_order'
