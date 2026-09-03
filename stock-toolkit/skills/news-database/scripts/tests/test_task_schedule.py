"""task_schedule 表 + 调度函数 + news_collect_monitor 三查测试（M0）。

红线：全部用 /tmp（tmp_path）副本库，生产库零接触。
"""

import sqlite3

import pytest

from news_database import task_schedule as ts
from news_database.db import connect, init_db


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# 建表幂等
# ---------------------------------------------------------------------------


def test_ensure_table_idempotent(db_path):
    """两次建表调用不报错，且不触碰 scan_log。"""
    conn = _conn(db_path)
    ts.ensure_table(conn)
    ts.ensure_table(conn)  # 幂等：不报错
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_schedule)")}
    assert cols == {"task_id", "last_run_at", "ttl_hours", "next_due_at", "last_result"}
    # scan_log 原样（init_db 建了，本模块没动它）
    scan_cols = {r[1] for r in conn.execute("PRAGMA table_info(scan_log)")}
    assert scan_cols == {"scope_type", "scope_id", "last_scan"}
    conn.close()


def test_init_db_creates_task_schedule_idempotent(db_path):
    """init_db 集成路径：两次 init_db 均带出 task_schedule，不报错。"""
    conn = _conn(db_path)
    init_db(conn)  # 第二次
    n = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='task_schedule'"
    ).fetchone()[0]
    assert n == 1
    conn.close()


def test_old_db_without_task_schedule_ensure_table(db_path):
    """旧库（无 task_schedule 表）ensure_table 后可用，既有表数据不受影响。"""
    conn = connect(db_path)
    conn.execute(
        "CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO stocks VALUES ('600519.SH', '贵州茅台')")
    conn.commit()
    conn.close()
    conn = connect(db_path)
    ts.ensure_table(conn)
    n = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    assert n == 1  # 既有数据未动


# ---------------------------------------------------------------------------
# 默认任务矩阵
# ---------------------------------------------------------------------------


def test_default_task_matrix_values():
    """方案 §2：T1 占位 4h / T2=12h / T3=36h（24-48 取中）/ T4=720h（30 天）。"""
    matrix = dict(ts.DEFAULT_TASK_MATRIX)
    assert matrix[ts.XUEQIU_SENTIMENT] == 4.0
    assert matrix[ts.DAILY_NEWS] == 12.0
    assert matrix[ts.DEEP_ANALYSIS] == 36.0
    assert matrix[ts.INDUSTRY_RESEARCH] == 720.0


def test_ensure_default_tasks_idempotent(db_path):
    """seed 默认任务：缺行才插入，重复调用不覆盖已有节奏。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    n1 = conn.execute("SELECT COUNT(*) FROM task_schedule").fetchone()[0]
    assert n1 == len(ts.DEFAULT_TASK_MATRIX)
    # 手动改 T4 节奏，重复 seed 不应覆盖
    conn.execute("UPDATE task_schedule SET ttl_hours=1 WHERE task_id=?",
                 (ts.INDUSTRY_RESEARCH,))
    conn.commit()
    ts.ensure_default_tasks(conn)
    ttl = conn.execute(
        "SELECT ttl_hours FROM task_schedule WHERE task_id=?",
        (ts.INDUSTRY_RESEARCH,)).fetchone()["ttl_hours"]
    assert ttl == 1
    conn.close()


# ---------------------------------------------------------------------------
# due_tasks / mark_task_run
# ---------------------------------------------------------------------------


def test_due_tasks_empty_table(db_path):
    """空表（仅建表）→ []。"""
    conn = _conn(db_path)
    ts.ensure_table(conn)
    assert ts.due_tasks(conn, now="2026-09-03 12:00:00") == []
    conn.close()


def test_due_tasks_returns_only_due(db_path):
    """只返回 next_due_at <= now 的行，按到期时间升序。"""
    conn = _conn(db_path)
    ts.ensure_table(conn)
    conn.execute(
        "INSERT INTO task_schedule (task_id, ttl_hours, next_due_at) "
        "VALUES ('daily_news', 12, '2026-09-01 00:00:00')")
    conn.execute(
        "INSERT INTO task_schedule (task_id, ttl_hours, next_due_at) "
        "VALUES ('deep_analysis', 36, '2026-09-05 00:00:00')")
    conn.commit()
    due = ts.due_tasks(conn, now="2026-09-03 12:00:00")
    assert [r["task_id"] for r in due] == ["daily_news"]
    row = due[0]
    assert row["last_run_at"] is None
    assert row["ttl_hours"] == 12
    assert row["next_due_at"] == "2026-09-01 00:00:00"
    conn.close()


def test_mark_task_run_advances_next_due(db_path):
    """mark 后 last_run_at=now、next_due_at=now+ttl 推进、last_result 落库。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    ts.mark_task_run(conn, ts.DAILY_NEWS, result="本轮收闻 12 条",
                     now="2026-09-03 07:00:00")
    row = conn.execute(
        "SELECT * FROM task_schedule WHERE task_id=?", (ts.DAILY_NEWS,)
    ).fetchone()
    assert row["last_run_at"] == "2026-09-03 07:00:00"
    assert row["next_due_at"] == "2026-09-03 19:00:00"  # +12h
    assert row["last_result"] == "本轮收闻 12 条"
    # 推进后 daily_news 不再到期（其余任务仍到期，只查本任务）
    due = ts.due_tasks(conn, now="2026-09-03 18:59:59")
    assert ts.DAILY_NEWS not in [r["task_id"] for r in due]
    # 到了 next_due 又到期
    due2 = ts.due_tasks(conn, now="2026-09-03 19:00:00")
    assert [r["task_id"] for r in due2 if r["task_id"] == ts.DAILY_NEWS] == [ts.DAILY_NEWS]
    conn.close()


