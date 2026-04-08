"""配置管理

集中管理所有路径配置，提供统一的路径管理接口
"""

from pathlib import Path



def get_workspace_config() -> dict:
    """
    获取工作空间配置

    只支持 STOCK_ANALYSIS_WORKSPACE 环境变量来覆盖工作空间根目录
    不再支持 STOCK_INTERMEDIATE_DIR 环境变量

    Returns:
        dict: 包含以下键的字典:
            - workspace_root: 工作空间根目录 (Path)
            - tradings_dir: 交易数据目录 (Path) = workspace_root/tradings
            - stocks_analysis_dir: 股票分析目录 (Path) = workspace_root/stocks_analysis
    """
    import os

    # 获取脚本所在目录的父目录（SKILL_ROOT）
    SKILL_ROOT = Path(__file__).parent.parent

    # 工作空间根目录（优先使用环境变量）
    workspace_root = Path(os.getenv('STOCK_ANALYSIS_WORKSPACE', SKILL_ROOT))

    # 交易数据目录
    tradings_dir = workspace_root / "tradings"

    # 股票分析目录（直接在工作空间根目录下）
    stocks_analysis_dir = workspace_root / "stocks_analysis"

    return {
        'workspace_root': workspace_root,
        'tradings_dir': tradings_dir,
        'stocks_analysis_dir': stocks_analysis_dir,
    }


def get_trading_account_dir(stock_name: str) -> Path:
    """
    获取交易账户目录路径

    Args:
        stock_name: 股票名称（使用原始名称，不做清理）

    Returns:
        Path: 交易账户目录路径，格式为 tradings_dir/stock_name
    """
    config = get_workspace_config()
    return config['tradings_dir'] / stock_name


def get_stock_analysis_dir(stock_name: str) -> Path:
    """
    获取股票分析目录路径

    Args:
        stock_name: 股票名称（使用原始名称，不做清理）

    Returns:
        Path: 股票分析目录路径，格式为 stocks_analysis_dir/stock_name
    """
    config = get_workspace_config()
    return config['stocks_analysis_dir'] / stock_name
