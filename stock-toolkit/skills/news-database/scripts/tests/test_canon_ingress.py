"""入库代码保护（canon ingress）：storage 硬门 + cli ptrade2 自动修复 + 存量清洗。

event_stock.stock_code 唯一合法形态 = ptrade2 canon-code 的 canonical 输出：
A股 sh600176/sz002493、港股 hk00700、美股 gb_aapl（2026-09-07 起）。
"""

import shutil
import subprocess
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from news_database import cli, storage
from news_database.cli import app
from news_database.db import connect, init_db


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def _mk_event(conn, title="测试事件"):
    return storage.create_event(conn, title, entity_type="stock")


# ---------- storage.link_event_stock 硬门 ----------


def test_link_event_stock_canonical_passes(db_path):
    """合法 canonical 代码直接入库（A股/港股/美股三市场）。"""
    conn = _conn(db_path)
    eid = _mk_event(conn)
    for code in ("sh600176", "sz002493", "hk00700", "gb_aapl"):
        storage.link_event_stock(conn, eid, code, relevance=50)
    got = {r["stock_code"] for r in storage.event_stocks(conn, eid)}
    assert got == {"sh600176", "sz002493", "hk00700", "gb_aapl"}
    conn.close()


def test_link_event_stock_bare_6digit_raises(db_path):
    """裸 6 位（混存主力形态）必须被硬门拦截。"""
    conn = _conn(db_path)
    eid = _mk_event(conn)
    with pytest.raises(ValueError, match="股票代码格式非法"):
        storage.link_event_stock(conn, eid, "600176")
    assert storage.event_stocks(conn, eid) == []
    conn.close()


def test_link_event_stock_dot_suffix_raises(db_path):
    """点后缀 601127.SH → 拦截并提示归一。"""
    conn = _conn(db_path)
    eid = _mk_event(conn)
    with pytest.raises(ValueError, match="股票代码格式非法"):
        storage.link_event_stock(conn, eid, "601127.SH")
    conn.close()


def test_link_event_stock_uppercase_prefix_raises(db_path):
    """大写 SH/SZ 前缀 → 拦截。"""
    conn = _conn(db_path)
    eid = _mk_event(conn)
    for bad in ("SH603019", "SZ300811"):
        with pytest.raises(ValueError, match="股票代码格式非法"):
            storage.link_event_stock(conn, eid, bad)
    conn.close()


def test_link_event_stock_chinese_name_raises(db_path):
    """中文名当码 → 拦截（agent 应先走 cli 自动修复路径）。"""
    conn = _conn(db_path)
    eid = _mk_event(conn)
    with pytest.raises(ValueError, match="股票代码格式非法"):
        storage.link_event_stock(conn, eid, "亚康股份")
    conn.close()


def test_link_event_stock_error_message_mentions_canon_hint(db_path):
    """报错消息含归一提示（agent 看到就知道怎么修）。"""
    conn = _conn(db_path)
    eid = _mk_event(conn)
    with pytest.raises(ValueError, match="ptrade2 canon-code"):
        storage.link_event_stock(conn, eid, "600176")
    with pytest.raises(ValueError, match="sh600176"):
        storage.link_event_stock(conn, eid, "600176")
    conn.close()


def test_link_event_stock_relevance_upsert_semantics_kept(db_path):
    """硬门不改变 UPSERT 语义：同码重复 link 覆盖 relevance，不新增行。"""
    conn = _conn(db_path)
    eid = _mk_event(conn)
    storage.link_event_stock(conn, eid, "sh600176", relevance=30)
    storage.link_event_stock(conn, eid, "sh600176", relevance=90)
    rows = storage.event_stocks(conn, eid)
    assert len(rows) == 1
    assert rows[0]["relevance"] == 90
    conn.close()


def test_link_event_stock_missing_event_still_raises_first(db_path):
    """事件不存在仍先报事件错（原有校验顺序：事件检查在格式检查之前）。"""
    conn = _conn(db_path)
    with pytest.raises(ValueError, match="事件 99999 不存在"):
        storage.link_event_stock(conn, 99999, "sh600176")
    with pytest.raises(ValueError, match="事件 99999 不存在"):
        storage.link_event_stock(conn, 99999, "600176")   # 事件错优先于格式错
    conn.close()


