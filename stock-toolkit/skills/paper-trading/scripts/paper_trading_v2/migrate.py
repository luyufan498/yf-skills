"""迁移脚本：旧 JSON 账户 → SQLite（归档为 closed 段，operations/conditions 完整保留）"""
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from paper_trading_v2.db import get_connection
from paper_trading_v2.storage import SqlStorage


def migrate_existing(source_tradings_dir: Path, db_path: Path, archive_dir: Path) -> dict:
    """
    遍历旧 JSON 账户目录，迁移入 SQLite。
    - account/positions/exright → accounts 表（SqlStorage）
    - operations → operations 表
    - conditions.json → conditions 表（ConditionsManager）
    - 每个账户落一条 closed 段（预算=total，现值=available，realized=available-total）
    - 物理目录移入 archive_dir（只读归档）
    """
    from paper_trading_v2.models import (
        Account, AccountHistory, CapitalPool, Operation, Position, ExRightAppliedRecord,
    )
    from paper_trading_v2.conditions_manager import ConditionsManager
    from paper_trading_v2.conditions import ConditionsRecord

    s = SqlStorage(db_path)
    cm = ConditionsManager(storage=s)
    conn = get_connection(db_path)
    migrated = []
    now = datetime.now().isoformat()
    try:
        for stock_dir in sorted(source_tradings_dir.iterdir()):
            if not stock_dir.is_dir():
                continue
            account_file = stock_dir / 'account.json'
            if not account_file.exists():
                continue
            with open(account_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cp = data.get('capital_pool', {})
            account = Account(
                stock_name=data['stock_name'],
                stock_code=data.get('stock_code'),
                capital_pool=CapitalPool(
                    total=cp.get('total', 0),
                    available=cp.get('available', 0),
                    used=cp.get('used', 0),
                ),
                fifo_index=data.get('fifo_index', -1),
                fifo_offset=data.get('fifo_offset', 0.0),
                created_at=data.get('created_at', now),
                updated_at=data.get('updated_at', now),
            )
            for pos_data in data.get('positions', []):
                account.positions.append(Position(
                    stock_code=pos_data.get('stock_code', data.get('stock_code')),
                    quantity=pos_data.get('quantity', 0),
                    price=pos_data.get('price', 0.0),
                    total_cost=pos_data.get('total_cost', 0.0),
                    operation=pos_data.get('operation', 'buy'),
                    timestamp=pos_data.get('timestamp', ''),
                    note=pos_data.get('note', ''),
                ))
            for ex_data in data.get('exright_applied', []):
                account.exright_applied.append(ExRightAppliedRecord(
                    cqr=ex_data.get('cqr', ''), fhcontent=ex_data.get('fhcontent', ''),
                    applied_at=ex_data.get('applied_at', ''),
                    reason=ex_data.get('reason', ''),
                    migrated=True,
                ))
            s.save_account(account)
            # operations
            ops_file = stock_dir / 'operations.json'
            if ops_file.exists():
                with open(ops_file, 'r', encoding='utf-8') as f:
                    ops_data = json.load(f)
                ops = [Operation(
                    type=o.get('type', ''), price=o.get('price'),
                    quantity=o.get('quantity'), amount=o.get('amount'),
                    cost=o.get('cost'), profit=o.get('profit'),
                    capital=o.get('capital'), timestamp=o.get('timestamp', ''),
                    note=o.get('note', ''),
                ) for o in ops_data.get('operations', [])]
                s.save_operations(data['stock_name'],
                                  AccountHistory(stock_name=data['stock_name'], operations=ops))
            # conditions.json → SQLite conditions 表
            cond_file = stock_dir / 'conditions.json'
            if cond_file.exists():
                try:
                    with open(cond_file, 'r', encoding='utf-8') as f:
                        cond_data = json.load(f)
                    record = ConditionsRecord.model_validate(cond_data)
                    cm.save_conditions(record)
                except Exception as e:
                    print(f"⚠ 迁移条件失败 {stock_dir.name}: {e}")
            # 落一条 closed 段（历史记录，不占预算）。
            # 注意：直接写主 conn 必须用 `with conn:` 立即提交——save_account 等走独立连接，
            # 若本连接挂未提交写事务会持 RESERVED 锁，迁第二个账户时触发 database is locked。
            with conn:
                conn.execute(
                    "INSERT INTO position (stock, code, strategy, status, budget, topup_total, "
                    "opened_at, closed_at, close_value, realized_pnl, cooldown_until) "
                    "VALUES (?,?,'L2','closed',?,0,?,?,?,?,'2000-01-01T00:00:00')",
                    (account.stock_name, account.stock_code, account.capital_pool.total,
                     account.created_at, now, account.capital_pool.available,
                     account.capital_pool.available - account.capital_pool.total))
            # 移入归档
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / stock_dir.name
            shutil.move(str(stock_dir), str(dest))
            migrated.append(stock_dir.name)
        # migration 审计
        if migrated:
            with conn:
                conn.execute("INSERT INTO audit (timestamp, action, stock, amount, free_before, "
                             "free_after, reason, source) VALUES (?,?,?,?,?,?,?,?)",
                             (now, 'migrate', None, None, None, None, '历史 JSON 迁移入库', 'manual'))
    finally:
        conn.close()
    return {"migrated": migrated, "count": len(migrated)}
