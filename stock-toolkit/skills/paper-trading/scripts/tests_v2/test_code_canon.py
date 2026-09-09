"""code→中文名归一 + canonical 代码（2026-09-07 亚康股份/301085 实体分裂事故修复）

pool.stock 是 PRIMARY KEY、一只票存中文名：watchlist-add 纯代码入参必须反查中文名，
反查不到 fail-closed 拒绝（杜绝 '301085'(L2) 与 '亚康股份'(NEWS) 同 code 双 active 行）。
测试全走 /tmp 副本（ws fixture 钉 STOCK_ANALYSIS_WORKSPACE；NEWS_DB_PATH 钉不存在路径
或 tmp newsdb——零生产库读）。
"""
import sys, os, json, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _make_newsdb(path, rows):
    """造 newsdb 副本（stocks 表），供 newsdb 路径测试"""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT NOT NULL)")
    conn.executemany("INSERT INTO stocks (code, name) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def no_newsdb(tmp_path, monkeypatch):
    """钉 NEWS_DB_PATH 到不存在路径：resolve/lookup 只走 fallback/hot_stocks，零生产 newsdb 读"""
    monkeypatch.setenv("NEWS_DB_PATH", str(tmp_path / "absent-news.db"))


# ============ canonical_stock_code：纯格式归一（不触网） ============

@pytest.mark.parametrize("raw,expect", [
    ("sh600176", "sh600176"),          # 已规范原样
    ("SH601127", "sh601127"),          # 大写前缀小写化
    ("SZ002493", "sz002493"),
    ("600176.SH", "sh600176"),         # 后缀形态 → 前缀形态
    ("600176.sz", "sz600176"),
    ("hk00700", "hk00700"),
    ("HK00700", "hk00700"),
    ("gb_aapl", "gb_aapl"),
    ("GB_AAPL", "gb_aapl"),
    ("600176", "sh600176"),            # 裸 6 位按首位推断前缀
    ("002493", "sz002493"),
    ("301085", "sz301085"),
    ("830799", "bj830799"),            # 北交所 8 开头
    ("00700", "hk00700"),              # 裸 5 位 → 港股
    ("aapl", "gb_aapl"),               # 裸英文 ticker（内置 gb 库内）
    ("TSLA", "gb_tsla"),
    ("brk_a", "gb_brk_a"),
])
def test_canonical_stock_code_forms(raw, expect):
    from paper_trading_v2.code_searcher import canonical_stock_code
    assert canonical_stock_code(raw) == expect


@pytest.mark.parametrize("bad", ["亚康股份", "腾讯控股", "", "1234567", "###",
                                 "abc def", "600176.SS"])
def test_canonical_stock_code_rejects(bad):
    from paper_trading_v2.code_searcher import canonical_stock_code
    with pytest.raises(ValueError):
        canonical_stock_code(bad)


# ============ looks_like_stock_code：代码形态判定 ============

@pytest.mark.parametrize("raw", ["301085", "sz301085", "SZ002493", "600176.SH",
                                 "hk00700", "gb_aapl", "AAPL"])
def test_looks_like_stock_code_true(raw):
    from paper_trading_v2.code_searcher import looks_like_stock_code
    assert looks_like_stock_code(raw) is True


@pytest.mark.parametrize("raw", ["亚康股份", "腾讯控股", "阿里巴巴-SW", "美团-W",
                                 "NEWS票", "", "   "])
def test_looks_like_stock_code_false(raw):
    from paper_trading_v2.code_searcher import looks_like_stock_code
    assert looks_like_stock_code(raw) is False


# ============ resolve_name_for_code：代码 → 中文名（纯本地，不触网） ============

@pytest.mark.parametrize("raw,expect", [
    ("sz301085", "亚康股份"),
    ("301085", "亚康股份"),        # 裸 6 位推断 sz → fallback 反查
    ("sh600176", "中国巨石"),
    ("600176.SH", "中国巨石"),      # 容错形态（canonical 归一后命中）
    ("hk00700", "腾讯控股"),        # hot_stocks 港股反查
    ("gb_aapl", "苹果"),            # hot_stocks 美股反查
])
def test_resolve_name_for_code_local_sources(raw, expect, no_newsdb):
    from paper_trading_v2.code_searcher import resolve_name_for_code
    assert resolve_name_for_code(raw) == expect


def test_resolve_name_for_code_newsdb(tmp_path, monkeypatch):
    """newsdb stocks 表容错匹配（601127.SH / 601127 / sh601127 → 赛力斯）"""
    from paper_trading_v2.code_searcher import resolve_name_for_code
    p = _make_newsdb(tmp_path / "news.db", [("601127.SH", "赛力斯")])
    monkeypatch.setenv("NEWS_DB_PATH", str(p))
    assert resolve_name_for_code("sh601127") == "赛力斯"
    assert resolve_name_for_code("601127") == "赛力斯"
    assert resolve_name_for_code("601127.SH") == "赛力斯"


def test_resolve_name_for_code_newsdb_failure_returns_none(tmp_path, monkeypatch):
    """连接失败/缺表 → None 不抛（resolve 永不崩，fail-open 到调用方拒绝）"""
    from paper_trading_v2.code_searcher import resolve_name_for_code
    # 缺表
    p = tmp_path / "notable.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE other (x TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("NEWS_DB_PATH", str(p))
    assert resolve_name_for_code("sh601127") is None
    # 坏库（非 sqlite 文件）
    bad = tmp_path / "bad.db"
    bad.write_text("not a sqlite database")
    monkeypatch.setenv("NEWS_DB_PATH", str(bad))
    assert resolve_name_for_code("sh601127") is None


@pytest.mark.parametrize("raw", ["sz999999", "亚康股份", "zzz999", "hk00042"])
def test_resolve_name_for_code_miss_returns_none(raw, no_newsdb):
    from paper_trading_v2.code_searcher import resolve_name_for_code
    assert resolve_name_for_code(raw) is None


# ============ lookup_code_for_name：中文名 → code（本地表，不触网） ============

def test_lookup_code_for_name_local_tables(no_newsdb):
    from paper_trading_v2.code_searcher import lookup_code_for_name
    assert lookup_code_for_name("亚康股份") == "sz301085"   # fallback
    assert lookup_code_for_name("腾讯控股") == "hk00700"    # hot_stocks
    assert lookup_code_for_name("不存在股份") is None        # 本地表全 miss


def test_lookup_code_for_name_newsdb(tmp_path, monkeypatch):
    from paper_trading_v2.code_searcher import lookup_code_for_name
    p = _make_newsdb(tmp_path / "news.db", [("601127.SH", "赛力斯")])
    monkeypatch.setenv("NEWS_DB_PATH", str(p))
    assert lookup_code_for_name("赛力斯") == "601127.SH"


# ============ watchlist-add：纯代码入参反查中文名（实体分裂防线） ============

def _pool_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT stock, code FROM pool").fetchall()
    conn.close()
    return rows


def test_watchlist_add_pure_code_resolves_to_cn_name(ws, db_path, monkeypatch):
    """事故回归：watchlist-add 301085 必须存中文名 亚康股份，不得存纯代码行"""
    monkeypatch.setenv("NEWS_DB_PATH", str(ws / "absent-news.db"))
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["watchlist-add", "301085", "--strategy", "L2"])
    assert r.exit_code == 0, r.output
    assert "亚康股份" in r.output
    rows = _pool_rows(db_path)
    assert ("亚康股份", "sz301085") in rows
    assert all(s != "301085" for s, _ in rows)   # 无纯代码行（实体分裂）


