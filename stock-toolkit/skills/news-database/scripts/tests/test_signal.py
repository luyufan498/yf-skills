"""预期信号标注：schema 迁移 + 写入 + 关键词回填。"""

import sqlite3

from news_database import storage
from news_database.db import connect, init_db
from news_database.signal import backfill_signals, classify_signal


def _conn(db_path):
    conn = connect(db_path)
    init_db(conn)
    return conn


def _add_msg(conn, title, summary="", source_type="official", **kw):
    eid = storage.create_event(conn, "事件", entity_type="stock")
    return storage.add_message(conn, eid, title, summary=summary,
                               source_type=source_type, **kw)


def test_init_creates_signal_columns(db_path):
    conn = _conn(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)")]
    assert "signal_direction" in cols
    assert "signal_type" in cols
    conn.close()


def test_old_db_migrates_signal_columns(db_path):
    """旧库（无 signal 列）init_db 后应自动补列，旧行回填默认值。"""
    raw = sqlite3.connect(db_path)
    raw.executescript("""
        DROP TABLE IF EXISTS messages;
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            url TEXT, source TEXT, occurred_at TEXT,
            fetched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            importance INTEGER NOT NULL DEFAULT 3, keywords TEXT,
            embedding BLOB, ts_updated TEXT,
            source_type TEXT NOT NULL DEFAULT 'media',
            confidence INTEGER NOT NULL DEFAULT 4,
            message_type TEXT NOT NULL DEFAULT 'other'
        );
        INSERT INTO messages (event_id, title) VALUES (1, '旧消息');
    """)
    raw.commit()
    raw.close()
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT signal_direction, signal_type FROM messages").fetchone()
    assert row["signal_direction"] == "none"
    assert row["signal_type"] == ""
    conn.close()


def test_add_message_defaults(db_path):
    conn = _conn(db_path)
    _add_msg(conn, "普通消息")
    row = conn.execute(
        "SELECT signal_direction, signal_type FROM messages").fetchone()
    assert row["signal_direction"] == "none"
    assert row["signal_type"] == ""
    conn.close()


def test_add_message_with_signal(db_path):
    conn = _conn(db_path)
    _add_msg(conn, "拟回购股份注销", signal_direction="bullish", signal_type="buyback")
    row = conn.execute(
        "SELECT signal_direction, signal_type FROM messages").fetchone()
    assert row["signal_direction"] == "bullish"
    assert row["signal_type"] == "buyback"
    conn.close()


def test_classify_signal_buyback(db_path):
    assert classify_signal("公司拟回购1-2亿元股份并注销") == ("bullish", "buyback")
    assert classify_signal("控股股东拟增持不超过1%股份") == ("bullish", "increase")
    assert classify_signal("大股东拟减持预披露，窗口至8/27") == ("bearish", "reduction")
    assert classify_signal("控股股东质押210万股") == ("bearish", "pledge")
    assert classify_signal("2026半年报预约披露8/25") == ("event", "earnings_preview")
    assert classify_signal("花旗维持卖出评级，目标价60元") == ("bearish", "rating")


def test_backfill_signals(db_path):
    conn = _conn(db_path)
    _add_msg(conn, "公司拟回购股份注销", summary="回购")
    _add_msg(conn, "股东减持预披露", summary="减持")
    _add_msg(conn, "半年报预约8/25披露", summary="业绩预告")
    _add_msg(conn, "中标维信诺项目4728万元", summary="中标")
    _add_msg(conn, "普通经营消息", summary="无信号")
    n, stats = backfill_signals(conn)
    assert n == 4  # 前4条被打标，最后1条保持 none
    rows = {r["title"]: (r["signal_direction"], r["signal_type"])
            for r in conn.execute("SELECT title, signal_direction, signal_type FROM messages")}
    assert rows["公司拟回购股份注销"] == ("bullish", "buyback")
    assert rows["股东减持预披露"] == ("bearish", "reduction")
    assert rows["半年报预约8/25披露"] == ("event", "earnings_preview")
    assert rows["中标维信诺项目4728万元"] == ("bullish", "win_bid")
    assert rows["普通经营消息"] == ("none", "")
    conn.close()


def test_backfill_is_idempotent(db_path):
    conn = _conn(db_path)
    _add_msg(conn, "公司拟回购股份注销", signal_direction="bearish")  # 手动标了别的方向
    _add_msg(conn, "普通消息")
    n, _ = backfill_signals(conn)
    # 已标注的消息不覆盖，只补 none 的
    assert n == 0
    row = conn.execute(
        "SELECT signal_direction FROM messages WHERE title='公司拟回购股份注销'").fetchone()
    assert row["signal_direction"] == "bearish"
    conn.close()


def test_backfill_skips_market_events(db_path):
    """market 类事件（大盘/美股行情）不标个股预期信号。"""
    conn = _conn(db_path)
    eid = storage.create_event(conn, "美股8/10收盘：英伟达5000亿融资，芯片重挫",
                               entity_type="market")
    storage.add_message(conn, eid, "英伟达与六家签署MOU，投资者担忧循环融资",
                        summary="纳指走低，芯片重挫")
    n, _ = backfill_signals(conn)
    assert n == 0  # market 事件整体跳过，不因"签署MOU"误标 bullish
    row = conn.execute(
        "SELECT signal_direction FROM messages").fetchone()
    assert row["signal_direction"] == "none"
    conn.close()


def test_backfill_skips_price_action(db_path):
    """price_action（股价走势）是已发生描述，不标预期。"""
    conn = _conn(db_path)
    _add_msg(conn, "铂科新材8/11收盘+3.01%", summary="回购催化反弹", message_type="price_action")
    n, _ = backfill_signals(conn)
    assert n == 0
    row = conn.execute(
        "SELECT signal_direction FROM messages").fetchone()
    assert row["signal_direction"] == "none"
    conn.close()
