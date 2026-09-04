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
    # M1.8/R1：sleeve-pool-init=从主池配对划拨 2M，主池 base 10M→8M（下文 main_free 断言同口径）
    _run(app, 'sleeve-pool-init', '--amount', '2000000')
    return env


# M1.8/R1 后的主池 base（10M 注入 − 2M 划拨给消息池）
MAIN_BASE = 8_000_000


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
    # fill 走 --allow-same-day：同进程"开槽→立即成交"状态机验证需豁免 T+1 门
    # （T+1 门本身及豁免路径由 test_sleeve_t1_gate 专测锁住）
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=[{'date': '2026-08-01', 'open': 10, 'high': 11, 'low': 9,
                               'close': 10, 'volume': 1}] * 30):
        r = _run(app, 'sleeve-fill', '--allow-same-day', '--price', '成员甲=10.0', '--price', '成员乙=20.0')
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
    assert abs(main_free - (MAIN_BASE - 100000)) < 1   # 主池承接 10 万成本（base=8M，M1.8/R1 划拨后）
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


# ============ NEWS 入池硬门 + 同票双组默认拒绝（第四.8，CLI 全路径覆盖） ============
# 密闭约定：只走 STOCK_ANALYSIS_WORKSPACE（env/sleeve_ready），绝不设 STOCK_POOL_DB
# （历史上该变量不被 CLI 解析，曾把测试写进生产库）。

_KLINES_60 = [{'date': '2026-01-05', 'open': 1, 'high': 1, 'low': 1,
               'close': 1}] * 60


def _news_add(app, stock, code, event_key, kind='policy', *extra):
    """NEWS 收编 CLI（G2 次新否决需日K ≥41 根 → 注入 60 根常量 K 线，不触网）。"""
    from unittest.mock import patch as _patch
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=_KLINES_60):
        return _run(app, 'watchlist-add', stock, '--code', code, '--strategy', 'NEWS',
                    '--event-key', event_key, '--news-kind', kind, *extra)