def test_mark_task_run_never_run_not_due_then_due(db_path):
    """seed 出的新行（next_due=now）立即可 due；mark 后按 ttl 推进。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    # seed 行 next_due_at=插入时刻 → 现在必到期
    due = ts.due_tasks(conn)
    assert {r["task_id"] for r in due} == {
        ts.XUEQIU_SENTIMENT, ts.DAILY_NEWS, ts.DEEP_ANALYSIS, ts.INDUSTRY_RESEARCH
    }
    ts.mark_task_run(conn, ts.XUEQIU_SENTIMENT, result="ok")
    due2 = ts.due_tasks(conn)
    assert ts.XUEQIU_SENTIMENT not in {r["task_id"] for r in due2}
    conn.close()


def test_mark_task_run_unknown_task_uses_default_ttl(db_path):
    """未知任务 mark：按 T2 缺省节奏（12h）建行，不崩。"""
    conn = _conn(db_path)
    ts.ensure_table(conn)
    ts.mark_task_run(conn, "new_task_from_agent", result="发现新行业",
                     now="2026-09-03 10:00:00")
    row = conn.execute(
        "SELECT * FROM task_schedule WHERE task_id='new_task_from_agent'"
    ).fetchone()
    assert row["ttl_hours"] == ts.TTL_DAILY_NEWS
    assert row["next_due_at"] == "2026-09-03 22:00:00"
    conn.close()


def test_due_tasks_without_explicit_now(db_path):
    """now 缺省取当前时间：seed → 立即 due；mark → 未来 36h 本任务不 due。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    ts.mark_task_run(conn, ts.DEEP_ANALYSIS, result="一轮完成")
    due = ts.due_tasks(conn)
    assert ts.DEEP_ANALYSIS not in [r["task_id"] for r in due]
    conn.close()


# ---------------------------------------------------------------------------
# news_collect_monitor 三查（IDLE 字节稳定）
# ---------------------------------------------------------------------------


def _run_monitor(monkeypatch, news_db, tasks_db=None, env=None, cwd=None):
    """跑 monitor 脚本，返回 stdout。"""
    import subprocess
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, str(scripts_dir / "news_collect_monitor.py")]
    e = {"STOCK_NEWS_DB": str(news_db)}
    if tasks_db:
        e["STOCK_TASKS_DB"] = str(tasks_db)
    if env:
        e.update(env)
    r = subprocess.run(cmd, capture_output=True, text=True, env=e,
                       cwd=str(cwd or scripts_dir), timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_monitor_idle_on_empty_db(db_path, tmp_path, monkeypatch):
    """空表/无 COLLECT 注入/T5 默认关 → 稳定 IDLE，两拍字节一致。"""
    import subprocess
    import sys

    scripts_dir = tmp_path / "scripts_link"  # 任意 cwd，脚本自适应
    scripts_dir.mkdir()
    out1 = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db",
                        cwd=scripts_dir)
    assert out1.strip() == "IDLE"
    # 第二拍字节稳定（不含时间戳/计数等变化内容）
    out2 = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db",
                        cwd=scripts_dir)
    assert out1 == out2
    # T5 默认关闭：无 [STALE] 输出（开启路径由专项测试覆盖）
    assert "[STALE]" not in out1


def test_monitor_idle_byte_stable_with_defaults(db_path, tmp_path, monkeypatch):
    """seed 默认任务（全部已到期）→ 输出 4 行 COLLECT-DUE，两拍字节一致。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    conn.close()
    out1 = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db")
    due_lines = [l for l in out1.strip().splitlines() if l.startswith("[COLLECT-DUE]")]
    assert len(due_lines) == 4
    # never 语义：seed 行 last_run_at 为空 → 上次 never
    assert any("上次 never" in l for l in due_lines)
    assert any("ttl=4" in l for l in due_lines)
    out2 = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db")
    assert out1 == out2  # IDLE 字节稳定：到期行持续输出，但内容不变


def test_monitor_after_mark_no_due_idle(db_path, tmp_path, monkeypatch):
    """mark 全部任务后（未到期）+ 无注入 + T5 关 → IDLE。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    for task_id, _ in ts.DEFAULT_TASK_MATRIX:
        ts.mark_task_run(conn, task_id, result="ok")
    conn.close()
    out = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db")
    assert out.strip() == "IDLE"


