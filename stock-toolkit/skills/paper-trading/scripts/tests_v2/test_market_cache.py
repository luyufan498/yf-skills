"""P2 market.db 日K缓存库（raw + 除权事件）：mock 腾讯网络层，tmp 工作区，零真实网络。

跑法：scripts/.venv/bin/python -m pytest tests_v2/test_market_cache.py -q
覆盖：首次抓取落库 / 二次纯读库（网络0调用）/ 缺口补抓只补缺 bar /
TTL 超期全量重建 / 除权 ratio 跳变写事件 / 抓取失败返回旧缓存 / 交易日历收盘判定
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

import paper_trading_v2.market_cache as mc

RAW = [
    {'date': '2026-09-01', 'open': 10.0, 'high': 10.5, 'low': 9.8, 'close': 10.2, 'volume': 100.0},
    {'date': '2026-09-02', 'open': 10.2, 'high': 10.6, 'low': 10.0, 'close': 10.4, 'volume': 110.0},
    {'date': '2026-09-03', 'open': 10.4, 'high': 10.8, 'low': 10.2, 'close': 10.6, 'volume': 120.0},
    {'date': '2026-09-04', 'open': 10.6, 'high': 11.0, 'low': 10.4, 'close': 10.8, 'volume': 130.0},
]


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    """tmp 工作区 + 日历钉到 2026-09-04（15:30 已收盘）+ 网络调用计数器"""
    ws = tmp_path / 'ws'
    ws.mkdir()
    monkeypatch.setenv('STOCK_ANALYSIS_WORKSPACE', str(ws))
    db = str(ws / 'market.db')
    counter = {'raw': 0, 'qfq': 0, 'range': 0}

    def fake_raw(code, start='', end='', count=15):
        counter['raw'] += 1
        bars = [dict(b) for b in RAW]
        if start:
            counter['range'] += 1
            bars = [b for b in bars if b['date'] >= start and (not end or b['date'] <= end)]
        return bars

    def fake_qfq(code, count=250):
        counter['qfq'] += 1
        return [{'date': b['date'], 'close': b['close']} for b in RAW]

    monkeypatch.setattr(mc, '_fetch_raw_bars', fake_raw)
    monkeypatch.setattr(mc, '_fetch_qfq_bars', fake_qfq)
    return {'db': db, 'counter': counter, 'ws': ws}


def _rows(db, sql, args=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


class TestFetchKlineCached:
    def test_first_fetch_full_refresh_lands_db(self, cache_env):
        """首次抓取：全量重建落库（含 last_full_refresh_at）"""
        db = cache_env['db']
        bars = mc.fetch_kline_cached('sh688041', db_path=db)
        assert len(bars) == 4
        assert bars[0]['date'] == '2026-09-01' and bars[-1]['date'] == '2026-09-04'  # 升序
        rows = _rows(db, "SELECT date, close FROM kline_daily WHERE code='sh688041' ORDER BY date")
        assert [r[0] for r in rows] == [b['date'] for b in RAW]
        assert rows[-1][1] == 10.8
        meta = dict(_rows(db, "SELECT key, value FROM meta WHERE code='sh688041'"))
        assert 'last_full_refresh_at' in meta and meta['last_full_refresh_bars'] == '4'

    def test_second_call_pure_cache_zero_network(self, cache_env):
        """二次调用：库内最新 >= 最近已收盘交易日 → 纯读库，网络 0 调用"""
        db, counter = cache_env['db'], cache_env['counter']
        mc.fetch_kline_cached('sh688041', db_path=db)
        n = dict(counter)
        bars2 = mc.fetch_kline_cached('sh688041', db_path=db)
        assert dict(counter) == n, f"二次调用不应触网: {counter}"
        assert len(bars2) == 4 and bars2[-1]['date'] == '2026-09-04'

    def test_gap_fill_only_missing_bars(self, cache_env):
        """缺口补抓：只补缺的 bar（date-range），不整段重拉"""
        db, counter = cache_env['db'], cache_env['counter']
        mc.fetch_kline_cached('sh688041', db_path=db)
        # 模拟数据落后两天：删掉 09-03/09-04 两根
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM kline_daily WHERE code='sh688041' AND date >= '2026-09-03'")
        conn.execute("UPDATE meta SET value='2026-09-04T15:30:00' WHERE key='last_full_refresh_at'")
        conn.commit()
        conn.close()
        counter['raw'] = 0
        bars = mc.fetch_kline_cached('sh688041', db_path=db)
        assert len(bars) == 4 and bars[-1]['date'] == '2026-09-04'
        # range 调用恰好 1 次，count=15 全量重拉（无 start）0 次
        assert counter['raw'] == 1 and counter['range'] == 1

    def test_ttl_expiry_full_rebuild(self, cache_env):
        """TTL 超 7 天：DELETE 该 code 全量重拉 count 250 重建"""
        db, counter = cache_env['db'], cache_env['counter']
        mc.fetch_kline_cached('sh688041', db_path=db)
        # 造脏数据 + 过期时间戳
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO kline_daily (code,date,open,high,low,close,volume) "
                     "VALUES ('sh688041','1999-01-01',1,1,1,1,1)")
        conn.execute("UPDATE meta SET value='2026-08-01T00:00:00' WHERE key='last_full_refresh_at'")
        conn.commit()
        conn.close()
        counter['raw'] = 0
        bars = mc.fetch_kline_cached('sh688041', db_path=db)
        assert counter['raw'] == 1  # 全量重拉发生
        dirty = _rows(db, "SELECT COUNT(*) FROM kline_daily WHERE code='sh688041' AND date='1999-01-01'")
        assert dirty[0][0] == 0, "TTL 重建应 DELETE 脏 bar（自愈）"
        assert [b['date'] for b in bars] == [b['date'] for b in RAW]

    def test_ttl_lock_in_second_check_skips_refetch(self, cache_env, monkeypatch):
        """并发防风暴：进 _full_refresh 前别人刚刷过（TTL 内）→ 锁内二次检查跳过重拉"""
        db, counter = cache_env['db'], cache_env['counter']
        mc.fetch_kline_cached('sh688041', db_path=db)  # 建立 TTL 内时间戳
        counter['raw'] = 0
        # 直接调 _full_refresh 模拟"另一进程刚刷完、本进程迟到"
        bars = mc._full_refresh('sh688041', 15, db)
        assert counter['raw'] == 0, "锁内二次检查应跳过重拉"
        assert len(bars) == 4

    def test_exright_ratio_jump_writes_event(self, cache_env):
        """除权检测（兜底信号）：ratio=qfq/raw 日间跳变超阈值 → 写 exright_events，
        factor=跳变系数 ratio[cur]/ratio[prev]（跨 re-anchor 不变量）"""
        db = cache_env['db']
        # 09-04 除权：raw 10.8 → qfq 5.4（10送10 量级）
        def fake_qfq(code, count=250):
            q = [{'date': b['date'], 'close': b['close']} for b in RAW]
            q[-1]['close'] = 5.4  # ratio: 1.0×3 → 0.5
            return q
        import paper_trading_v2.market_cache as m
        orig = m._fetch_qfq_bars
        m._fetch_qfq_bars = fake_qfq
        try:
            bars = mc.fetch_kline_cached('sh688041', db_path=db)
        finally:
            m._fetch_qfq_bars = orig
        assert len(bars) == 4
        ev = mc.read_exright_events('sh688041', db_path=db)
        assert len(ev) == 1
        assert ev[0]['date'] == '2026-09-04'
        assert abs(ev[0]['factor'] - 0.5) < 1e-6
        assert 'ratio 1.000000 -> 0.500000' in ev[0]['note']
        assert 'jump 0.500000' in ev[0]['note']

    def test_exright_fhcontent_marker_small_dividend(self, cache_env):
        """除权检测（权威信号）：小额分红跳变 < 阈值但 qfq bar 带 FHcontent → 记事件"""
        db = cache_env['db']

        def fake_qfq(code, count=250):
            q = [{'date': b['date'], 'close': b['close']} for b in RAW]
            # 09-04 派息：ratio 1.0 -> 0.9992（跳变 8e-4 < 5e-3 阈值，纯跳变检测漏）
            q[-1]['close'] = RAW[-1]['close'] * 0.9992
            q[-1]['exright'] = {'cqr': '2026-09-04', 'FHcontent': '10派0.85元', 'nd': '2025'}
            return q
        import paper_trading_v2.market_cache as m
        orig = m._fetch_qfq_bars
        m._fetch_qfq_bars = fake_qfq
        try:
            mc.fetch_kline_cached('sh688041', db_path=db)
        finally:
            m._fetch_qfq_bars = orig
        ev = mc.read_exright_events('sh688041', db_path=db)
        assert len(ev) == 1
        assert ev[0]['date'] == '2026-09-04'
        assert 'FHcontent=10派0.85元' in ev[0]['note']
        assert abs(ev[0]['factor'] - 0.9992) < 1e-6

    def test_fetch_fail_returns_stale_cache(self, cache_env, monkeypatch):
        """抓取失败：返回现有缓存（陈旧但可用）+ 记失败时间，不崩"""
        db, counter = cache_env['db'], cache_env['counter']
        mc.fetch_kline_cached('sh688041', db_path=db)
        # 腾讯挂了 + 库落后一天
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM kline_daily WHERE code='sh688041' AND date >= '2026-09-04'")
        conn.execute("UPDATE meta SET value='2026-09-04T15:30:00' WHERE key='last_full_refresh_at'")
        conn.commit()
        conn.close()

        def boom(*a, **k):
            raise RuntimeError('network down')

        monkeypatch.setattr(mc, '_fetch_raw_bars', boom)
        bars = mc.fetch_kline_cached('sh688041', db_path=db)
        assert len(bars) == 3 and bars[-1]['date'] == '2026-09-03', "失败应返回陈旧缓存"
        meta = dict(_rows(db, "SELECT key, value FROM meta WHERE code='sh688041'"))
        assert 'last_fetch_fail_at' in meta

    def test_empty_response_marks_fail_keeps_cache(self, cache_env, monkeypatch):
        """空响应（如非法 code）：不写库不删旧数据，记失败"""
        db = cache_env['db']
        mc.fetch_kline_cached('sh688041', db_path=db)
        monkeypatch.setattr(mc, '_fetch_raw_bars', lambda *a, **k: [])
        bars = mc.fetch_kline_cached('sz002536', db_path=db)  # 另一 code 首抓即空
        assert bars == []
        # 旧 code 数据完好
        assert len(mc.fetch_kline_cached('sh688041', db_path=db)) == 4


class TestCalendar:
    def test_last_closed_after_market(self):
        """15:00 后：当日（交易日）即最近已收盘交易日"""
        d = mc.last_closed_trading_day(datetime(2026, 9, 4, 15, 30))
        assert d == '2026-09-04'

    def test_last_closed_before_market(self):
        """15:00 前：上一交易日"""
        d = mc.last_closed_trading_day(datetime(2026, 9, 4, 10, 0))
        assert d == '2026-09-03'

    def test_weekend(self):
        """周六：周五"""
        d = mc.last_closed_trading_day(datetime(2026, 9, 5, 12, 0))
        assert d == '2026-09-04'

    def test_calendar_source_is_real_file(self):
        """日历真源可读、2026 全年覆盖（242 天）"""
        days = mc.load_trading_days()
        assert len(days) == 242
        assert days[0] == '2026-01-05' and days[-1] == '2026-12-31'


class TestExrightDetect:
    def test_no_jump_no_event(self):
        raw = [{'date': '2026-09-01', 'close': 10.0}, {'date': '2026-09-02', 'close': 10.1}]
        qfq = [{'date': '2026-09-01', 'close': 10.0}, {'date': '2026-09-02', 'close': 10.1}]
        assert mc.detect_exright_jumps(raw, qfq) == []

    def test_jump_detected_with_factor(self):
        raw = [{'date': '2026-09-01', 'close': 10.0},
               {'date': '2026-09-02', 'close': 10.8}]
        qfq = [{'date': '2026-09-01', 'close': 10.0},
               {'date': '2026-09-02', 'close': 5.4}]
        ev = mc.detect_exright_jumps(raw, qfq)
        assert len(ev) == 1 and ev[0]['date'] == '2026-09-02' and abs(ev[0]['factor'] - 0.5) < 1e-6


class TestNonDayKline:
    def test_week_passthrough_no_cache(self, cache_env, monkeypatch):
        """非 day 类型：透传直抓，不落缓存库"""
        from paper_trading_v2.kline_fetcher import KLineDataFetcher
        called = {}
        monkeypatch.setattr(KLineDataFetcher, 'fetch_kline_data',
                            lambda self, code, kt, cnt, adjust='qfq': called.setdefault('kt', kt) or [{'date': 'x'}])
        bars = mc.fetch_kline_cached('sh688041', kline_type='week', db_path=cache_env['db'])
        assert called['kt'] == 'week'
        # 周 K 不落库：表不存在（从未建库）= 0 行
        if os.path.exists(cache_env['db']):
            assert _rows(cache_env['db'], "SELECT COUNT(*) FROM kline_daily") == [(0,)]


class TestCliCached:
    def test_fetch_kline_cached_cli_pretty(self, cache_env, monkeypatch):
        """fetch-kline-cached CLI：pretty 输出与 fetch-kline 同构（'收: X' 可解析）"""
        from typer.testing import CliRunner
        from paper_trading_v2.cli import app
        runner = CliRunner()
        r = runner.invoke(app, ["fetch-kline-cached", "sh688041", "--count", "4"])
        assert r.exit_code == 0, r.output
        assert "raw 不复权" in r.output
        assert "收: 10.80" in r.output, r.output
        assert "📅 2026-09-04:" in r.output

    def test_fetch_kline_cached_cli_json(self, cache_env, monkeypatch):
        from typer.testing import CliRunner
        from paper_trading_v2.cli import app
        runner = CliRunner()
        r = runner.invoke(app, ["fetch-kline-cached", "sh688041", "--count", "4",
                                "--format", "json"])
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data['code'] == 'sh688041' and data['adjust'] == 'raw'
        assert data['cached'] is True and 'exright_events' in data
        assert len(data['data']) == 4


if __name__ == '__main__':
    raise SystemExit("请用 pytest 运行: .venv/bin/python -m pytest tests_v2/test_market_cache.py -q")