# ---------- cli --stock canon 自动修复路径 ----------

runner = CliRunner()

PTRADE2_BIN = Path.home() / ".local" / "bin" / "ptrade2"


@pytest.fixture
def fake_canon(monkeypatch):
    """mock ptrade2 归一（测试本地可跑，不依赖真实 ptrade2 子进程）。"""
    mapping = {
        "sh600176": "sh600176",
        "600176": "sh600176",
        "600176.SH": "sh600176",
        "sz002493": "sz002493",
        "sz301085": "sz301085",
        "亚康股份": "sz301085",
    }

    def _fake(raw):
        if raw in mapping:
            return mapping[raw]
        raise ValueError(f"无法解析股票代码 {raw!r}——请用正确代码或中文全称重填（如 亚康股份 或 sz301085）")

    monkeypatch.setattr(cli, "_canon_code", _fake)
    return _fake


def _init(db, monkeypatch):
    monkeypatch.setenv("STOCK_NEWS_DB", str(db))
    runner.invoke(app, ["init"])


def _counts(db):
    conn = connect(db)
    got = tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("events", "messages", "event_stock"))
    conn.close()
    return got


def test_save_stock_canonical_direct(tmp_path, monkeypatch, fake_canon):
    """--stock 传 canonical 直通入库。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)
    r = runner.invoke(app, ["save", "--new-event", "--title", "光模块涨价",
                                   "--stock", "sh600176"])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    assert [x["stock_code"] for x in storage.event_stocks(conn, 1)] == ["sh600176"]
    conn.close()


def test_save_stock_dot_suffix_autofixed(tmp_path, monkeypatch, fake_canon):
    """--stock 传 600176.SH → 自动归一为 sh600176 入库。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)
    r = runner.invoke(app, ["save", "--new-event", "--title", "光模块涨价",
                                   "--stock", "600176.SH"])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    assert [x["stock_code"] for x in storage.event_stocks(conn, 1)] == ["sh600176"]
    conn.close()


def test_save_stock_chinese_name_resolved(tmp_path, monkeypatch, fake_canon):
    """--stock 传中文名 → canon-code 解析出 canonical 入库。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)
    r = runner.invoke(app, ["save", "--new-event", "--title", "算力服务",
                                   "--stock", "亚康股份"])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    assert [x["stock_code"] for x in storage.event_stocks(conn, 1)] == ["sz301085"]
    conn.close()


def test_save_stock_multi_codes_all_canonical(tmp_path, monkeypatch, fake_canon):
    """--stock 多码逗号分隔 → 全部归一为 canonical 入库。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)
    r = runner.invoke(app, ["save", "--new-event", "--title", "光模块涨价",
                                   "--stock", "600176.SH,sz002493"])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    got = {x["stock_code"] for x in storage.event_stocks(conn, 1)}
    conn.close()
    assert got == {"sh600176", "sz002493"}


def test_save_stock_unparseable_aborts_zero_writes(tmp_path, monkeypatch, fake_canon):
    """乱码 → 整单 exit 1，零写入（事件/消息/关联都不落库）。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)
    r = runner.invoke(app, ["save", "--new-event", "--title", "光模块涨价",
                                   "--stock", "abc"])
    assert r.exit_code == 1, r.output
    assert "❌" in r.output
    assert "重填" in r.output
    assert "本次未保存任何关联" in r.output
    assert _counts(db) == (0, 0, 0)


def test_save_stock_unparseable_existing_event_zero_writes(tmp_path, monkeypatch, fake_canon):
    """归属已有事件 + 乱码 → 整单中止，事件/消息/关联零新增。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)
    conn = _conn(db)
    storage.create_event(conn, "已有事件")
    conn.close()
    assert _counts(db) == (1, 0, 0)
    r = runner.invoke(app, ["save", "--title", "新消息", "--event", "1",
                                   "--stock", "00013"])
    assert r.exit_code == 1, r.output
    assert "本次未保存任何关联" in r.output
    assert _counts(db) == (1, 0, 0)