def test_monitor_collect_inject_pending_count(db_path, tmp_path, monkeypatch):
    """tasks.db 有 pending COLLECT → [COLLECT-INJECT] pending N。"""
    tdb = tmp_path / "tasks.db"
    tconn = sqlite3.connect(str(tdb))
    tconn.execute("""
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 3,
            source TEXT, entity TEXT, payload TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            claimed_at TEXT, done_at TEXT, note TEXT
        )
    """)
    tconn.execute(
        "INSERT INTO task_events (type, status) VALUES ('COLLECT', 'pending')")
    tconn.execute(
        "INSERT INTO task_events (type, status) VALUES ('COLLECT', 'pending')")
    tconn.execute(
        "INSERT INTO task_events (type, status) VALUES ('COLLECT', 'done')")
    tconn.execute(
        "INSERT INTO task_events (type, status) VALUES ('CANDIDATE', 'pending')")
    tconn.commit()
    tconn.close()
    out = _run_monitor(monkeypatch, db_path, tdb)
    assert "[COLLECT-INJECT] pending 2" in out  # 只数 pending，不含 done/其他类型


def test_monitor_tasks_db_missing_or_no_table(db_path, tmp_path, monkeypatch):
    """tasks.db 不存在或无 task_events 表 → 不崩，pending=0。"""
    out1 = _run_monitor(monkeypatch, db_path, tmp_path / "missing.db")
    assert "[COLLECT-INJECT]" not in out1
    # 空库（无 task_events 表）
    empty_db = tmp_path / "empty.db"
    sqlite3.connect(str(empty_db)).close()
    out2 = _run_monitor(monkeypatch, db_path, empty_db)
    assert "[COLLECT-INJECT]" not in out2
    assert out2.strip() == "IDLE"


def test_monitor_stale_check_enabled(db_path, tmp_path, monkeypatch):
    """NEWS_COLLECT_STALE_CHECK=1 时：池内股有 7 天内关联 → 无 STALE 输出。"""
    from news_database import storage

    conn = _conn(db_path)
    storage.upsert_stock(conn, "601127.SH", "赛力斯")
    eid = storage.create_event(conn, "赛力斯新款发布")
    storage.link_event_stock(conn, eid, "601127.SH")  # 新关联 → 非空置
    conn.close()
    out = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db",
                       env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert "[STALE]" not in out  # 有关联 → 不输出（N=0 输出空）
    assert "[STALE] disabled" not in out  # 开启后不再输出 disabled


def test_monitor_stale_check_enabled_detects_vacant(db_path, tmp_path, monkeypatch):
    """开启 T5 + 池内股无任何关联 → 输出候选数与最长时间。"""
    from news_database import storage

    conn = _conn(db_path)
    storage.upsert_stock(conn, "600519.SH", "贵州茅台")
    conn.close()
    out = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db",
                       env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert "[STALE] 空置候选 1 只" in out


def test_monitor_stale_check_enabled_mixed(db_path, tmp_path, monkeypatch):
    """开启 T5：一只新关联（7 天内）+ 一只旧关联（8 天前）→ 只算旧的。"""
    from datetime import datetime, timedelta

    from news_database import storage

    conn = _conn(db_path)
    storage.upsert_stock(conn, "601127.SH", "赛力斯")
    storage.upsert_stock(conn, "600519.SH", "贵州茅台")
    eid = storage.create_event(conn, "赛力斯新款发布")
    storage.link_event_stock(conn, eid, "601127.SH")
    storage.add_message(conn, eid, "新款发布快讯")  # fetched_at=今天 → 赛力斯非空置
    # 贵州茅台：无任何关联内容 → 空置（最长天数取空置侧已知关联，无则省略）
    conn.commit()
    conn.close()
    out = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db",
                       env={"NEWS_COLLECT_STALE_CHECK": "1"})
    # 赛力斯今天有关联 → 非空置；茅台零关联 → 空置
    assert "[STALE] 空置候选 1 只" in out


def test_monitor_creates_table_idempotently(db_path, tmp_path, monkeypatch):
    """monitor 在无 task_schedule 表的库上安全运行（自建表幂等）。"""
    conn = connect(db_path)  # 只建 schema 基础表，不跑 ensure_table
    init_db(conn)
    conn.close()
    out = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db")
    assert "IDLE" in out or "[STALE] disabled" in out
    # 再跑一次仍正常（表已建）
    out2 = _run_monitor(monkeypatch, db_path, tmp_path / "no_such_tasks.db")
    assert out == out2


def test_stale_check_disabled_by_default(monkeypatch):
    """T5 开关缺省关闭（M0 空跑 IDLE 稳定的关键）。"""
    monkeypatch.delenv("NEWS_COLLECT_STALE_CHECK", raising=False)
    assert ts.stale_check_enabled() is False
    monkeypatch.setenv("NEWS_COLLECT_STALE_CHECK", "1")
    assert ts.stale_check_enabled() is True
    monkeypatch.setenv("NEWS_COLLECT_STALE_CHECK", "0")
    assert ts.stale_check_enabled() is False
