"""CLI 冒烟：master-pool/watchlist 命令可用"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from typer.testing import CliRunner
import pytest

runner = CliRunner()


def test_cli_master_pool_init_show(ws):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["master-pool-init", "--amount", "10000000"])
    assert r.exit_code == 0
    assert "总池初始化成功" in r.output
    r = runner.invoke(app, ["master-pool-show"])
    assert r.exit_code == 0
    assert "10,000,000" in r.output


def test_cli_watchlist_add_remove(ws):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["watchlist-add", "赛力斯", "--code", "sh603527",
                            "--strategy", "L1", "--source", "manual", "--reason", "锁"])
    assert r.exit_code == 0
    assert "入池" in r.output
    r = runner.invoke(app, ["watchlist-list"])
    assert r.exit_code == 0
    assert "赛力斯" in r.output
    # L1 不能被 agent 移除
    r = runner.invoke(app, ["watchlist-remove", "赛力斯", "--source", "agent"])
    assert r.exit_code == 1
    assert "L1" in r.output


def test_cli_allocate_topup_release(ws):
    from paper_trading_v2.cli import app
    runner.invoke(app, ["master-pool-init", "--amount", "10000000"])
    runner.invoke(app, ["watchlist-add", "英维克", "--code", "sz000301",
                        "--strategy", "L2", "--source", "agent"])
    r = runner.invoke(app, ["master-pool-allocate", "英维克", "--amount", "500000", "--reason", "建仓"])
    assert r.exit_code == 0
    assert "分配" in r.output
    r = runner.invoke(app, ["master-pool-topup", "英维克", "--amount", "300000", "--reason", "补"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["master-pool-records"])
    assert r.exit_code == 0
    assert "allocate" in r.output and "topup" in r.output
    r = runner.invoke(app, ["master-pool-release", "英维克", "--reason", "清仓"])
    assert r.exit_code == 0
    assert "释放" in r.output
    # 冷却期内禁止重新 allocate
    r = runner.invoke(app, ["master-pool-allocate", "英维克", "--amount", "100000", "--reason", "想追回"])
    assert r.exit_code == 1
    assert "冷却" in r.output


def test_cli_init_before_show_errors(ws):
    from paper_trading_v2.cli import app
    r = runner.invoke(app, ["master-pool-show"])
    assert r.exit_code == 1
    assert "未初始化" in r.output


def test_cli_records_after_migrate_no_crash(ws):
    """migrate 后 master-pool-records 不崩（NULL free 字段）"""
    import json
    from paper_trading_v2.cli import app
    # 造一个假 JSON 账户
    src = ws / 'tradings'
    src.mkdir(parents=True, exist_ok=True)
    d = src / '赛力斯'
    d.mkdir(parents=True, exist_ok=True)
    with open(d / 'account.json', 'w', encoding='utf-8') as f:
        json.dump({'stock_name': '赛力斯', 'stock_code': 'sh603527',
                   'capital_pool': {'total': 500000, 'available': 500000, 'used': 0},
                   'positions': [], 'fifo_index': -1, 'fifo_offset': 0.0,
                   'exright_applied': [], 'created_at': '2026-01-01T09:00:00',
                   'updated_at': '2026-01-01T09:00:00'}, f, ensure_ascii=False)
    with open(d / 'operations.json', 'w', encoding='utf-8') as f:
        json.dump({'stock_name': '赛力斯', 'operations': [
            {'type': 'init', 'capital': 500000, 'timestamp': '2026-01-01T09:00:00'}]},
            f, ensure_ascii=False)
    # v9：v1 JSON 导入器作废（段即账户，一股一户导入语义无处安放）→ 显式拒绝不崩
    r = runner.invoke(app, ["migrate-existing", "--source", str(src),
                            "--archive", str(ws / 'archive')])
    assert r.exit_code == 1
    assert "v1 JSON 导入器" in r.output
    # master-pool-records 在空 audit 上照常可用（原测试的保护目标）
    r = runner.invoke(app, ["master-pool-records"])
    assert r.exit_code == 0


def test_cli_init_force_blocked_on_open_segment(ws):
    """有 open 段的股票禁止 init --force（防幻影资金）"""
    from paper_trading_v2.cli import app
    runner.invoke(app, ["master-pool-init", "--amount", "10000000"])
    runner.invoke(app, ["watchlist-add", "英维克", "--code", "sz000301",
                        "--strategy", "L2", "--source", "agent"])
    runner.invoke(app, ["master-pool-allocate", "英维克", "--amount", "500000", "--reason", "建仓"])
    r = runner.invoke(app, ["init", "英维克", "--capital", "9999999", "--force"])
    assert r.exit_code == 1
    assert "open 段" in r.output