def test_watchlist_add_news_rejected_when_tech_open_segment(sleeve_ready):
    """守卫 A（抢档）：技术组有 open 段 → NEWS 收编默认拒绝（第四.8）。

    技术组 open 段持有者走 ④动量/甜点加仓，不走消息组；--force 才放行。
    """
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    r = _run(app, 'watchlist-add', '抢档票', '--code', 'sh600010', '--strategy', 'L2')
    assert r.exit_code == 0, r.output
    r = _run(app, 'master-pool-allocate', '抢档票', '--amount', '100000', '--reason', '建仓')
    assert r.exit_code == 0, r.output                       # allocate 即升 L1 + open 段
    r = _news_add(app, '抢档票', 'sh600010', 'ND#A1')
    assert r.exit_code == 1, r.output
    assert '第四.8' in r.output and '有 open 段禁 NEWS 收编' in r.output, r.output
    conn = get_connection(_db(sleeve_ready))
    strat = conn.execute("SELECT strategy FROM pool WHERE stock='抢档票'").fetchone()[0]
    conn.close()
    assert strat == 'L1'                                    # 拒绝=零副作用，档位未动
    # force 豁免：显式 --force 放行且 watchlog 留痕
    r = _news_add(app, '抢档票', 'sh600010', 'ND#A1', 'policy', '--force')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    reason = conn.execute("SELECT reason FROM watchlog WHERE stock='抢档票' "
                          "ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert 'force' in (reason or '')


def test_watchlist_add_news_allowed_without_segment(sleeve_ready):
    """放行态不变：L2 行无 open 段 → NEWS 入池成功（逐字节原行为）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    r = _run(app, 'watchlist-add', '无段观察票', '--code', 'sh600011', '--strategy', 'L2')
    assert r.exit_code == 0, r.output
    r = _news_add(app, '无段观察票', 'sh600011', 'ND#A2')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    row = dict(conn.execute("SELECT * FROM pool WHERE stock='无段观察票'").fetchone())
    conn.close()
    assert row['strategy'] == 'NEWS' and row['event_key'] == 'ND#A2'


def test_watchlist_add_news_dup_blocked_when_in_slot(sleeve_ready):
    """守卫 B（重复入池/断链）：NEWS active 且已在活跃槽 → 再次 NEWS 入池拒绝；
    --force 放行且 watchlog reason 带 force。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    r = _news_add(app, '槽内票', 'sh600012', 'ND#A3')
    assert r.exit_code == 0, r.output
    r = _run(app, 'sleeve-open', '槽内票', '--budget', '100000', '--event-key', 'ND#A3',
             '--news-kind', 'policy', '--code', 'sh600012')
    assert r.exit_code == 0, r.output
    r = _news_add(app, '槽内票', 'sh600012', 'ND#A3B')      # 换新键再来=断链
    assert r.exit_code == 1, r.output
    assert '已在槽' in r.output and '派生波' in r.output, r.output
    conn = get_connection(_db(sleeve_ready))
    key = conn.execute("SELECT event_key FROM pool WHERE stock='槽内票'").fetchone()[0]
    conn.close()
    assert key == 'ND#A3'                                   # 拒绝未改键
    r = _news_add(app, '槽内票', 'sh600012', 'ND#A3B', 'policy', '--force')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    reason = conn.execute("SELECT reason FROM watchlog WHERE stock='槽内票' "
                          "ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert 'force' in (reason or '')


def test_watchlist_add_news_allowed_not_in_slot(sleeve_ready):
    """M3 晨审合法路径不变：NEWS active 但无槽 → 重复 watchlist-add 放行、event_key 更新。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    assert _news_add(app, '晨审票', 'sh600013', 'ND#A4').exit_code == 0
    r = _news_add(app, '晨审票', 'sh600013', 'ND#A4B')      # 催化换键（无槽）
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    row = dict(conn.execute("SELECT strategy, event_key FROM pool WHERE stock='晨审票'")
               .fetchone())
    conn.close()
    assert row == {'strategy': 'NEWS', 'event_key': 'ND#A4B'}


def test_sleeve_open_conflict_rejected_by_default(sleeve_ready):
    """sleeve-open 同票双组冲突：默认拒绝（第四.8），--force 才开槽。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    assert _run(app, 'watchlist-add', '双组冲突票', '--code', 'sh600014',
                '--strategy', 'L2').exit_code == 0
    r = _run(app, 'master-pool-allocate', '双组冲突票', '--amount', '100000',
             '--reason', '技术组建段')
    assert r.exit_code == 0, r.output
    r = _run(app, 'sleeve-open', '双组冲突票', '--budget', '100000', '--event-key',
             'ND#A5', '--news-kind', 'policy', '--code', 'sh600014')
    assert r.exit_code == 1, r.output
    assert '第四.8' in r.output, r.output
    conn = get_connection(_db(sleeve_ready))
    assert conn.execute("SELECT COUNT(*) FROM event_slots WHERE event_key='ND#A5'"
                        ).fetchone()[0] == 0                # 拒绝=不建槽
    free = conn.execute("SELECT free FROM sleeve_ledger").fetchone()[0]
    conn.close()
    assert abs(free - 2000000) < 1                          # 拒绝=不扣钱
    r = _run(app, 'sleeve-open', '双组冲突票', '--budget', '100000', '--event-key',
             'ND#A5', '--news-kind', 'policy', '--code', 'sh600014', '--force')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    assert conn.execute("SELECT COUNT(*) FROM event_slots WHERE event_key='ND#A5'"
                        ).fetchone()[0] == 1
    conn.close()


def test_cli_sleeve_fill_t1_gate(sleeve_ready, monkeypatch):
    """T+1 硬门回归锁（cron-audit 2026-09-02 P1，2026-09-02 earliest_fill 改造后
    语义保留）：newsdb 不可达 fail-closed 回退旧口径——今日开槽批量跳过/指定键
    拒绝/隔夜槽放行成交。
    （STOCK_NEWS_DB 指向不存在路径=锁死 fallback 旧行为路径，不依赖真实 newsdb。）"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection
    from paper_trading_v2.sleeve_open import SleeveOpener
    from unittest.mock import patch as _patch
    monkeypatch.setenv('STOCK_NEWS_DB', '/nonexistent/news_for_t1_gate.db')
    SleeveOpener(_db(sleeve_ready)).open_slot(['T1票'], budget=100000,
                                              event_key='ND#960', news_kind='policy')
    kl = [{'date': '2026-08-01', 'open': 10, 'high': 11, 'low': 9,
           'close': 10, 'volume': 1}] * 30
    # ① 今日开槽：批量 fill 须跳过（槽保持 pending、不碰钱）
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=kl):
        r = _run(app, 'sleeve-fill', '--price', 'T1票=10.0')
    assert r.exit_code == 0 and '成交时点门跳过 ND#960' in r.output, r.output
    conn = get_connection(_db(sleeve_ready))
    assert conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#960'"
                        ).fetchone()[0] == 'pending'
    conn.close()
    # ② 指定 event-key 打今日槽：拒绝退出码 1
    r = _run(app, 'sleeve-fill', '--event-key', 'ND#960', '--price', 'T1票=10.0')
    assert r.exit_code == 1 and '成交时点门拒绝' in r.output
    # ③ opened_at 回填为昨日（=隔夜槽）：批量 fill 正常成交（明晨真实路径）。
    # ATR 显式注入：mock 常量价 K 线 TR=0 → R7 判 ATR 解析失败拒单，与本锁无关
    conn = get_connection(_db(sleeve_ready))
    with conn:
        conn.execute("UPDATE event_slots SET opened_at='2000-01-01T09:00:00' "
                     "WHERE event_key='ND#960'")
    conn.close()
    with _patch('paper_trading_v2.kline_fetcher.KLineDataFetcher.fetch_kline_data',
                return_value=kl):
        r = _run(app, 'sleeve-fill', '--price', 'T1票=10.0', '--atr', 'T1票=0.5')
    assert r.exit_code == 0 and 'T1票' in r.output and '买' in r.output, r.output
    conn = get_connection(_db(sleeve_ready))
    fs = conn.execute("SELECT fill_status FROM event_slots WHERE event_key='ND#960'"
                      ).fetchone()[0]
    conn.close()
    assert fs == 'filled'


def test_cli_sleeve_open_place_one_shot(sleeve_ready):
    """b：sleeve-open --place 开槽即挂单（v12 消灭开槽未挂断链）。"""
    from paper_trading_v2.cli import app
    from paper_trading_v2.db import get_connection

    # 无 --place → 槽 open/pending 且未挂单（保持向后兼容）
    r = _run(app, 'sleeve-open', '开单甲', '--budget', '100000',
             '--event-key', 'ND#811', '--news-kind', 'policy',
             '--code', 'sh600811', '--reason', '开槽即挂单测试')
    assert r.exit_code == 0, r.output
    conn = get_connection(_db(sleeve_ready))
    slot = dict(conn.execute("SELECT status, fill_status, order_id, band_min "
                             "FROM event_slots WHERE event_key='ND#811'").fetchone())
    conn.close()
    assert slot['status'] == 'open' and slot['fill_status'] == 'pending'
    assert slot['order_id'] is None and slot['band_min'] is None

    # --place 缺 --anchor → 拒（fail-closed）
    r = _run(app, 'sleeve-open', '开单乙', '--budget', '100000',
             '--event-key', 'ND#812', '--news-kind', 'policy',
             '--code', 'sh600812', '--place')
    assert r.exit_code == 1 and '--anchor' in r.output, r.output

    # --place 齐参 → open + 自动挂单 pending_order
    # 2026-09-04 时间敏感修复：TTL 必须=下一交易节收盘（fail-closed 校验），写死日期/随意未来值都会拒
    from paper_trading_v2.sleeve_order import next_session_close
    _ttl = next_session_close().isoformat(timespec='seconds')
    r = _run(app, 'sleeve-open', '开单乙', '--budget', '100000',
             '--event-key', 'ND#812', '--news-kind', 'policy',
             '--code', 'sh600812', '--place', '--anchor', '10.0',
             '--ttl', _ttl, '--reason', '开槽即挂单')
    assert r.exit_code == 0, r.output
    assert '开槽即挂单' in r.output and 'pending_order' in r.output, r.output
    conn = get_connection(_db(sleeve_ready))
    slot = dict(conn.execute("SELECT status, fill_status, order_id, band_min, band_max "
                             "FROM event_slots WHERE event_key='ND#812'").fetchone())
    conn.close()
    assert slot['status'] == 'pending_order' and slot['fill_status'] == 'pending'
    assert slot['order_id'] and abs(slot['band_min'] - 9.5) < 1e-9 \
        and abs(slot['band_max'] - 10.5) < 1e-9