def test_watchlist_add_pure_code_with_explicit_code_keeps_code(ws, db_path, monkeypatch):
    """用户显式 --code：名字反查后 code 保留用户值"""
    monkeypatch.setenv("NEWS_DB_PATH", str(ws / "absent-news.db"))
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["watchlist-add", "600176.SH", "--code", "sh600176",
                            "--strategy", "L2"])
    assert r.exit_code == 0, r.output
    assert "中国巨石" in r.output
    assert ("中国巨石", "sh600176") in _pool_rows(db_path)


def test_watchlist_add_hk_code_resolves(ws, db_path, monkeypatch):
    monkeypatch.setenv("NEWS_DB_PATH", str(ws / "absent-news.db"))
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["watchlist-add", "hk00700", "--strategy", "L2"])
    assert r.exit_code == 0, r.output
    assert ("腾讯控股", "hk00700") in _pool_rows(db_path)


def test_watchlist_add_unresolvable_code_rejected_zero_write(ws, db_path, monkeypatch):
    """反查不到 fail-closed：exit 1 且零写入（未触 DB，连 schema 都不建）"""
    monkeypatch.setenv("NEWS_DB_PATH", str(ws / "absent-news.db"))
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["watchlist-add", "sz999999", "--strategy", "L2"])
    assert r.exit_code == 1
    assert "无法解析为股票名称" in r.output
    assert "sz999999" in r.output
    assert "亚康股份" in r.output          # 文案带中文名入池示例
    assert not db_path.exists()            # 零写入


# ============ ptrade2 canon-code CLI ============

