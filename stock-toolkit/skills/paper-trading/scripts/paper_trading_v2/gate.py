"""CLI 能力矩阵闸门（sleeve-m1，方案 2.5/3.2）

组×层正交：只有持仓层实体可触资金；消息组（accounts.grp='news'）：
- 禁 conditions 全家（下单风格=仅 sleeve-fill 开盘成交分支）
- 禁 buy（建仓只走 sleeve-open→sleeve-fill）
- 禁 topup（加仓锁死，灰度）
- 主池 allocate 不得直接买 NEWS 票（须走 sleeve-migrate 迁移桥）
- 迁移票加仓锁（event_slots.topup_locked=1）→ 禁 topup

违例 → 报错 + shadow_log(kind='gate_violation') 留痕（永不静默放行）。
"""
import json
from typing import Optional

from paper_trading_v2.sleeve_slots import now_iso

# 消息组被禁能力
NEWS_BLOCKED = ('conditions_write', 'buy', 'topup', 'allocate')

_MESSAGES = {
    'conditions_write': '消息组（grp=news）禁 conditions 全家（能力矩阵 2.5）——'
                        'sleeve 持仓退出只走保护链/论点失效/sleeve-migrate，'
                        '保护线由 sleeve-fill/atr-sync 挂载',
    'buy': '消息组禁直接 buy——建仓只走 sleeve-open→sleeve-fill 开盘成交分支',
    'topup': '消息组加仓锁死（灰度期）——双轨账 8 周裁决后才复审',
    'allocate': '该股属消息组（grp=news），技术组禁直接买 NEWS 票——须走 sleeve-migrate 迁移桥',
}


class GateViolation(ValueError):
    """能力矩阵违例（已写 shadow_log gate_violation）。"""


def _resolve_db(db_path):
    if db_path:
        return db_path
    from paper_trading_v2.config import get_workspace_config
    return get_workspace_config()['db_path']


def open_conn(db_path=None):
    from paper_trading_v2.db import get_connection, migrate_db
    conn = get_connection(_resolve_db(db_path))
    migrate_db(conn)
    return conn


def account_grp(conn, stock_name) -> Optional[str]:
    row = conn.execute("SELECT grp FROM accounts WHERE stock_name=? ORDER BY id LIMIT 1",
                       (stock_name,)).fetchone()
    return row[0] if row else None


def log_violation(conn, stock_name, capability, detail='', source='agent'):
    conn.execute(
        "INSERT INTO shadow_log (kind, key, payload, created_at) VALUES (?,?,?,?)",
        ('gate_violation', stock_name,
         json.dumps({"capability": capability, "detail": detail, "source": source,
                     "ts": now_iso()}, ensure_ascii=False),
         now_iso()))


def enforce(stock_name, capability, *, conn=None, db_path=None, source='agent',
            pool='main'):
    """能力矩阵前置检查；违例抛 GateViolation（写 shadow_log）。

    conn：调用方已持有事务连接（master_pool 内部路径）时传入，避免自连。
    """
    own = conn is None
    if own:
        conn = open_conn(db_path)
    try:
        row = conn.execute("SELECT grp FROM accounts WHERE stock_name=? ORDER BY id LIMIT 1",
                           (stock_name,)).fetchone()
        grp = row[0] if row else None
        # ① 消息组账户：禁 conditions 写 / buy / topup / 主池 allocate
        if grp == 'news' and capability in NEWS_BLOCKED:
            _reject(conn, stock_name, capability,
                    _MESSAGES[capability], source, own)
        # ② 主池 allocate 直接买 NEWS 档票（须走迁移桥）
        if capability == 'allocate' and pool == 'main':
            prow = conn.execute("SELECT strategy FROM pool WHERE stock=? AND "
                                "pool_status='active'", (stock_name,)).fetchone()
            if prow and prow['strategy'] == 'NEWS':
                _reject(conn, stock_name, capability,
                        '该股在池中为 NEWS 档（消息组信号缓冲）——技术组不得直接 allocate，'
                        '须走 sleeve-migrate 迁移桥', source, own)
        # ③ 迁移票加仓锁（migrated 槽 topup_locked=1）
        if capability == 'topup':
            prow = conn.execute("SELECT event_key FROM pool WHERE stock=?", (stock_name,)).fetchone()
            if prow and prow['event_key']:
                slot = conn.execute("SELECT status, topup_locked FROM event_slots "
                                    "WHERE event_key=?", (prow['event_key'],)).fetchone()
                if slot and slot['status'] == 'migrated' and slot['topup_locked']:
                    _reject(conn, stock_name, capability,
                            '迁移票加仓锁（event_slots.topup_locked=1）——'
                            '二波事件开新槽，不得对已迁移持仓加仓', source, own)
    finally:
        if own:
            conn.close()
    return True


def _reject(conn, stock_name, capability, message, source, own_conn):
    log_violation(conn, stock_name, capability, message, source)
    if own_conn:
        conn.commit()
    raise GateViolation(f"⛔ {message}")


def conditions_write_actions():
    """conditions 命令的写操作集合（--action 值）。读操作（show/event-list/check）放行。"""
    return {'set', 'update', 'remove', 'trigger', 'expire',
            'event-set', 'event-remove', 'event-trigger'}
