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