def test_canon_code_text_and_json(no_newsdb):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["canon-code", "600176.SH"])
    assert r.exit_code == 0, r.output
    assert "sh600176" in r.output and "中国巨石" in r.output
    r = runner.invoke(app, ["canon-code", "sz301085", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"code": "sz301085", "name": "亚康股份", "market": "A股"}


def test_canon_code_name_path(no_newsdb):
    """中文名 → code（本地表，不触网）"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["canon-code", "亚康股份", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["code"] == "sz301085" and data["name"] == "亚康股份"
    r = runner.invoke(app, ["canon-code", "腾讯控股", "--json"])
    data = json.loads(r.output)
    assert data["code"] == "hk00700" and data["market"] == "港股"


def test_canon_code_gb_ticker(no_newsdb):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["canon-code", "aapl", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output) == {"code": "gb_aapl", "name": "苹果", "market": "美股"}


def test_canon_code_partial_success_name_null(no_newsdb):
    """代码形态但查不到名字 → exit 0，code 仍输出、name=null（部分成功）"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["canon-code", "sz999999", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["code"] == "sz999999" and data["name"] is None


def test_canon_code_code_form_unparseable_exit_1(no_newsdb):
    """代码形态但无法归一 → exit 1 + 明确文案"""
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["canon-code", "1234567"])
    assert r.exit_code == 1
    assert "无法解析" in r.output and "亚康股份" in r.output and "sz301085" in r.output
    r = runner.invoke(app, ["canon-code", "zzz", "--json"])
    assert r.exit_code == 1
    assert "error" in json.loads(r.output)


def test_canon_code_name_path_unparseable_exit_1(no_newsdb, monkeypatch):
    """中文名全 miss（含搜索兜底 miss）→ exit 1；monkeypatch 搜索兜底避免触网"""
    from paper_trading_v2 import code_searcher
    monkeypatch.setattr(code_searcher, "validate_stock_name", lambda name: (False, None))
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["canon-code", "乱七八糟###"])
    assert r.exit_code == 1
    assert "无法解析" in r.output


# ============ verify_code_name：代码↔名称一致性（2026-09-09 北方铜业错码事故） ============

def _offline(monkeypatch):
    """断网：腾讯实时接口返回 None → 校验只走本地三级反查"""
    monkeypatch.setattr(
        'paper_trading_v2.price_fetcher.StockPriceFetcher.get_realtime_price',
        lambda self, code: None)


def test_verify_code_name_match_mismatch_and_failopen(no_newsdb, monkeypatch):
    """一致放行 / 错配拒绝（北方铜业配龙版传媒码）/ 查不到 fail-open"""
    _offline(monkeypatch)
    from paper_trading_v2.code_searcher import verify_code_name
    ok, found = verify_code_name("北方铜业", "sz000737")
    assert ok and found == "北方铜业"
    ok, found = verify_code_name("北方铜业", "sh605577")     # 龙版传媒
    assert not ok and found == "龙版传媒"
    ok, found = verify_code_name("北方铜业", "605577.SH")    # 后缀形态同样归一后判定
    assert not ok and found == "龙版传媒"
    ok, found = verify_code_name("某不存在的票", "sh999999")
    assert ok and found is None                              # fail-open 不阻塞


def test_verify_code_name_norm_tolerates_suffix(no_newsdb, monkeypatch):
    """名称前后缀归一：北方铜业 vs 北方铜业股份/ST北方铜业 不算错配"""
    from paper_trading_v2 import code_searcher as cs
    for variant in ("北方铜业股份", "北方铜业股份有限公司", "ST北方铜业", "北方铜业 "):
        monkeypatch.setattr(cs, "resolve_name_for_code", lambda c, v=variant: v)
        ok, _ = cs.verify_code_name("北方铜业", "sz000737", allow_network=False)
        assert ok, variant


def test_watchlist_add_rejects_mismatched_code(ws, db_path, monkeypatch):
    """CLI 闸门：--code 与名称不符 → exit 1 且零写入；PTRADE2_ALLOW_CODE_MISMATCH=1 放行"""
    monkeypatch.delenv("PTRADE2_ALLOW_CODE_MISMATCH", raising=False)   # 打开闸门（conftest 默认关）
    monkeypatch.setenv("NEWS_DB_PATH", str(ws / "absent-news.db"))
    _offline(monkeypatch)
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["watchlist-add", "北方铜业", "--code", "sh605577", "--strategy", "L2"])
    assert r.exit_code == 1
    assert "代码与股票名不符" in r.output
    assert "sz000737" in r.output          # 提示正确代码
    assert "龙版传媒" in r.output          # 指出该码真实对应谁
    assert not db_path.exists()            # 零写入
    monkeypatch.setenv("PTRADE2_ALLOW_CODE_MISMATCH", "1")
    r2 = runner.invoke(app, ["watchlist-add", "北方铜业", "--code", "sh605577", "--strategy", "L2"])
    assert r2.exit_code == 0, r2.output
    assert ("北方铜业", "sh605577") in _pool_rows(db_path)   # 逃生阀生效
