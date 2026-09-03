"""M1 执行侧测试：news_collector（run_task/stale_candidates/mark_stale_checked/
stale_line 字节稳定）+ task_bus COLLECT 类型登记与 claim 硬门 + monitor stale 稳定输出。

红线：全部用 /tmp（tmp_path）副本库，生产库零接触。
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# task-bus 包（仓库内兄弟 skill，供 COLLECT claim 硬门测试 import）
_TASKBUS_DIR = _SCRIPTS_DIR.parent.parent / "task-bus" / "scripts"
if str(_TASKBUS_DIR) not in sys.path:
    sys.path.insert(0, str(_TASKBUS_DIR))

from news_database import news_collector as nc  # noqa: E402
from news_database import storage  # noqa: E402
from news_database import task_schedule as ts  # noqa: E402
from news_database.db import connect, init_db  # noqa: E402


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# run_task：成功推进 / FAIL 不推进
# ---------------------------------------------------------------------------


def test_run_task_success_advances(db_path):
    """collector 成功 → mark_task_run 推进 next_due=now+ttl，last_result=摘要。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    ok = nc.run_task(
        conn, ts.DAILY_NEWS,
        lambda row: f"收闻 {row['task_id']} 12 条")
    assert ok is True
    t = ts.get_task(conn, ts.DAILY_NEWS)
    assert t["last_run_at"] is not None
    assert t["last_result"] == "收闻 daily_news 12 条"
    # 推进：last_run + ttl = next_due（T2=12h）
    from datetime import datetime, timedelta
    nxt = datetime.strptime(t["next_due_at"], "%Y-%m-%d %H:%M:%S")
    last = datetime.strptime(t["last_run_at"], "%Y-%m-%d %H:%M:%S")
    assert (nxt - last) == timedelta(hours=12)
    # 推进后不再到期
    assert ts.DAILY_NEWS not in [r["task_id"] for r in ts.due_tasks(conn)]
    conn.close()


def test_run_task_fail_does_not_advance(db_path):
    """collector 抛异常 → last_result='FAIL:...'，next_due 不推进（下次仍到期）。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    before = ts.get_task(conn, ts.DAILY_NEWS)["next_due_at"]

    def boom(row):
        raise RuntimeError("searxng 超时")

    ok = nc.run_task(conn, ts.DAILY_NEWS, boom)
    assert ok is False
    t = ts.get_task(conn, ts.DAILY_NEWS)
    assert t["last_result"].startswith("FAIL:RuntimeError:")
    assert "searxng 超时" in t["last_result"]
    assert t["next_due_at"] == before      # 未推进
    assert t["last_run_at"] is None        # 从未成功过
    # 仍到期（下次心跳还会再试）
    assert ts.DAILY_NEWS in [r["task_id"] for r in ts.due_tasks(conn)]
    conn.close()


def test_run_task_fail_after_success_keeps_due(db_path):
    """先成功推进、后失败：next_due 保持上次成功值（不回退不推进）。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    nc.run_task(conn, ts.DAILY_NEWS, lambda row: "ok")
    advanced = ts.get_task(conn, ts.DAILY_NEWS)["next_due_at"]

    def boom(row):
        raise ValueError("403")

    assert nc.run_task(conn, ts.DAILY_NEWS, boom) is False
    t = ts.get_task(conn, ts.DAILY_NEWS)
    assert t["next_due_at"] == advanced    # 推进值未被失败破坏
    assert t["last_result"].startswith("FAIL:ValueError:")
    conn.close()


def test_run_task_unknown_task_seeds_and_runs(db_path):
    """未知任务：先 seed 默认矩阵；仍无行时 collector 收 None，成功后按缺省 TTL 建行。"""
    conn = _conn(db_path)
    ok = nc.run_task(conn, "brand_new_task", lambda row: "首轮")
    assert ok is True
    t = ts.get_task(conn, "brand_new_task")
    assert t is not None
    assert t["last_result"] == "首轮"
    assert t["ttl_hours"] == ts.TTL_DAILY_NEWS  # 与 mark_task_run 缺省一致
    # seed 顺带把默认矩阵灌了
    assert len(ts.list_tasks(conn)) == len(ts.DEFAULT_TASK_MATRIX) + 1
    conn.close()


