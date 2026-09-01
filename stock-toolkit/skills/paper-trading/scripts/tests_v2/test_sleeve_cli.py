"""CLI 层测试（sleeve-m1）：sleeve 命令组 / 闸门 callback / watchlist-add 新参数与 G2 / 槽状态机"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch
from typer.testing import CliRunner

runner = CliRunner()


def _db(ws):
    return ws / 'master_pool.db'


def _run(app, *args):
    r = runner.invoke(app, [str(a) for a in args])
    return r


@pytest.fixture(autouse=True)
def no_network(ws):
    """密闭测试：价格/ATR 抓取不触网（R7 价差防线参照昨收，未 mock 会拿真实行情拒单）"""
    with patch('paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
               return_value=None):
        yield


@pytest.fixture
def env(ws, monkeypatch):
    """CLI 默认走 STOCK_ANALYSIS_WORKSPACE 环境变量（config.get_workspace_config）"""
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', str(ws))
    return ws


@pytest.fixture
def sleeve_ready(env):
    from paper_trading_v2.cli import app
    _run(app, 'master-pool-init', '--amount', '10000000')
    _run(app, 'sleeve-pool-init', '--amount', '2000000')
    return env


def test_cli_sleeve_pool_init_and_show(sleeve_ready):
    from paper_trading_v2.cli import app
    r = _run(app, 'sleeve-show')
    assert r.exit_code == 0, r.output
    assert '消息池' in r.output and '2,000,000' in r.output


def test_cli_sleeve_open_fill_close_state_machine(sleeve_ready):
    """槽状态机：sleeve-open → sleeve-fill → 部分成员止损 → partial → 全清 closed。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    from unittest.mock import patch as _patch

    r = _run(app, 'sleeve-open', '成员甲', '成员乙', '--budget', '200000',
             '--event-key', 'ND#501', '--news-kind', 'policy', '--code', 'sh600001',
             '--code', 'sh600002', '--reason', '政策事件')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    slot = dict(conn.execute("SELECT * FROM event_slots WHERE event_key='ND#501'").fetchone())
    assert slot['status'] == 'open' and slot['fill_status'] == 'pending'
    assert slot['budget'] == 200000
    weights = [dict(x) for x in conn.execute(
        "SELECT stock, weight FROM event_slot_members WHERE event_key='ND#501'")]
    assert {w['stock'] for w in weights} == {'成员甲', '成员乙'}
    assert all(abs(w['weight'] - 0.5) < 1e-9 for w in weights)
    ledger_free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    conn.close()
    assert ledger_free == 1800000

    # fill（开盘价注入 + ATR 注入，挂三件套）
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=[{'date': '2026-08-01', 'open': 10, 'high': 11, 'low': 9,
                               'close': 10, 'volume': 1}] * 30):
        r = _run(app, 'sleeve-fill', '--price', '成员甲=10.0', '--price', '成员乙=20.0')
    assert r.exit_code == 0, r.output
    assert '成员甲' in r.output and '成员乙' in r.output
    conn = get_connection(_db(sleeve_ready))
    slot = dict(conn.execute("SELECT * FROM event_slots WHERE event_key='ND#501'").fetchone())
    assert slot['fill_status'] == 'filled'
    # 三件套挂载：成本保护 = 开盘价 − 2.0×ATR
    conds = [dict(x) for x in conn.execute(
        "SELECT type, price, category, status FROM conditions")]
    assert any(c['type'] == 'cost_protection' for c in conds)
    assert any(c['type'] == 'trailing_stop' for c in conds)
    conn.close()

    # 部分成员止损清仓（模拟成员乙清仓）→ 槽 partial
    from paper_trading_v2.trading import PaperTrader
    import os
    from paper_trading_v2.storage import SqlStorage
    s = SqlStorage(_db(sleeve_ready))
    acct = s.load_account('成员乙')
    acct.positions = []
    s.save_account(acct)
    conn = get_connection(_db(sleeve_ready))
    from paper_trading_v2.master_pool import MasterPoolManager
    MasterPoolManager(_db(sleeve_ready)).release('成员乙', reason='止损', pool='sleeve')
    slot = dict(conn.execute("SELECT status FROM event_slots WHERE event_key='ND#501'"
                             ).fetchone())
    conn.close()
    assert slot['status'] == 'partial'                      # 仍占坑

    # 全清 → closed 释放
    s = SqlStorage(_db(sleeve_ready))
    acct = s.load_account('成员甲')
    acct.positions = []
    s.save_account(acct)
    MasterPoolManager(_db(sleeve_ready)).release('成员甲', reason='清仓', pool='sleeve')
    conn = get_connection(_db(sleeve_ready))
    slot = dict(conn.execute("SELECT status, closed_at FROM event_slots WHERE event_key='ND#501'"
                             ).fetchone())
    active = conn.execute("SELECT COUNT(*) FROM event_slots WHERE status IN ('open','partial')"
                          ).fetchone()[0]
    conn.close()
    assert slot['status'] == 'closed' and slot['closed_at']
    assert active == 0                                       # 坑已释放


