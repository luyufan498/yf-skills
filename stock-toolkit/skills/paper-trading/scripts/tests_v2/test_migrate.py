"""迁移测试：v1 JSON 导入器在 v9（账户层退役）下的合同

v9（M1.6 账户层退役）起 migrate_existing 显式拒绝：accounts 表退役、段即账户，
"一股一户"导入语义无处安放；历史导入已于 2026-08-10 在 v8 完成并归档。
本套件锁定：v9+ 上拒绝、报错可读、零部分写入（先查版本后动库）。
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from pathlib import Path


def _make_json_account(tmp_path, name='赛力斯', code='sh603527', available=500000.0):
    """造一个假 JSON 账户目录（account + operations + conditions）"""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    with open(d / 'account.json', 'w', encoding='utf-8') as f:
        json.dump({
            'stock_name': name, 'stock_code': code,
            'capital_pool': {'total': 500000, 'available': available, 'used': 0},
            'positions': [], 'fifo_index': -1, 'fifo_offset': 0.0,
            'exright_applied': [],
            'created_at': '2026-01-01T09:00:00', 'updated_at': '2026-01-01T09:00:00',
        }, f, ensure_ascii=False)
    with open(d / 'operations.json', 'w', encoding='utf-8') as f:
        json.dump({'stock_name': name, 'operations': [
            {'type': 'init', 'capital': 500000, 'timestamp': '2026-01-01T09:00:00'},
            {'type': 'buy', 'price': 100.0, 'quantity': 1000, 'amount': 100000,
             'timestamp': '2026-01-02T10:00:00'},
        ]}, f, ensure_ascii=False)
    with open(d / 'conditions.json', 'w', encoding='utf-8') as f:
        json.dump({'stock_name': name, 'updated_at': '2026-01-02T10:00:00',
                   'conditions': {'trailing_stop': {
                       'id': 'abc12345', 'type': 'trailing_stop', 'name': '移动止损',
                       'price': 75.0, 'action': '减仓50%', 'category': 'hard',
                       'expiry_date': None, 'status': 'active', 'auto_link_cost': False,
                       'peak_price': 78.0, 'history': [], 'created_at': '2026-01-01T09:00:00',
                       'modified_at': '2026-01-02T09:00:00'}},
                   'events': []}, f, ensure_ascii=False)
    return d


def test_migrate_existing_rejects_v9(tmp_path):
    """v9 段即账户：v1 一股一户导入语义无处安放 → 显式拒绝（RuntimeError 可读）。"""
    from paper_trading_v2.migrate import migrate_existing
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    _make_json_account(src, '赛力斯', available=550000.0)  # 盈利 5 万
    with pytest.raises(RuntimeError, match='v1 JSON 导入器'):
        migrate_existing(src, tmp_path / 'master_pool.db', tmp_path / 'archive')


def test_migrate_rejection_leaves_no_partial_state(tmp_path):
    """拒绝发生在动库之前：库可能未建/为空，但绝无半成品账户数据。"""
    from paper_trading_v2.migrate import migrate_existing
    from paper_trading_v2.db import get_connection, migrate_db
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    _make_json_account(src, '英维克', code='sz000301', available=500000.0)
    db = tmp_path / 'master_pool.db'
    with pytest.raises(RuntimeError, match='v1 JSON 导入器'):
        migrate_existing(src, db, tmp_path / 'archive')
    # 显式建库后核对零残留（无账户壳、无段、无流水）
    conn = get_connection(db)
    migrate_db(conn)
    try:
        assert conn.execute("SELECT COUNT(*) FROM accounts_old").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM position").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM conditions").fetchone()[0] == 0
    finally:
        conn.close()


def test_migrate_empty_dir_rejects_v9(tmp_path):
    from paper_trading_v2.migrate import migrate_existing
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    with pytest.raises(RuntimeError, match='v1 JSON 导入器'):
        migrate_existing(src, tmp_path / 'master_pool.db', tmp_path / 'archive')
