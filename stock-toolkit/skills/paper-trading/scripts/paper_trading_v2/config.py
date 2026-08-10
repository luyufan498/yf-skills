"""配置管理 — v2：SQLite 单一事实源，交易数据目录 tradings_v2"""
import os
from pathlib import Path

DEFAULT_WORKSPACE = Path(os.path.expanduser('~/.paper-trading-v2'))

def get_workspace_config() -> dict:
    workspace_root = Path(os.getenv('STOCK_ANALYSIS_WORKSPACE', str(DEFAULT_WORKSPACE)))
    return {
        'workspace_root': workspace_root,
        'tradings_dir': workspace_root / 'tradings_v2',
        'stocks_analysis_dir': workspace_root / 'stocks_analysis',
        'temp_data_dir': workspace_root / 'temp-data',
        'db_path': workspace_root / 'master_pool.db',
    }

def get_trading_account_dir(stock_name: str) -> Path:
    return get_workspace_config()['tradings_dir'] / stock_name

def get_stock_temp_data_dir(stock_name: str, category: str = None) -> Path:
    """临时数据目录: temp_data_dir/stock_name[/category]"""
    base_dir = get_workspace_config()['temp_data_dir'] / stock_name
    if category:
        return base_dir / category
    return base_dir
