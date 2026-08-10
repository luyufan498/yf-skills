"""迁移测试：JSON 账户 → SQLite"""
import sys, os, json, shutil
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


def test_migrate_existing(tmp_path):
    from paper_trading_v2.migrate import migrate_existing
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    _make_json_account(src, '赛力斯', available=550000.0)  # 盈利 5 万
    db = tmp_path / 'master_pool.db'
    arch = tmp_path / 'archive'
    result = migrate_existing(src, db, arch)
    assert result['count'] == 1
    # SQLite 有账户 + 操作
    from paper_trading_v2.storage import SqlStorage
    s = SqlStorage(db)
    acct = s.load_account('赛力斯')
    assert acct is not None
    assert acct.capital_pool.total == 500000
    assert acct.capital_pool.available == 550000
    ops = s.load_operations('赛力斯')
    assert ops is not None and len(ops.operations) == 2
    # 条件迁入 SQLite
    from paper_trading_v2.conditions_manager import ConditionsManager
    cm = ConditionsManager(storage=s)
    rec = cm.load_conditions('赛力斯')
    assert rec is not None
    assert 'trailing_stop' in rec.conditions
    assert rec.conditions['trailing_stop'].id == 'abc12345'
    assert rec.conditions['trailing_stop'].peak_price == 78.0
    # closed 段落库
    conn = s._conn()
    try:
        seg = conn.execute("SELECT * FROM position WHERE stock='赛力斯'").fetchone()
        assert seg is not None
        assert seg['status'] == 'closed'
        assert seg['close_value'] == 550000
        assert seg['realized_pnl'] == 50000
    finally:
        conn.close()
    # 源目录已移走
    assert not (src / '赛力斯').exists()
    assert (arch / '赛力斯' / 'account.json').exists()


def test_migrate_empty_dir(tmp_path):
    from paper_trading_v2.migrate import migrate_existing
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    result = migrate_existing(src, tmp_path / 'master_pool.db', tmp_path / 'archive')
    assert result['count'] == 0


def test_migrate_legacy_operation_and_decision(tmp_path):
    """v1 旧格式 operation 字段 + decision 记录：归一 + 过滤，不中止整批"""
    from paper_trading_v2.migrate import migrate_existing
    from paper_trading_v2.storage import SqlStorage
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    d = _make_json_account(src, '英维克', code='sz000301', available=500000.0)
    with open(d / 'operations.json', 'w', encoding='utf-8') as f:
        json.dump({'stock_name': '英维克', 'operations': [
            {'operation': 'init', 'capital': 500000, 'timestamp': '2026-01-01T09:00:00'},
            {'type': 'decision', 'content': 'x', 'timestamp': '2026-01-01T09:05:00'},
            {'type': 'buy', 'price': 50.0, 'quantity': 1000, 'amount': 50000,
             'timestamp': '2026-01-02T10:00:00'},
        ]}, f, ensure_ascii=False)
    result = migrate_existing(src, tmp_path / 'master_pool.db', tmp_path / 'archive')
    assert result['count'] == 1
    s = SqlStorage(tmp_path / 'master_pool.db')
    ops = s.load_operations('英维克')
    assert len(ops.operations) == 2  # init + buy，decision 被过滤
    assert ops.operations[0].type == 'init'


def test_migrate_idempotent_reskip(tmp_path):
    """已迁移账户重跑：跳过，不重复落段"""
    from paper_trading_v2.migrate import migrate_existing
    from paper_trading_v2.storage import SqlStorage
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    db = tmp_path / 'master_pool.db'
    _make_json_account(src, '赛力斯', available=550000.0)
    migrate_existing(src, db, tmp_path / 'archive')
    # 把归档目录复制回来模拟中断后重跑
    shutil.copytree(tmp_path / 'archive' / '赛力斯', src / '赛力斯')
    result = migrate_existing(src, db, tmp_path / 'archive2')
    assert result['count'] == 0  # 已迁移，跳过
    s = SqlStorage(db)
    conn = s._conn()
    try:
        c = conn.execute("SELECT COUNT(*) c FROM position WHERE stock='赛力斯'").fetchone()['c']
        assert c == 1  # 只有一段
    finally:
        conn.close()


def test_migrate_salvages_conditions_with_bad_event(tmp_path):
    """坏 event 不丢整只股票的条件"""
    from paper_trading_v2.migrate import migrate_existing
    from paper_trading_v2.storage import SqlStorage
    from paper_trading_v2.conditions_manager import ConditionsManager
    src = tmp_path / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    d = _make_json_account(src, '科创新源', code='sz300731', available=500000.0)
    with open(d / 'conditions.json', 'w', encoding='utf-8') as f:
        json.dump({
            'stock_name': '科创新源',
            'conditions': {'trailing_stop': {
                'id': 'abc12345', 'type': 'trailing_stop', 'name': '移动止损',
                'price': 75.0, 'action': '减仓50%', 'category': 'hard',
                'expiry_date': None, 'status': 'active', 'auto_link_cost': False,
                'peak_price': 78.0, 'history': [], 'created_at': '2026-01-01T09:00:00',
                'modified_at': '2026-01-02T09:00:00'}},
            'events': [{'id': 'ev1', 'type': 'loss_protect', 'name': '亏损保护',
                        'price': 70.0, 'action': '清仓', 'category': 'hard',
                        'status': 'active', 'history': []}],
        }, f, ensure_ascii=False)
    result = migrate_existing(src, tmp_path / 'master_pool.db', tmp_path / 'archive')
    assert result['count'] == 1
    s = SqlStorage(tmp_path / 'master_pool.db')
    cm = ConditionsManager(storage=s)
    rec = cm.load_conditions('科创新源')
    assert rec is not None
    assert 'trailing_stop' in rec.conditions  # 合法条件被保留
    assert rec.conditions['trailing_stop'].id == 'abc12345'