def test_save_stock_ptrade2_unavailable_fail_closed(tmp_path, monkeypatch):
    """ptrade2 不可用 → fail-closed 整单 exit 1，零写入。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)

    def _unavailable(raw):
        raise RuntimeError(f"ptrade2 不可用，无法校验代码格式: {raw!r}（FileNotFoundError）")

    monkeypatch.setattr(cli, "_canon_code", _unavailable)
    r = runner.invoke(app, ["save", "--new-event", "--title", "光模块涨价",
                                   "--stock", "600176"])
    assert r.exit_code == 1, r.output
    assert "ptrade2 不可用" in r.output
    assert _counts(db) == (0, 0, 0)


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_canon_code_helper_fail_closed_when_binary_missing(monkeypatch):
    """ptrade2 二进制缺失 → RuntimeError（fail-closed）。"""
    def _boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(cli.subprocess, "run", _boom)
    with pytest.raises(RuntimeError, match="ptrade2 不可用，无法校验代码格式"):
        cli._canon_code("600176")


def test_canon_code_helper_fail_closed_on_timeout(monkeypatch):
    """ptrade2 超时 → RuntimeError（fail-closed）。"""
    def _hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ptrade2", timeout=15)
    monkeypatch.setattr(cli.subprocess, "run", _hang)
    with pytest.raises(RuntimeError, match="ptrade2 不可用，无法校验代码格式"):
        cli._canon_code("600176")


def test_canon_code_helper_rejects_noncanonical_result(monkeypatch):
    """ptrade2 返回非 canonical 形态 → 守门拒绝（fail-closed）。"""
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: _Proc('{"code": "BJ832566"}', 0))
    with pytest.raises(ValueError, match="无法解析股票代码"):
        cli._canon_code("832566")


@pytest.mark.skipif(not PTRADE2_BIN.exists(), reason="ptrade2 未安装（~/.local/bin）")
def test_save_stock_real_ptrade2_integration(tmp_path, monkeypatch):
    """集成：真实 ptrade2 子进程归一 600176.SH → sh600176。"""
    db = tmp_path / "t.db"
    _init(db, monkeypatch)
    r = runner.invoke(app, ["save", "--new-event", "--title", "中国巨石中标",
                                   "--stock", "600176.SH"])
    assert r.exit_code == 0, r.output
    conn = connect(db)
    assert [x["stock_code"] for x in storage.event_stocks(conn, 1)] == ["sh600176"]
    conn.close()


# ---------- canon_migrate 存量清洗（/tmp 副本，不碰生产 news.db） ----------

from news_database import canon_migrate  # noqa: E402


def _mk_dirty_db(db_path):
    """构造三态脏库副本：canonical + 旧码（autofix）+ 需人工（human）。直插绕过硬门（模拟存量）。"""
    conn = _conn(db_path)
    eid1 = storage.create_event(conn, "光模块涨价")
    eid2 = storage.create_event(conn, "港股异动")
    for eid, code, rel in ((eid1, "sh600176", 70), (eid1, "600176.SH", 40),
                           (eid1, "SH603019", 30), (eid1, "600176", 30),
                           (eid2, "INTC", 50), (eid2, "00013", 20)):
        conn.execute("INSERT INTO event_stock (event_id, stock_code, relevance) VALUES (?, ?, ?)",
                     (eid, code, rel))
    for code, name in (("sh600176", "中国巨石"), ("600176.SH", "中国巨石"), ("600176", "中国巨石"),
                       ("sz002493", "中际旭创"), ("INTC", "英特尔")):
        conn.execute("INSERT INTO stocks (code, name) VALUES (?, ?)", (code, name))
    conn.commit()
    conn.close()
    return eid1, eid2


def test_migrate_dry_run_classifies_and_leaves_db_untouched(tmp_path, monkeypatch, fake_canon, capsys):
    """dry-run：逐行分类输出报告，库零改动。"""
    db = tmp_path / "dirty.db"
    _mk_dirty_db(db)
    before = _counts(db)
    rc = canon_migrate.main(["--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert _counts(db) == before
    assert "600176.SH" in out          # autofix 行出现在报告
    assert "需人工" in out
    assert "INTC" in out               # human 行出现在报告（fake 拒 INTC → 需人工）


def test_migrate_apply_normalizes_merges_and_backs_up(tmp_path, monkeypatch, fake_canon):
    """--apply：UPDATE 归一 + 同 event 冲突归并（relevance 取 max）+ 需人工不动 + 自动备份。"""
    db = tmp_path / "dirty.db"
    eid1, eid2 = _mk_dirty_db(db)
    rc = canon_migrate.main(["--db", str(db), "--apply"])
    assert rc == 0
    conn = connect(db)
    rows = {(r["event_id"], r["stock_code"]): r["relevance"]
            for r in conn.execute("SELECT event_id, stock_code, relevance FROM event_stock")}
    stocks = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM stocks")}
    conn.close()
    # 600176/600176.SH → sh600176 撞已有 canonical 行 → 归并，relevance 取 max
    assert rows[(eid1, "sh600176")] == 70
    assert rows[(eid1, "sh603019")] == 30
    assert len(rows) == 4
    # 需人工裁决行不处理（fake 解析不了的 INTC/00013 留原样）
    assert rows[(eid2, "INTC")] == 50
    assert rows[(eid2, "00013")] == 20
    # stocks 以 name 去重：中国巨石只留 canonical sh600176，human 行不动
    assert stocks == {"sh600176": "中国巨石", "sz002493": "中际旭创", "INTC": "英特尔"}
    assert list(tmp_path.glob("dirty.db.bak-canon-*")) != []


def test_migrate_apply_idempotent(tmp_path, monkeypatch, fake_canon):
    """幂等：二次 --apply 空转。"""
    db = tmp_path / "dirty.db"
    _mk_dirty_db(db)
    canon_migrate.main(["--db", str(db), "--apply"])
    after = _counts(db)
    assert canon_migrate.main(["--db", str(db), "--apply"]) == 0
    assert _counts(db) == after


def test_migrate_report_file_marks_human_rows(tmp_path, monkeypatch, fake_canon):
    """--report 输出文件列出每行分类与需人工裁决行。"""
    db = tmp_path / "dirty.db"
    _mk_dirty_db(db)
    report = tmp_path / "report.txt"
    assert canon_migrate.main(["--db", str(db), "--report", str(report)]) == 0
    text = report.read_text()
    assert "需人工" in text
    assert "INTC" in text and "00013" in text
    assert "600176.SH → sh600176" in text


def test_migrate_missing_db_fails_closed(tmp_path):
    """库文件不存在 → 报错 exit 1。"""
    assert canon_migrate.main(["--db", str(tmp_path / "nope.db")]) == 1


def test_migrate_classify_shapes(fake_canon):
    """_classify 各形态分类：机械归一确定性；其余交 ptrade2（可解析 autofix / 拒绝 human）。"""
    # 机械归一（确定性，不调 ptrade2，与 canonical 一致）
    assert canon_migrate._classify("600176.SH") == ("autofix", "sh600176")
    assert canon_migrate._classify("SH603019") == ("autofix", "sh603019")
    assert canon_migrate._classify("00700.HK") == ("autofix", "hk00700")
    assert canon_migrate._classify("02513.HK") == ("autofix", "hk02513")
    # 已 canonical
    assert canon_migrate._classify("hk00700") == ("canonical", "hk00700")
    assert canon_migrate._classify("gb_aapl") == ("canonical", "gb_aapl")
    # 交 ptrade2 裁决：可解析 → autofix（中文名/裸码统一走规则源）
    assert canon_migrate._classify("亚康股份") == ("autofix", "sz301085")
    # 拒绝 → 留人工（4 位 .HK 补零歧义 / 字母 ticker / 裸码错位）
    assert canon_migrate._classify("0700.HK") == ("human", None)   # 非 5 位 .HK，fake 拒
    assert canon_migrate._classify("INTC") == ("human", None)      # fake 拒（真实库需 gb_intc 人工裁决）
    assert canon_migrate._classify("02269") == ("human", None)     # fake 拒（真实 ptrade2 可解 hk02269）