def test_run_task_nonstring_result_serialized(db_path):
    """collector 返回 dict → json 序列化进 last_result。"""
    conn = _conn(db_path)
    ts.ensure_default_tasks(conn)
    nc.run_task(conn, ts.DAILY_NEWS, lambda row: {"msgs": 3, "ok": True})
    t = ts.get_task(conn, ts.DAILY_NEWS)
    assert json.loads(t["last_result"]) == {"msgs": 3, "ok": True}
    conn.close()


# ---------------------------------------------------------------------------
# stale_candidates：T5 空置候选（口径=messages 最近关联）
# ---------------------------------------------------------------------------

_NOW = "2026-09-03 12:00:00"


def _seed_watch_stock(conn, code, name, msg_age_days=None, watchlist=1):
    """造 watchlist 股 + （可选）N 天前的消息关联。"""
    storage.upsert_stock(conn, code, name, is_watchlist=watchlist)
    if msg_age_days is None:
        return
    eid = storage.create_event(conn, f"{name}事件")
    storage.link_event_stock(conn, eid, code)
    storage.add_message(conn, eid, f"{name}快讯")
    # add_message fetched_at=now → 手动回拨到 N 天前，模拟旧关联
    old = "2026-09-03 12:00:00"
    from datetime import datetime, timedelta
    ts_old = (datetime.strptime(old, "%Y-%m-%d %H:%M:%S")
              - timedelta(days=msg_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE messages SET fetched_at=? WHERE event_id=?", (ts_old, eid))
    conn.commit()


def test_stale_candidates_recent_message_not_candidate(db_path):
    """7 天内有消息关联 → 非候选。"""
    conn = _conn(db_path)
    _seed_watch_stock(conn, "601127.SH", "赛力斯", msg_age_days=2)
    cands = nc.stale_candidates(conn, days=7, now=_NOW)
    assert [c["code"] for c in cands] == []
    conn.close()


def test_stale_candidates_old_message_is_candidate(db_path):
    """仅 8 天前消息 → 候选，days=8，last_related_at=旧时间。"""
    conn = _conn(db_path)
    _seed_watch_stock(conn, "600519.SH", "贵州茅台", msg_age_days=8)
    cands = nc.stale_candidates(conn, days=7, now=_NOW)
    assert len(cands) == 1
    c = cands[0]
    assert c["code"] == "600519.SH"
    assert c["name"] == "贵州茅台"
    assert c["days"] == 8
    assert c["last_related_at"].startswith("2026-08-26")
    conn.close()


def test_stale_candidates_event_without_message_counts_as_never(db_path):
    """空壳事件（仅 event_stock 关联、无 messages 行）→ 视为从未关联（口径=messages）。"""
    conn = _conn(db_path)
    storage.upsert_stock(conn, "601127.SH", "赛力斯", is_watchlist=1)
    eid = storage.create_event(conn, "赛力斯事件")
    storage.link_event_stock(conn, eid, "601127.SH")  # 只有事件，无消息
    cands = nc.stale_candidates(conn, days=7, now=_NOW)
    assert len(cands) == 1
    assert cands[0]["code"] == "601127.SH"
    assert cands[0]["last_related_at"] is None
    assert cands[0]["days"] is None
    conn.close()


def test_stale_candidates_non_watchlist_ignored(db_path):
    """非池内股（is_watchlist=0）零关联也不进候选。"""
    conn = _conn(db_path)
    _seed_watch_stock(conn, "600000.SH", "浦发银行", msg_age_days=None, watchlist=0)
    assert nc.stale_candidates(conn, days=7, now=_NOW) == []
    conn.close()


def test_stale_candidates_mixed_sorted_worst_first(db_path):
    """混合：从未关联 > 8 天 > 6 天内；排序最久在前。"""
    conn = _conn(db_path)
    _seed_watch_stock(conn, "601127.SH", "赛力斯", msg_age_days=2)    # 非候选
    _seed_watch_stock(conn, "600519.SH", "贵州茅台", msg_age_days=8)  # 8 天
    _seed_watch_stock(conn, "300034.SZ", "钢研高纳")                  # 从未关联
    cands = nc.stale_candidates(conn, days=7, now=_NOW)
    assert [c["code"] for c in cands] == ["300034.SZ", "600519.SH"]
    assert cands[0]["days"] is None and cands[1]["days"] == 8
    conn.close()


def test_stale_candidates_empty_watchlist(db_path):
    """无池内股 → 空列表。"""
    conn = _conn(db_path)
    assert nc.stale_candidates(conn, days=7, now=_NOW) == []
    conn.close()


def test_mark_stale_checked_upsert(db_path):
    """检查留痕：插入后重复检查覆盖（last_checked/result）。"""
    conn = _conn(db_path)
    nc.mark_stale_checked(conn, "600519.SH", "found:中标公告")
    nc.mark_stale_checked(conn, "600519.SH", "vacant")
    rows = conn.execute("SELECT code, result FROM stale_check_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["code"] == "600519.SH"
    assert rows[0]["result"] == "vacant"
    conn.close()


# ---------------------------------------------------------------------------
# kv（newsdb 侧轻量状态）
# ---------------------------------------------------------------------------


def test_kv_set_get_roundtrip(db_path):
    conn = _conn(db_path)
    nc.kv_set(conn, "k1", {"a": 1})
    assert nc.kv_get(conn, "k1") == {"a": 1}
    nc.kv_set(conn, "k1", "plain")
    assert nc.kv_get(conn, "k1") == "plain"
    assert nc.kv_get(conn, "missing") is None
    conn.close()


def test_kv_creates_table_on_old_db(db_path):
    """旧库（无 kv_store 表）kv_set/kv_get 自动补表，不影响既有表。"""
    conn = _conn(db_path)
    storage.upsert_stock(conn, "600519.SH", "贵州茅台")
    nc.kv_set(conn, "last_stale_sig", "abc")
    assert nc.kv_get(conn, "last_stale_sig") == "abc"
    assert conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# stale_line / stale_sig：monitor 输出字节稳定性
# ---------------------------------------------------------------------------


def _cand(code, days):
    return {"code": code, "name": code, "last_related_at": None, "days": days}


def test_stale_line_disabled_returns_empty(db_path):
    conn = _conn(db_path)
    assert nc.stale_line([_cand("600519.SH", 8)], conn, enabled=False) == []
    conn.close()


def test_stale_line_zero_candidates_empty_output(db_path):
    """N=0 → 输出空（任务定案）。"""
    conn = _conn(db_path)
    assert nc.stale_line([], conn, enabled=True) == []
    conn.close()


def test_stale_line_first_beat_then_stable(db_path):
    """首拍（发现）→ 含最长天数行；集合不变 → 固定行（两拍同字节）。"""
    conn = _conn(db_path)
    cands = [_cand("600519.SH", 8), _cand("300034.SZ", None)]
    out1 = nc.stale_line(cands, conn, enabled=True)
    assert out1 == ["[STALE] 空置候选 2 只（最长 8 天）"]
    out2 = nc.stale_line(cands, conn, enabled=True)
    assert out2 == ["[STALE] 空置候选 2 只"]  # 固定行含 N，不含天数
    out3 = nc.stale_line(cands, conn, enabled=True)
    assert out2 == out3                      # 候选集不变 → 字节稳定
    conn.close()


def test_stale_line_set_change_same_n_new_bytes(db_path):
    """N 不变但集合成员变化（A 恢复、B 新空置）→ 字节必变。"""
    conn = _conn(db_path)
    out1 = nc.stale_line([_cand("600519.SH", 8)], conn, enabled=True)
    assert out1 == ["[STALE] 空置候选 1 只（最长 8 天）"]
    # 集合变化（600519 恢复关联，300034 新空置且从未关联）
    out2 = nc.stale_line([_cand("300034.SZ", None)], conn, enabled=True)
    assert out2 == ["[STALE] 空置候选 1 只（名单更新）"]
    assert out1 != out2
    # 恢复为同集合 → 固定行
    out3 = nc.stale_line([_cand("300034.SZ", None)], conn, enabled=True)
    assert out3 == ["[STALE] 空置候选 1 只"]
    conn.close()


def test_stale_line_all_never_first_beat(db_path):
    """全部从未关联（首次启用 T5 场景）→ 名单更新行。"""
    conn = _conn(db_path)
    out = nc.stale_line([_cand("600519.SH", None)], conn, enabled=True)
    assert out == ["[STALE] 空置候选 1 只（名单更新）"]
    out2 = nc.stale_line([_cand("600519.SH", None)], conn, enabled=True)
    assert out2 == ["[STALE] 空置候选 1 只"]
    conn.close()


def test_stale_sig_set_sensitive():
    """签名对集合成员/顺序敏感。"""
    s1 = nc.stale_sig([_cand("A", 1)])
    s2 = nc.stale_sig([_cand("B", 1)])
    s3 = nc.stale_sig([_cand("A", 1), _cand("B", 2)])
    assert len({s1, s2, s3}) == 3


# ---------------------------------------------------------------------------
# task_bus：COLLECT 类型登记 + claim 硬门
# ---------------------------------------------------------------------------


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


def test_collect_type_registered(tasks_db):
    """COLLECT 进 TYPES 白名单，add 入队成功。"""
    assert "COLLECT" in tasks_db.TYPES
    assert tasks_db.COLLECT_TYPES == ("COLLECT",)
    assert tasks_db.COLLECT_CONSUMER == "news-collect"
    tid = tasks_db.add("COLLECT", "601127.SH", source="news-collector",
                       payload={"reason": "异动补搜"})
    assert tid > 0
    ev = tasks_db.list_events(type_="COLLECT")[0]
    assert ev["type"] == "COLLECT" and ev["entity"] == "601127.SH"


def test_collect_claim_hard_gate_rejects_other_consumer(tasks_db):
    """COLLECT 仅 news-collect 可 claim：msg-watch/其他 consumer → PermissionError。"""
    tid = tasks_db.add("COLLECT", "601127.SH")
    with pytest.raises(PermissionError):
        tasks_db.claim(tid, consumer="msg-watch")
    with pytest.raises(PermissionError):
        tasks_db.claim(tid, consumer="morning-review")
    # 未被认领（仍 pending）
    ev = tasks_db.list_events(type_="COLLECT")[0]
    assert ev["status"] == "pending"


def test_collect_claim_hard_gate_rejects_missing_consumer(tasks_db):
    """consumer 缺省（旧 prompt 漏传）→ fail-closed 拒绝。"""
    tid = tasks_db.add("COLLECT", "601127.SH")
    with pytest.raises(PermissionError):
        tasks_db.claim(tid, consumer=None)
    ev = tasks_db.list_events(type_="COLLECT")[0]
    assert ev["status"] == "pending"


def test_collect_claim_by_news_collect_ok(tasks_db):
    """唯一消费者 news-collect 认领成功，claimed_by 入 payload。"""
    tid = tasks_db.add("COLLECT", "601127.SH")
    row = tasks_db.claim(tid, consumer="news-collect")
    assert row is not None
    assert row["status"] == "processing"
    assert json.loads(row["payload"])["claimed_by"] == "news-collect"


def test_legacy_types_claim_unaffected(tasks_db):
    """存量类型（CANDIDATE/NEWS_CANDIDATE 硬门）行为不变：CANDIDATE 无 consumer 可认领。"""
    tid = tasks_db.add("CANDIDATE", "601127.SH")
    row = tasks_db.claim(tid, consumer=None)  # 存量不校验
    assert row is not None and row["status"] == "processing"
    # NEWS_* 旧硬门仍在：非 msg-watch 拒绝
    nid = tasks_db.add("NEWS_CANDIDATE", "601127.SH")
    with pytest.raises(PermissionError):
        tasks_db.claim(nid, consumer="news-collect")
    ok = tasks_db.claim(nid, consumer="msg-watch")
    assert ok is not None


def test_collect_claim_cli_exit_codes(tasks_db, capsys):
    """CLI 层：COLLECT 他人认领退出码 3（硬门拒绝），正确 consumer 退出码 0。"""
    from typer.testing import CliRunner
    from task_bus.cli import app
    runner = CliRunner()
    tid = tasks_db.add("COLLECT", "601127.SH")
    r = runner.invoke(app, ["claim", str(tid), "--consumer", "msg-watch"])
    assert r.exit_code == 3  # PermissionError → 3
    r2 = runner.invoke(app, ["claim", str(tid), "--consumer", "news-collect"])
    assert r2.exit_code == 0
    assert "news-collect" in r2.output


# ---------------------------------------------------------------------------
# monitor：stale 集合签名稳定输出（字节级）
# ---------------------------------------------------------------------------


def _run_monitor(news_db, tasks_db=None, env=None, cwd=None):
    import subprocess
    scripts_dir = _SCRIPTS_DIR
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


def test_monitor_stale_byte_stable_when_set_unchanged(db_path, tmp_path, monkeypatch):
    """候选集不变：首拍发现行，第二/三拍固定行同字节（无变化不唤醒）。"""
    conn = _conn(db_path)
    _seed_watch_stock(conn, "600519.SH", "贵州茅台", msg_age_days=8)
    conn.close()
    out1 = _run_monitor(db_path, tmp_path / "no.db",
                        env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert "[STALE] 空置候选 1 只（最长 8 天）" in out1
    out2 = _run_monitor(db_path, tmp_path / "no.db",
                        env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert "[STALE] 空置候选 1 只" in out2
    out3 = _run_monitor(db_path, tmp_path / "no.db",
                        env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert out2 == out3  # 两拍同字节


def test_monitor_stale_byte_changes_when_set_changes(db_path, tmp_path, monkeypatch):
    """候选集变化（候选恢复关联）→ 输出变化字节（唤醒语义）。"""
    conn = _conn(db_path)
    _seed_watch_stock(conn, "600519.SH", "贵州茅台", msg_age_days=8)
    conn.close()
    out1 = _run_monitor(db_path, tmp_path / "no.db",
                        env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert "[STALE]" in out1
    # 候选恢复关联（新消息今天入）→ N=0 → 无 STALE 行
    conn = _conn(db_path)
    eid = storage.create_event(conn, "茅台新公告")
    storage.link_event_stock(conn, eid, "600519.SH")
    storage.add_message(conn, eid, "公告快讯")
    conn.close()
    out2 = _run_monitor(db_path, tmp_path / "no.db",
                        env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert "[STALE]" not in out2
    # 再次空置（另一只）→ 字节变化（N=1 新名单）
    conn = _conn(db_path)
    _seed_watch_stock(conn, "300034.SZ", "钢研高纳")
    conn.close()
    out3 = _run_monitor(db_path, tmp_path / "no.db",
                        env={"NEWS_COLLECT_STALE_CHECK": "1"})
    assert "[STALE] 空置候选 1 只（名单更新）" in out3
    assert out1 != out3


def test_monitor_stale_disabled_by_default_no_bytes(db_path, tmp_path, monkeypatch):
    """开关关（默认）→ 无 STALE 行，IDLE 稳定。"""
    conn = _conn(db_path)
    _seed_watch_stock(conn, "600519.SH", "贵州茅台", msg_age_days=8)
    conn.close()
    out = _run_monitor(db_path, tmp_path / "no.db")
    assert "[STALE]" not in out
    assert out.strip() == "IDLE"


def test_monitor_collect_pending_after_register(db_path, tmp_path, monkeypatch):
    """COLLECT 登记后：tasks.db pending COLLECT → [COLLECT-INJECT] pending N（不变）。"""
    monkeypatch.setenv("STOCK_TASKS_DB", str(tmp_path / "tasks.db"))
    for mod in list(sys.modules):
        if mod == "task_bus" or mod.startswith("task_bus."):
            del sys.modules[mod]
    from task_bus import db as tdb
    tdb.add("COLLECT", "601127.SH")
    tdb.add("COLLECT", "600519.SH")
    out = _run_monitor(db_path, tmp_path / "tasks.db")
    assert "[COLLECT-INJECT] pending 2" in out