def test_cli_gate_blocks_conditions_write_via_command(sleeve_ready):
    """CLI 闸门：news 账户调 conditions 写 → 报错 + shadow_log gate_violation。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    from paper_trading_v2.sleeve_open import SleeveOpener
    SleeveOpener(_db(sleeve_ready)).open_slot(['闸门票'], budget=100000,
                                              event_key='ND#601', news_kind='policy')
    r = _run(app, 'conditions', '闸门票', '--action', 'set', '--type', 'trailing_stop',
             '--price', '9.0', '--action-str', '减仓', '--category', 'hard')
    assert r.exit_code == 1, r.output
    assert '消息组' in r.output
    conn = get_connection(_db(sleeve_ready))
    n = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='gate_violation'"
                     ).fetchone()[0]
    conn.close()
    assert n >= 1
    # 读操作放行
    r = _run(app, 'conditions', '闸门票', '--action', 'event-list')
    assert r.exit_code == 0, r.output


def test_cli_gate_blocks_buy_on_news_account(sleeve_ready):
    from paper_trading_v2.cli import app
    from paper_trading_v2.sleeve_open import SleeveOpener
    SleeveOpener(_db(sleeve_ready)).open_slot(['闸门票2'], budget=100000,
                                              event_key='ND#602', news_kind='policy')
    r = _run(app, 'buy', '闸门票2', '--qty', '100')
    assert r.exit_code == 1, r.output
    assert '消息组' in r.output


def test_cli_allocate_rejects_news_strategy_stock(sleeve_ready):
    """技术组禁直接买 NEWS 票（须走迁移桥）。"""
    from paper_trading_v2.cli import app
    _run(app, 'watchlist-add', 'NEWS票', '--code', 'sh600003', '--strategy', 'NEWS',
         '--event-key', 'ND#603', '--news-kind', 'policy')
    r = _run(app, 'master-pool-allocate', 'NEWS票', '--amount', '100000', '--reason', '绕过')
    assert r.exit_code == 1, r.output
    assert '迁移桥' in r.output


def test_cli_watchlist_add_news_params(sleeve_ready):
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    from unittest.mock import patch as _patch
    # 60 根日K → 通过 G2
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=[{'date': '2026-01-05', 'open': 1, 'high': 1, 'low': 1,
                               'close': 1}] * 60):
        r = _run(app, 'watchlist-add', '收编票', '--code', 'sh600004', '--strategy', 'NEWS',
                 '--event-key', 'ND#700', '--news-kind', 'earnings', '--reason', '中报超预期')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    row = dict(conn.execute("SELECT * FROM pool WHERE stock='收编票'").fetchone())
    log = dict(conn.execute("SELECT event_key, news_kind FROM watchlog WHERE stock='收编票' "
                            "ORDER BY id DESC LIMIT 1").fetchone())
    conn.close()
    assert row['event_key'] == 'ND#700' and row['refresh_cadence'] == 'event'
    assert log['event_key'] == 'ND#700' and log['news_kind'] == 'earnings'


def test_cli_watchlist_add_rejects_new_listing_for_news(sleeve_ready):
    """上市 <40 交易日：NEWS 收编硬拒绝；技术组仅提示不拒绝。"""
    from paper_trading_v2.cli import app
    from unittest.mock import patch as _patch
    bars = [{'date': '2026-08-25', 'open': 1, 'high': 1, 'low': 1, 'close': 1}] * 5
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=bars):
        r = _run(app, 'watchlist-add', '次新票', '--code', 'sz301999', '--strategy', 'NEWS',
                 '--event-key', 'ND#800', '--news-kind', 'tech_catalyst')
        assert r.exit_code == 1, r.output
        assert 'G2' in r.output
        # 技术组仅提示
        r = _run(app, 'watchlist-add', '次新票', '--code', 'sz301999', '--strategy', 'L2',
                 '--reason', '技术面观察')
        assert r.exit_code == 0, r.output
        assert 'G2' in r.output and '仅提示' in r.output


def test_cli_watchlist_remove_archive_flag(sleeve_ready):
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    _run(app, 'watchlist-add', '归档CLI票', '--code', 'sh600005', '--strategy', 'L2')
    r = _run(app, 'watchlist-remove', '归档CLI票', '--archive', '--reason', '清仓归档')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    row = dict(conn.execute("SELECT pool_status, archived_at FROM pool WHERE stock='归档CLI票'"
                            ).fetchone())
    conn.close()
    assert row['pool_status'] == 'archived' and row['archived_at']


def test_cli_sleeve_migrate_command(sleeve_ready):
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    from paper_trading_v2.sleeve_open import SleeveOpener
    SleeveOpener(_db(sleeve_ready)).open_slot(['迁移票'], budget=100000,
                                              event_key='ND#900', news_kind='sentiment',
                                              code_map={'迁移票': 'sh600006'})
    SleeveOpener(_db(sleeve_ready)).fill_pending(event_key='ND#900',
                                                 open_prices={'迁移票': 10.0},
                                                 skip_conditions=True)
    r = _run(app, 'sleeve-migrate', '迁移票', '--reason', 'V11 资格+未触发否决项')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    slot = dict(conn.execute("SELECT status, topup_locked, orig_budget FROM event_slots "
                             "WHERE event_key='ND#900'").fetchone())
    shadow = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='bridge_track' "
                          "AND key='ND#900'").fetchone()[0]
    main_free = conn.execute("SELECT free FROM pool_ledger").fetchone()[0]
    sleeve_free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    conn.close()
    assert slot['status'] == 'migrated' and slot['topup_locked'] == 1
    assert slot['orig_budget'] == 100000
    assert shadow >= 1
    assert abs(main_free - 9900000) < 1            # 主池承接 10 万成本
    assert abs(sleeve_free - 2000000) < 1          # 消息池回款 10 万（1.9M + 结转 0.1M）


def test_cli_sleeve_cancel_drop_order(sleeve_ready):
    """弃单：资金回消息池 + 影子账#1 + 坑释放。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    from paper_trading_v2.sleeve_open import SleeveOpener
    SleeveOpener(_db(sleeve_ready)).open_slot(['弃单票'], budget=100000,
                                              event_key='ND#950', news_kind='policy')
    r = _run(app, 'sleeve-cancel', 'ND#950', '--reason', 'TTL 过期停牌')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    slot = dict(conn.execute("SELECT status, fill_status FROM event_slots WHERE event_key='ND#950'"
                             ).fetchone())
    drop = conn.execute("SELECT COUNT(*) FROM shadow_log WHERE kind='drop_order' AND key='ND#950'"
                        ).fetchone()[0]
    free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    conn.close()
    assert slot['status'] == 'archived' and slot['fill_status'] == 'cancelled'
    assert drop == 1
    assert abs(free - 2000000) < 1
