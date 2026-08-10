"""脚手架冒烟测试：确保 v2 包可导入、config 正确"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_version():
    from paper_trading_v2 import __version__
    assert __version__ == "2.0.0"

def test_config_db_path(ws):
    from paper_trading_v2.config import get_workspace_config
    cfg = get_workspace_config()
    assert str(cfg['db_path']) == str(ws / 'master_pool.db')
    assert str(cfg['tradings_dir']) == str(ws / 'tradings_v2')

import importlib
import pkgutil
import paper_trading_v2

def test_all_v2_modules_import():
    """回归网：vendor 复制漏改 import 会在此失败"""
    mods = [m.name for m in pkgutil.iter_modules(paper_trading_v2.__path__)]
    assert mods, "paper_trading_v2 不应为空"
    for name in mods:
        importlib.import_module(f'paper_trading_v2.{name}')
