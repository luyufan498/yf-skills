"""analysis_schedule 表 + 调度函数 + ANALYSIS_REFRESH claim 硬门 + monitor 测试。

覆盖（analysis-ttl 方案）：建表/幂等、upsert/seed_pool、request_refresh 置位、
mark_analyzed 清位、due_stocks 排序（refresh 优先/refreshed_at 旧→新/TTL 到期/
NULL 最前/limit）、TTL 3 交易日边界（trading_calendar 真源：9/4 周五 → 9/7 周一
仅 1 交易日未到期）、ANALYSIS_REFRESH claim 硬门（错 consumer exit 3/正确放行/
存量不校验）、monitor IDLE 字节稳定。

红线：全部用 /tmp（tmp_path）副本库，生产库零接触。
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# task-bus 包（仓库内兄弟 skill，供 ANALYSIS_REFRESH claim 硬门测试 import）
_TASKBUS_DIR = _SCRIPTS_DIR.parent.parent / "task-bus" / "scripts"
if str(_TASKBUS_DIR) not in sys.path:
    sys.path.insert(0, str(_TASKBUS_DIR))

from news_database import analysis_schedule as asched  # noqa: E402
from news_database.db import connect, init_db  # noqa: E402


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


@pytest.fixture
def tasks_db(tmp_path, monkeypatch):
    """独立 tmp 任务库 + 干净 import 的 task_bus.db。"""
    monkeypatch.setenv("STOCK_TASKS_DB", str(tmp_path / "tasks.db"))
    for mod in list(sys.modules):
        if mod == "task_bus" or mod.startswith("task_bus."):
            del sys.modules[mod]
    from task_bus import db as tdb
    yield tdb
    for mod in list(sys.modules):
        if mod == "task_bus" or mod.startswith("task_bus."):
            del sys.modules[mod]


# ---------------------------------------------------------------------------
# 建表/幂等
# ---------------------------------------------------------------------------


def test_ensure_table_idempotent(db_path):
    """两次建表调用不报错，字段齐全，不触碰 task_schedule/scan_log。"""
    conn = _conn(db_path)
    asched.ensure_table(conn)
    asched.ensure_table(conn)  # 幂等：不报错
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(analysis_schedule)")}
    assert set(cols) == {"stock", "last_analyzed_at", "refresh_required",
                         "refresh_reason", "refresh_source", "refreshed_at",
                         "last_result"}
    assert cols["stock"] == "TEXT"
    assert cols["refresh_required"] == "INTEGER"
    nn = {r[1] for r in conn.execute("PRAGMA table_info(analysis_schedule)")
          if r[3]}
    # TEXT PRIMARY KEY 在 SQLite PRAGMA 里 notnull=0（仅 INTEGER PK 隐式非空），
    # 显式 NOT NULL 声明只有 refresh_required
    assert nn == {"refresh_required"}
    ts_cols = {r[1] for r in conn.execute("PRAGMA table_info(task_schedule)")}
    assert ts_cols == {"task_id", "last_run_at", "ttl_hours", "next_due_at",
                       "last_result"}  # 兄弟表未动
    conn.close()


def test_init_db_creates_analysis_schedule(db_path):
    """init_db 集成路径：SCHEMA_SQL 建出 analysis_schedule，二次 init_db 不报错。"""
    conn = _conn(db_path)
    init_db(conn)  # 第二次（幂等）
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='analysis_schedule'"
    ).fetchone()[0]
    assert n == 1
    conn.close()


def test_upsert_stock_keeps_existing_state(db_path):
    """upsert_stock：新行 last_analyzed_at=NULL；已有行历史状态全保留。"""
    conn = _conn(db_path)
    asched.upsert_stock(conn, "贵州茅台")
    row = asched.get_stock(conn, "贵州茅台")
    assert row["last_analyzed_at"] is None
    assert row["refresh_required"] == 0
    # 分析后重新 upsert（池同步重跑）：历史保留
    asched.mark_analyzed(conn, "贵州茅台", result="中性", now="2026-09-04 10:00:00")
    asched.upsert_stock(conn, "贵州茅台")
    row2 = asched.get_stock(conn, "贵州茅台")
    assert row2["last_analyzed_at"] == "2026-09-04 10:00:00"
    assert row2["last_result"] == "中性"
    conn.close()


def test_seed_pool_idempotent(db_path):
    """seed_pool：缺行才插，重复调用不增行不覆盖，返回新入表数。"""
    conn = _conn(db_path)
    n1 = asched.seed_pool(conn, ["贵州茅台", "赛力斯", "光智科技"])
    assert n1 == 3
    n2 = asched.seed_pool(conn, ["贵州茅台", "中际旭创"])
    assert n2 == 1  # 只补中际旭创
    n3 = asched.seed_pool(conn, ["贵州茅台", "赛力斯"])
    assert n3 == 0
    rows = asched.list_all(conn)
    assert {r["stock"] for r in rows} == {"贵州茅台", "赛力斯", "光智科技", "中际旭创"}
    assert all(r["last_analyzed_at"] is None for r in rows)
    conn.close()


# ---------------------------------------------------------------------------
# request_refresh 置位 / mark_analyzed 清位
# ---------------------------------------------------------------------------


def test_request_refresh_sets_flag(db_path):
    """request_refresh：refresh_required=1 + reason/source 落库，幂等重打标。"""
    conn = _conn(db_path)
    asched.request_refresh(conn, "赛力斯", reason="news_signal",
                           source="ND#123", now="2026-09-04 09:00:00")
    row = asched.get_stock(conn, "赛力斯")
    assert row["refresh_required"] == 1
    assert row["refresh_reason"] == "news_signal"
    assert row["refresh_source"] == "ND#123"
    assert row["refreshed_at"] == "2026-09-04 09:00:00"
    assert row["last_analyzed_at"] is None  # 不动分析游标
    # 幂等重打标：更新 reason/refreshed_at，仍置位
    asched.request_refresh(conn, "赛力斯", reason="price_move", source="daymove",
                           now="2026-09-04 11:00:00")
    row2 = asched.get_stock(conn, "赛力斯")
    assert row2["refresh_required"] == 1
    assert row2["refresh_reason"] == "price_move"
    assert row2["refreshed_at"] == "2026-09-04 11:00:00"
    conn.close()


def test_mark_analyzed_clears_refresh(db_path):
    """mark_analyzed：last_analyzed_at=now、refresh_required=0、reason='ttl'、result 落库。"""
    conn = _conn(db_path)
    asched.request_refresh(conn, "光智科技", reason="news_signal", source="ND#9")
    asched.mark_analyzed(conn, "光智科技", result="偏多：磷化铟订单饱满",
                         now="2026-09-04 15:30:00")
    row = asched.get_stock(conn, "光智科技")
    assert row["last_analyzed_at"] == "2026-09-04 15:30:00"
    assert row["refresh_required"] == 0
    assert row["refresh_reason"] == "ttl"
    assert row["last_result"] == "偏多：磷化铟订单饱满"
    # 清位后不再 due（TTL 内）
    due = asched.due_stocks(conn, now="2026-09-04 16:00:00")
    assert "光智科技" not in [r["stock"] for r in due]
    conn.close()


# ---------------------------------------------------------------------------
# due_stocks 排序
# ---------------------------------------------------------------------------


def _seed_mixed(conn):
    """混合态：refresh 两只（打标一旧一新）+ TTL 过期两只 + TTL 内一只 + never 一只。"""
    asched.seed_pool(conn, ["茅台", "赛力斯", "光智", "中际", "新易盛", "工业富联"])
    # refresh 组：茅台打标更早（更优先），赛力斯打标晚
    asched.request_refresh(conn, "茅台", reason="news_signal", source="ND#1",
                           now="2026-09-04 09:00:00")
    asched.request_refresh(conn, "赛力斯", reason="price_move", source="daymove",
                           now="2026-09-04 10:00:00")
    # TTL 组：光智 8/28 分析（距 9/4 已 4 个交易日 → 过期）、
    # 中际 8/31 分析（距 9/4 已 3 个交易日 → 过期，比光智新排后）
    asched.mark_analyzed(conn, "光智", result="旧一轮", now="2026-08-28 10:00:00")
    asched.mark_analyzed(conn, "中际", result="较旧一轮", now="2026-08-31 10:00:00")
    # TTL 内：新易盛今天分析
    asched.mark_analyzed(conn, "新易盛", result="新鲜", now="2026-09-04 09:30:00")
    # 工业富联：never（NULL 最前于 TTL 组）


def test_due_stocks_refresh_first_then_null_then_oldest(db_path):
    """排序：refresh 组在前（打标旧→新），组二 NULL(never) 最前 → 最久 → TTL 内不入队。"""
    conn = _conn(db_path)
    _seed_mixed(conn)
    due = asched.due_stocks(conn, now="2026-09-04 15:00:00")
    stocks = [r["stock"] for r in due]
    # refresh 两只（茅台打标早在前）+ never（工业富联）+ TTL 过期（光智 8/28 比
    # 中际 8/31 旧在前）；新易盛 9/4 分析未过期不入队
    assert stocks == ["茅台", "赛力斯", "工业富联", "光智", "中际"]
    assert due[0]["refresh_required"] == 1
    assert due[0]["refresh_reason"] == "news_signal"
    assert due[2]["last_analyzed_at"] is None  # never 行
    conn.close()


def test_due_stocks_limit(db_path):
    """limit=2：只出前 2（refresh 组），剩余下轮。"""
    conn = _conn(db_path)
    _seed_mixed(conn)
    due = asched.due_stocks(conn, now="2026-09-04 15:00:00", limit=2)
    assert [r["stock"] for r in due] == ["茅台", "赛力斯"]
    conn.close()


def test_due_stocks_empty_and_unseeded(db_path):
    """空表（未 seed）→ []；缺行库 ensure 后安全空跑。"""
    conn = _conn(db_path)
    asched.ensure_table(conn)
    assert asched.due_stocks(conn, now="2026-09-04 15:00:00") == []
    conn.close()


# ---------------------------------------------------------------------------
# TTL 3 交易日边界（trading_calendar 真源：data/trading_calendar.json 2026）
# ---------------------------------------------------------------------------


def test_ttl_boundary_three_trading_days_fri_to_mon(db_path):
    """9/4 周五 → 9/7 周一只 1 交易日：周五分析周一仍新鲜（核心边界）。

    边界=now 往回数 3 个交易日：9/7 → 9/4 → 9/3 → 9/2（边界日 2026-09-02），
    9/4 分析（> 边界）未过期。
    """
    conn = _conn(db_path)
    asched.seed_pool(conn, ["茅台", "赛力斯"])
    asched.mark_analyzed(conn, "茅台", result="周五分析", now="2026-09-04 15:00:00")
    asched.mark_analyzed(conn, "赛力斯", result="周一分析", now="2026-09-07 10:00:00")
    due = asched.due_stocks(conn, now="2026-09-07 15:00:00")
    stocks = [r["stock"] for r in due]
    assert "茅台" not in stocks  # 9/4→9/7 仅 1 交易日，未过期
    assert "赛力斯" not in stocks
    # 周二（9/8，第 2 交易日）仍未过期
    due2 = asched.due_stocks(conn, now="2026-09-08 15:00:00")
    assert stocks_consistent(due2, {"茅台", "赛力斯"}, absent=True)
    # 周三（9/9，第 3 交易日）：两只仍未过期
    due3 = asched.due_stocks(conn, now="2026-09-09 15:00:00")
    assert stocks_consistent(due3, {"茅台", "赛力斯"}, absent=True)
    # 周四（9/10，茅台第 4 交易日）→ 茅台过期；赛力斯（9/7 分析）第 3 交易日仍新鲜
    due4 = asched.due_stocks(conn, now="2026-09-10 15:00:00")
    assert "茅台" in [r["stock"] for r in due4]
    assert "赛力斯" not in [r["stock"] for r in due4]
    conn.close()


def stocks_consistent(rows, names, *, absent):
    stocks = {r["stock"] for r in rows}
    return names.isdisjoint(stocks) if absent else names <= stocks


def test_ttl_boundary_expiry_after_three_trading_days(db_path):
    """8/28（周五）分析 → 8/31,9/1,9/2 满 3 交易日 → 9/3（第 4 交易日）到期，9/2 未到期。"""
    conn = _conn(db_path)
    asched.seed_pool(conn, ["光智"])
    asched.mark_analyzed(conn, "光智", result="旧", now="2026-08-28 10:00:00")
    due_before = asched.due_stocks(conn, now="2026-09-02 20:00:00")
    assert "光智" not in [r["stock"] for r in due_before]  # 第 3 交易日 9/2 走完仍新鲜
    due_at = asched.due_stocks(conn, now="2026-09-03 20:00:00")
    assert "光智" in [r["stock"] for r in due_at]  # 第 4 交易日 → 过期
    conn.close()


def test_ttl_boundary_skips_holiday_week(db_path):
    """长假边界：9/30 分析，国庆 10/1-10/7 休市 → 节后首日 10/8 起才开算。

    回数 3 交易日：10/8 时=10/8,9/30,9/29（边界 9/30=分析当天，未过期）；
    10/9/10/12 边界仍在 9/30（当天不<当天）→ 均未过期；10/13 边界=10/8
    → 9/30 < 10/8 过期。长假整体把 TTL 顺延一个节后交易日。
    """
    conn = _conn(db_path)
    asched.seed_pool(conn, ["中际"])
    asched.mark_analyzed(conn, "中际", result="节前", now="2026-09-30 15:00:00")
    for day in ("2026-10-08", "2026-10-09", "2026-10-12"):
        due = asched.due_stocks(conn, now=f"{day} 20:00:00")
        assert "中际" not in [r["stock"] for r in due], day  # 节后 3 个交易日内仍新鲜
    due4 = asched.due_stocks(conn, now="2026-10-13 20:00:00")
    assert "中际" in [r["stock"] for r in due4]  # 第 4 个交易日 → 过期
    conn.close()


def test_ttl_boundary_format_safe_against_timestamp(db_path):
    """字符串边界安全：last_analyzed_at '2026-09-02 10:00:00' 不 < '2026-09-02'。"""
    conn = _conn(db_path)
    asched.seed_pool(conn, ["新易盛"])
    asched.mark_analyzed(conn, "新易盛", result="边界当天", now="2026-09-02 10:00:00")
    due = asched.due_stocks(conn, now="2026-09-07 15:00:00")
    # 9/7 往回 3 交易日=边界日 9/2：边界当天分析（时间戳 > 纯日期）未过期
    assert "新易盛" not in [r["stock"] for r in due]
    conn.close()


# ---------------------------------------------------------------------------
# ANALYSIS_REFRESH claim 硬门（同 COLLECT 模式 fail-closed）
# ---------------------------------------------------------------------------


def test_analysis_refresh_type_registered(tasks_db):
    """ANALYSIS_REFRESH 进 TYPES 白名单，add 入队成功，常量正确。"""
    assert "ANALYSIS_REFRESH" in tasks_db.TYPES
    assert tasks_db.ANALYSIS_TYPES == ("ANALYSIS_REFRESH",)
    assert tasks_db.ANALYSIS_CONSUMER == "analysis-watch"
    tid = tasks_db.add("ANALYSIS_REFRESH", "赛力斯", source="news-collector",
                       payload={"reason": "news_signal", "ref": "ND#123"})
    assert tid > 0
    ev = tasks_db.list_events(type_="ANALYSIS_REFRESH")[0]
    assert ev["type"] == "ANALYSIS_REFRESH" and ev["entity"] == "赛力斯"


def test_analysis_refresh_claim_rejects_other_consumer(tasks_db):
    """ANALYSIS_REFRESH 仅 analysis-watch 可 claim：msg-watch/news-collect → PermissionError。"""
    tid = tasks_db.add("ANALYSIS_REFRESH", "赛力斯")
    with pytest.raises(PermissionError):
        tasks_db.claim(tid, consumer="msg-watch")
    with pytest.raises(PermissionError):
        tasks_db.claim(tid, consumer="news-collect")
    # 未被认领（仍 pending）
    ev = tasks_db.list_events(type_="ANALYSIS_REFRESH")[0]
    assert ev["status"] == "pending"


def test_analysis_refresh_claim_rejects_missing_consumer(tasks_db):
    """consumer 缺省（旧心跳漏传）→ fail-closed 拒绝（同 COLLECT 模式）。"""
    tid = tasks_db.add("ANALYSIS_REFRESH", "赛力斯")
    with pytest.raises(PermissionError):
        tasks_db.claim(tid, consumer=None)
    ev = tasks_db.list_events(type_="ANALYSIS_REFRESH")[0]
    assert ev["status"] == "pending"


def test_analysis_refresh_claim_by_analysis_watch_ok(tasks_db):
    """唯一消费者 analysis-watch 认领成功，claimed_by 入 payload。"""
    tid = tasks_db.add("ANALYSIS_REFRESH", "赛力斯")
    row = tasks_db.claim(tid, consumer="analysis-watch")
    assert row is not None
    assert row["status"] == "processing"
    assert json.loads(row["payload"])["claimed_by"] == "analysis-watch"


def test_legacy_types_claim_unaffected_by_analysis_gate(tasks_db):
    """存量类型不校验：CANDIDATE 无 consumer 照常 claim；MSG_*/COLLECT 旧门不变。"""
    tid = tasks_db.add("CANDIDATE", "601127.SH")
    row = tasks_db.claim(tid, consumer=None)  # 存量不校验
    assert row is not None and row["status"] == "processing"
    # MSG_* 旧硬门仍在
    nid = tasks_db.add("MSG_CANDIDATE", "601127.SH")
    with pytest.raises(PermissionError):
        tasks_db.claim(nid, consumer="analysis-watch")
    ok = tasks_db.claim(nid, consumer="msg-watch")
    assert ok is not None
    # COLLECT 旧硬门仍在
    cid = tasks_db.add("COLLECT", "601127.SH")
    with pytest.raises(PermissionError):
        tasks_db.claim(cid, consumer="analysis-watch")
    ok2 = tasks_db.claim(cid, consumer="news-collect")
    assert ok2 is not None


def test_analysis_refresh_claim_cli_exit_codes(tasks_db):
    """CLI 层：错 consumer 退出码 3（硬门拒绝），analysis-watch 退出码 0。"""
    from task_bus.cli import app
    from typer.testing import CliRunner
    runner = CliRunner()
    tid = tasks_db.add("ANALYSIS_REFRESH", "赛力斯")
    r = runner.invoke(app, ["claim", str(tid), "--consumer", "msg-watch"])
    assert r.exit_code == 3  # PermissionError → 3
    r2 = runner.invoke(app, ["claim", str(tid), "--consumer", "analysis-watch"])
    assert r2.exit_code == 0
    assert "analysis-watch" in r2.output


# ---------------------------------------------------------------------------
# analysis_watch_monitor：IDLE 字节稳定 + 队列行
# ---------------------------------------------------------------------------


def _run_monitor(news_db, env=None, cwd=None):
    """跑 monitor 脚本，返回 stdout。"""
    scripts_dir = _SCRIPTS_DIR
    cmd = [sys.executable, str(scripts_dir / "analysis_watch_monitor.py")]
    e = {"STOCK_NEWS_DB": str(news_db)}
    if env:
        e.update(env)
    r = subprocess.run(cmd, capture_output=True, text=True, env=e,
                       cwd=str(cwd or scripts_dir), timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_monitor_idle_byte_stable_on_empty_db(db_path, tmp_path):
    """空表（未 seed）→ 稳定 IDLE，两拍字节一致（无时间戳）。"""
    conn = _conn(db_path)
    conn.close()
    out1 = _run_monitor(db_path, cwd=tmp_path)
    assert out1.strip() == "IDLE"
    out2 = _run_monitor(db_path, cwd=tmp_path)
    assert out1 == out2  # 字节稳定


def test_monitor_idle_after_all_analyzed(db_path, tmp_path):
    """全池 TTL 内（今天刚分析）→ IDLE，两拍字节一致。"""
    conn = _conn(db_path)
    asched.seed_pool(conn, ["茅台", "赛力斯"])
    asched.mark_analyzed(conn, "茅台", result="中性")
    asched.mark_analyzed(conn, "赛力斯", result="偏多")
    conn.close()
    out1 = _run_monitor(db_path, cwd=tmp_path)
    assert out1.strip() == "IDLE"
    out2 = _run_monitor(db_path, cwd=tmp_path)
    assert out1 == out2
    conn = connect(db_path)
    asched.request_refresh(conn, "茅台", reason="news_signal", source="ND#7")
    conn.close()
    out3 = _run_monitor(db_path, cwd=tmp_path)
    assert out3 != out1  # 打标 → 字节变化 → 心跳直醒
    assert "[ANALYSIS-DUE] 茅台" in out3
    assert "原因=refresh:news_signal" in out3


def test_monitor_due_lines_refresh_priority_and_ttl_reason(db_path, tmp_path):
    """队列行：refresh 优先在前（含 refresh:reason），TTL 行含过期天数，两拍稳定。"""
    conn = _conn(db_path)
    asched.seed_pool(conn, ["茅台", "光智", "新易盛"])
    asched.request_refresh(conn, "茅台", reason="price_move", source="daymove")
    asched.mark_analyzed(conn, "光智", result="旧", now="2026-08-28 10:00:00")
    asched.mark_analyzed(conn, "新易盛", result="新鲜", now="2026-09-04 09:30:00")
    conn.close()
    out1 = _run_monitor(db_path, cwd=tmp_path)
    lines = out1.strip().splitlines()
    assert lines[0].startswith("[ANALYSIS-DUE] 茅台 ")
    assert "原因=refresh:price_move" in lines[0]
    assert any(l.startswith("[ANALYSIS-DUE] 光智 ") and "ttl-" in l for l in lines)
    assert not any("新易盛" in l for l in lines)  # TTL 内不出队
    out2 = _run_monitor(db_path, cwd=tmp_path)
    assert out1 == out2  # 字节稳定（无时间戳）


def test_monitor_never_analyzed_shown(db_path, tmp_path):
    """never 行：上次 never + 原因 ttl-never。"""
    conn = _conn(db_path)
    asched.seed_pool(conn, ["工业富联"])
    conn.close()
    out = _run_monitor(db_path, cwd=tmp_path)
    line = [l for l in out.strip().splitlines() if "工业富联" in l][0]
    assert "上次 never" in line
    assert "原因=ttl-never" in line
